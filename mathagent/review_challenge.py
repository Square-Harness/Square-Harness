# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

"""An independent manuscript reader that sends evidence while the main referee is working."""
import json
import threading
import time

from .agent import Agent
from .assistant_budget import BudgetClient
from .peer_transport import fork_cancellable
from .referee import MeteredClient
from .research import ResearchBudget
from .tools import schema


CHALLENGE_PROMPT = """Independently examine steps that matter to the author's mathematical conclusions.
Choose them from the manuscript, within the requested scope. Start with the implication whose failure would
most affect a principal result. Read its exact hypotheses, argument and dependencies. Try to justify it as
written or construct a counterexample satisfying its hypotheses. Distinguish a false statement from a gap in
its proof. A failed proof attempt alone is insufficient evidence.
Send each supported objection immediately with submit_challenge. Give the source lines, the mathematical
reason and the conclusions affected. Combine consequences of the same underlying defect. Then examine the
next distinct consequential step. If a step checks out, move on. Finish when you see no further consequential
question worth pursuing. The main referee writes the report.
If concrete evidence already indicates a serious objection and a specific outstanding check could change the
verdict, flag_challenge records the evidence and that check. Use the returned check_id to update the flag,
submit the confirmed objection, or withdraw_challenge if the concern is resolved. This lets the main referee
take over an open question when it is ready to finish. All these handoffs are private."""


class ChallengeCancellation(threading.Event):
    """The peer can be canceled independently or by pausing the enclosing review."""

    def __init__(self, parent):
        super().__init__()
        self.parent = parent

    def is_set(self):
        return super().is_set() or bool(self.parent and self.parent.is_set())

    def wait(self, timeout=None):
        if self.parent is None:
            return super().wait(timeout)
        end = None if timeout is None else time.monotonic() + timeout
        while not self.is_set():
            remaining = None if end is None else end - time.monotonic()
            if remaining is not None and remaining <= 0:
                break
            super().wait(0.1 if remaining is None else min(remaining, 0.1))
        return self.is_set()


class ChallengeTools:
    """Read only the selected manuscript and private computations, independently of the lead's work."""
    allowed = {'list_files', 'search_text', 'read_file', 'execute_python', 'read_experiment', 'read_skill_reference'}

    def __init__(self, workspace, challenge):
        self.workspace, self.challenge = workspace, challenge
        self.root = workspace.root
        self.ranges = {}
        workspace.mark_read = self.mark_read

    def mark_read(self, source_id, ranges):
        self.ranges.setdefault(source_id, []).extend(ranges)

    def schemas(self):
        tools = [s for s in self.workspace.schemas() if s['function']['name'] in self.allowed]
        text, integer = {'type': 'string'}, {'type': 'integer'}
        fields = dict(path=text, start_line=integer, end_line=integer, objection=text, evidence=text)
        with_id = {**fields, 'check_id': text}
        return tools + [
            schema('flag_challenge', 'Save a consequential open check backed by evidence. Use its check_id to update it.',
                   {**with_id, 'pending_check': text}, [*fields, 'pending_check']),
            schema('submit_challenge', 'Immediately hand a supported objection to the main referee. Include its '
                   'check_id if previously flagged. You can continue examining other steps.', with_id, list(fields)),
            schema('withdraw_challenge', 'Close a previously flagged check and record how the concern was resolved.',
                   {'check_id': text, 'reason': text}, ['check_id', 'reason'])]

    def execute(self, name, arguments):
        if self.challenge.cancel.is_set():
            raise KeyboardInterrupt
        if name in self.allowed:
            if name == 'read_experiment':
                try:
                    args = json.loads(arguments) if isinstance(arguments, str) else arguments
                    if args['experiment_id'] not in self.challenge.record['experiments']:
                        return 'Tool error: only this reader’s own experiments are available.'
                except (TypeError, KeyError, ValueError):
                    return 'Tool error: provide this reader’s experiment_id.'
            return self.workspace.execute(name, arguments)
        if name not in ('flag_challenge', 'submit_challenge', 'withdraw_challenge'):
            return 'Tool error: unavailable to this reader.'
        try:
            args = json.loads(arguments) if isinstance(arguments, str) else arguments
            if name == 'withdraw_challenge':
                reason = args['reason']
                if not isinstance(reason, str) or not reason.strip():
                    raise ValueError('Explain how the flagged concern was resolved.')
                return self.challenge.withdraw(args['check_id'], reason.strip())
            path = self.workspace.path(args['path'])
            lines = self.workspace.documents[path]['content'].splitlines()
            first, last = args['start_line'], args['end_line']
            if type(first) is not int or type(last) is not int or not 1 <= first <= last <= len(lines):
                raise ValueError('Give a valid source line range.')
            doc = self.workspace.documents[path]
            covered = first - 1
            for start, end in sorted(self.ranges.get(doc['id'], [])):
                if start <= covered + 1:
                    covered = max(covered, end)
            if covered < last:
                raise ValueError('Read the cited lines before handing off an objection.')
            note = dict(path=doc['path'], start_line=first, end_line=last)
            keys = ('objection', 'evidence', 'pending_check') if name == 'flag_challenge' else ('objection', 'evidence')
            for key in keys:
                value = args[key]
                if not isinstance(value, str) or not value.strip():
                    raise ValueError(f'Give a nonempty {key}.')
                note[key] = value.strip()
            return self.challenge.receive(note, args.get('check_id'))
        except (TypeError, KeyError, ValueError) as exc:
            return 'Tool error: ' + str(exc)


