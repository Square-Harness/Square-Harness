# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

"""Allowlisted per-worker display records. Private prompts, calls and child context never enter the view."""

import json

FAILED = {'failed', 'error', 'budget_exhausted', 'budget_violation', 'context_overflow',
          'needs_recovery', 'needs_context', 'blocked', 'incomplete'}
STOPPED = {'stopped', 'cancelled', 'paused', 'interrupted', 'skipped', 'dismissed', 'set_aside'}


def _short(value, limit):
    return value[:limit] if isinstance(value, str) else ''


def _error(value):
    if not isinstance(value, dict) or not isinstance(value.get('code'), str):
        return None
    return {key: value[key] for key in ('code', 'scope', 'retryable', 'message') if key in value
            and isinstance(value[key], (str, bool))}


def _progress(value):
    fields = ('phase', 'completed', 'total', 'unit', 'scope', 'partial', 'resumable')
    if isinstance(value, dict) and isinstance(value.get('phase'), str):
        return {key: value[key] for key in fields if key in value}
    return None


def _status(card, child, parent_status, error, native):
    state = card.get('status')
    child_state = child.get('status')
    if not child and card.get('agent_status'):
        return card['agent_status']
    if state in ('stopped', 'cancelled') or child_state in STOPPED:
        return 'stopped'
    if error or state in FAILED or native in FAILED or child_state in FAILED:
        return 'failed'
    if child_state == 'complete' or state == 'done':
        return 'finished'
    if parent_status in STOPPED or parent_status in ('complete', 'failed'):
        return 'stopped'
    if state in ('started', 'starting') or child_state == 'running':
        return 'running'
    return 'waiting'


def _usage(child, card):
    raw = child.get('usage') or {}
    return {**(card.get('agent_usage') or {}),
            **{dest: raw[src] for src, dest in (('tokens', 'output_tokens'), ('input_tokens', 'input_tokens'),
                                                ('seconds', 'seconds'), ('requests', 'requests'))
               if type(raw.get(src)) in (int, float)}}


def _lane(child, card, index, turn, batch, parent_status=None):
    action = child.get('action') or {}
    result = child.get('result') or {}
    error = _error(result.get('error')) or _error(card.get('agent_error'))
    if not error and card.get('error_code'):
        error = {'code': card['error_code'], 'message': card.get('error') or ''}
    native = result.get('status') or card.get('native_status')
    mode = action.get('mode') or card.get('mode') or 'explore'
    objective = action.get('request') or card.get('request') or ''
    return {'id': child.get('id') or card.get('child_id') or f'card-{index}',
            'turn_id': child.get('turn_id') or card.get('turn_id') or turn,
            'batch_id': child.get('batch_id') or card.get('batch_id') or batch,
            'card_index': index, 'mode': mode, 'objective': objective,
            'effort': action.get('effort') or card.get('effort') or '',
            'status': _status(card, child, parent_status, error, native),
            'job_id': card.get('job_id') or child.get('job_id') or card.get('answer_id'),
            'answer_id': card.get('answer_id'), 'started_at': card.get('started_at') or card.get('time'),
            'ended_at': card.get('ended_at'), 'progress': _progress(child.get('progress') or card.get('agent_progress')),
            'allowance': child.get('allocation', card.get('agent_allowance')), 'usage': _usage(child, card), 'error': error,
            'outcome': _short(card.get('outcome') or '', 1000),
            'timeline': child.get('agent_timeline') or card.get('agent_timeline') or []}


def _legacy_batches(items, start, stop):
    """Match old delegate tool calls back to route cards within this turn, preserving duplicate requests."""
    available = [i for i in range(start, stop) if items[i].get('role') == 'route' and items[i].get('orchestrated')]
    result = {}
    for index in range(start, stop):
        message = items[index]
        if message.get('role') != 'assistant':
            continue
        group = None
        for slot, call in enumerate(message.get('tool_calls') or []):
            fn = call.get('function') or {}
            if fn.get('name') != 'delegate':
                group = None
                continue
            if group is None:
                group = f'legacy-{start}-batch-{index}-{slot}'
            args = fn.get('arguments') or {}
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except ValueError:
                    continue
            if not isinstance(args, dict):
                continue
            match = next((i for i in available if items[i].get('mode') == args.get('mode')
                          and items[i].get('request') == args.get('request')), None)
            if match is not None:
                available.remove(match)
                result[match] = group
    return result