def _spare_inference(client):
    transport = client.client if isinstance(client, BudgetClient) else client
    room = getattr(getattr(transport, 'pool', None), 'room_for_peer', None)
    return room is None or room(transport.owner)


class _ChallengeClient(MeteredClient):
    def stream(self, payload):
        if self.client.cancel.is_set():
            raise KeyboardInterrupt
        yield from super().stream(payload)


class Challenge:
    def __init__(self, runner):
        self.runner = runner
        self.cancel = ChallengeCancellation(getattr(runner, 'cancel', None))
        self.thread = None
        self.record = runner.state.setdefault('challenge', {'status': 'new'})
        # Saved v2 reviews from the first implementation stored only one submitted or flagged observation.
        if 'checks' not in self.record:
            checks = []
            if self.record.get('finding'):
                checks.append({'status': 'submitted', 'note': self.record['finding']})
            elif self.record.get('flag') and self.record['status'] != 'no_finding':
                checks.append({'status': 'open', 'note': self.record['flag']})
            self.record['checks'] = checks

    def start(self, make_workspace):
        r = self.runner
        # Preserve existing evidence after a crash. Replaying a possibly dispatched model call could bill it twice.
        if self.record['status'] != 'new':
            if self.record['status'] in ('running', 'verifying'):
                self.record['status'] = 'interrupted'
                r._save()
            return
        if r.concurrency < 2:
            reason = 'one review inference slot'
        elif not _spare_inference(r.agent.client):
            reason = 'available model capacity reserved for main referee'
        elif (r.state.get('review_reference') or {}).get('followup'):
            reason = 'follow-up to a saved review'
        else:
            reason = ''
        peer = None if reason else fork_cancellable(r.agent.client, self.cancel)
        if peer is None:
            self.record.update(status='skipped', reason=reason or 'transport has no independently cancelable stream')
            r._save()
            return
        self.record.update(status='running', experiments=[])
        r._save()
        self.thread = threading.Thread(target=self._run, args=(peer, make_workspace), name='review-challenger')
        self.thread.start()

    def _run(self, peer, make_workspace):
        r = self.runner
        status = 'finished'
        try:
            client = _ChallengeClient(r, 'challenge', 'challenger')
            client.client = peer
            workspace = make_workspace(self.cancel, self.record)
            tools = ChallengeTools(workspace, self)
            agent = Agent(client, tools, r.agent.model, r.agent.ctx, r.agent.predict, r.state['thinking'], 'explore',
                          r.state['tool_rounds'], seed=r.agent.seed, temperature=r.state['temperature'],
                          top_p=r.state['top_p'])
            agent.stop_on_stalled_tools = False
            agent.project_context = r.state.get('project_context', '')
            agent.turn_image_refs = r.state.get('images', [])
            scope = r.state['goal'] + ('\nRequested focus: ' + r.state['target'] if r.state.get('target') else '')
            files = ', '.join(s['path'] for s in r.state['sources'])
            agent.run(scope + '\n\nPrimary manuscript: ' + r.state['sources'][0]['path'] + '\nFiles: ' + files,
                      instruction=CHALLENGE_PROMPT, background_tools=False)
            if agent.last_stop_reason:
                status = 'stopped'
                with r._mutex:
                    self.record['reason'] = agent.last_stop_reason
        except (KeyboardInterrupt, ResearchBudget):
            status = 'stopped'
        except Exception as exc:
            status = 'stopped' if self.cancel.is_set() else 'unavailable'
            if status == 'unavailable':
                with r._mutex:
                    self.record['reason'] = type(exc).__name__
        finally:
            with r._mutex:
                self.record['status'] = status
                r._save()

    def _open_check(self, check_id):
        for index, check in enumerate(self.record['checks'], 1):
            if check_id == f'C{index}' and check['status'] == 'open':
                return check
        raise ValueError('Use the check_id of an open flagged concern.')

    def receive(self, note, check_id=None):
        with self.runner._mutex:
            if self.cancel.is_set():
                return 'The main referee has closed the handoff.'
            status = 'open' if 'pending_check' in note else 'submitted'
            checks = self.record['checks']
            if check_id is None:
                for index, check in enumerate(checks, 1):
                    if check['status'] == status and check['note'] == note:
                        return f'Already recorded as C{index}.'
                check = {}
                checks.append(check)
                check_id = f'C{len(checks)}'
            else:
                check = self._open_check(check_id)
            check.update(status=status, note=note)
            self.runner._save()
            return f'Recorded as {check_id}.'

    def withdraw(self, check_id, reason):
        with self.runner._mutex:
            if self.cancel.is_set():
                return 'The main referee has closed the handoff.'
            self._open_check(check_id).update(status='withdrawn', reason=reason)
            self.runner._save()
            return f'Closed {check_id}.'

    def snapshot(self, final):
        with self.runner._mutex:
            if final:
                # The referee is ready to report. Close intake and take its evidence in the same critical section.
                self.cancel.set()
            notes = []
            for index, check in enumerate(self.record['checks'], 1):
                if check['status'] == 'submitted':
                    label = 'Supported objection. Verify against the manuscript before using it.'
                elif check['status'] == 'open' and self.cancel.is_set():
                    label = 'Unresolved challenger observation. Verify the outstanding check or disclose it as open.'
                else:
                    continue
                notes.append(f'C{index}. {label}\n' + json.dumps(check['note'], ensure_ascii=False))
            if not notes:
                return ''
            return 'Private evidence from the independent challenger:\n' + '\n'.join(notes)

    def close(self):
        self.cancel.set()
        if self.thread is not None:
            # Settle usage before releasing the saved job's lock.
            self.thread.join()