def _legacy_receipts(items, start, stop):
    """Identify old delegate receipts without exposing tool payloads or guessing between same-mode workers."""
    receipts = []
    for item in items[start:stop]:
        if item.get('role') == 'tool' and item.get('tool_name') == 'delegate':
            try:
                value = json.loads(item.get('content') or '')
            except (ValueError, TypeError):
                continue
            if isinstance(value, dict) and isinstance(value.get('result'), dict):
                receipts.append(value)
    cards = [i for i in range(start, stop) if items[i].get('role') == 'route' and items[i].get('orchestrated')]
    result = {}
    for i in cards:
        card = items[i]
        job = card.get('job_id') or card.get('answer_id')
        matches = [r for r in receipts if job and r.get('job_id') == job and r.get('mode') == card.get('mode')]
        if not matches and not job and sum(items[j].get('mode') == card.get('mode') for j in cards) == 1:
            matches = [r for r in receipts if r.get('mode') == card.get('mode')]
        if len(matches) == 1:
            saved = matches[0]['result']
            result[i] = {'native_status': saved.get('status'), 'agent_error': _error(saved.get('error')),
                         'agent_progress': _progress(saved.get('progress'))}
    return result


def agents(chat, *, current_only=False):
    state = chat.get('assistant_state') or {}
    items = chat.get('transcript') or []
    children = state.get('children') or []
    active_turn = state.get('id')
    indexed = {child['card_index']: child for child in children if type(child.get('card_index')) is int}
    lanes, seen = [], set()
    if not current_only:
        lanes.extend(chat.get('archived_agent_lanes') or [])
        bounds = [i for i, item in enumerate(items) if item.get('role') == 'user']
        if not bounds or bounds[0] != 0:
            bounds.insert(0, 0)
        for start, stop in zip(bounds, [*bounds[1:], len(items)]):
            old_cards = any(item.get('role') == 'route' and item.get('orchestrated') and not item.get('batch_id')
                            for item in items[start:stop])
            legacy = _legacy_batches(items, start, stop) if old_cards else {}
            receipts = _legacy_receipts(items, start, stop) if old_cards else {}
            for index in range(start, stop):
                card = items[index]
                if card.get('role') != 'route' or not card.get('orchestrated'):
                    continue
                child = indexed.get(index, {})
                if index in receipts:
                    card = {**receipts[index], **card}
                lane = _lane(child, card, index, active_turn if child else f'legacy-{start}',
                             legacy.get(index, f'legacy-{start}'), state.get('status') if child else 'set_aside')
                if not child and lane['status'] in ('running', 'waiting'):
                    lane['status'] = 'stopped'
                lanes.append(lane)
                seen.add(lane['id'])
    for child in children:
        if child.get('id') in seen:
            continue
        idx = child.get('card_index')
        card = items[idx] if type(idx) is int and 0 <= idx < len(items) and items[idx].get('role') == 'route' else {}
        lane = _lane(child, card, idx if card else None, active_turn, child.get('batch_id') or active_turn, state.get('status'))
        lanes.append(lane)
        seen.add(lane['id'])
    inv = state.get('investigation') or {}
    if inv.get('approved'):
        for task in (inv.get('plan') or {}).get('tasks', []):
            slot = (inv.get('tasks') or {}).get(task['id'], {})
            ident = slot.get('child_id')
            if not ident or ident in seen:
                continue
            lane = _lane({'id': ident, 'status': slot.get('status'), 'action': {**task, 'request': task['objective']},
                          'allocation': (inv.get('allowances', {}).get(task['id']) or {}).get('tokens')},
                         {}, None, active_turn, slot.get('batch_id') or active_turn, state.get('status'))
            lane['outcome'] = slot.get('reason') or ''
            lanes.append(lane)
    return lanes


def archive_turn(chat, next_state):
    previous = chat.get('assistant_state') or {}
    if not previous.get('id') or previous['id'] == next_state.get('id'):
        return
    for lane in agents(chat, current_only=True):
        index = lane['card_index']
        if index is None:
            # Planned or cancelled before acquiring a worker. No card will ever be written for it.
            lane['status'] = 'stopped' if lane['status'] in ('waiting', 'running') else lane['status']
            chat.setdefault('archived_agent_lanes', []).append(lane)
        else:
            chat['transcript'][index].update(child_id=lane['id'], turn_id=lane['turn_id'], batch_id=lane['batch_id'],
                agent_usage=lane['usage'], agent_allowance=lane['allowance'], agent_error=lane['error'],
                agent_progress=lane['progress'], agent_timeline=lane['timeline'], agent_status=lane['status'])
