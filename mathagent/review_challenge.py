# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

"""One independent, time-bounded mathematical challenge to a pinned manuscript."""
import json
import threading
import time

from .agent import Agent
from .assistant_budget import BudgetClient
from .peer_transport import fork_cancellable
from .referee import MeteredClient
from .research import ResearchBudget
from .tools import schema

CHALLENGE_SECONDS = 180
VERIFICATION_GRACE = 15
CHALLENGE_TOKENS = 16_384
CHALLENGE_ROUNDS = 6

CHALLENGE_PROMPT = """Independently select one consequential implication in the author's manuscript that a
careful reader might accept too quickly. Choose from the manuscript itself, respecting the author's requested
scope. Read the exact statement, hypotheses, argument and relevant dependencies. Try to derive the step as
written or find a counterexample satisfying its hypotheses. A failure to find a proof is insufficient evidence.
Use submit_challenge only for one located objection with checkable evidence. Distinguish a false statement
from a failure of the proof, and state which later conclusion is affected. If a specific serious objection is
already supported by evidence and one outstanding check will decide it, use flag_challenge with that evidence
and name the check before doing it. These tools are private handoffs to the main referee. If you have no
supported objection, finish with no finding. Your assignment is one challenge; the main referee writes the report."""


class ChallengeCancellation(threading.Event):
    def __init__(self, parent, until):
        super().__init__()
        self.parent, self.until = parent, until

    def is_set(self):
        return super().is_set() or bool(self.parent and self.parent.is_set()) or time.monotonic() >= self.until

    def wait(self, timeout=None):
        end = self.until if timeout is None else min(self.until, time.monotonic() + timeout)
        while not self.is_set() and time.monotonic() < end:
            super().wait(min(0.1, max(0, end - time.monotonic())))
        return self.is_set()


class _Submitted(Exception):
    """The supported objection has reached the lead; no wrap-up generation is needed."""


class ChallengeTools:
    """Private read-only selection. This reader cannot access drafts, notes or lead experiment IDs."""
    allowed = {'list_files', 'search_text', 'read_file', 'execute_python'}

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
        return tools + [
            schema('flag_challenge', 'Register one supported, consequential objection whose named outstanding check '
                   'could change the verdict.', {**fields, 'pending_check': text}, [*fields, 'pending_check']),
            schema('submit_challenge', 'Hand one supported objection to the main referee for independent verification.',
                   fields, list(fields))]

    def execute(self, name, arguments):
        if self.challenge.cancel.is_set():
            raise KeyboardInterrupt
        if name in self.allowed:
            return self.workspace.execute(name, arguments)
        if name not in ('flag_challenge', 'submit_challenge'):
            return 'Tool error: unavailable to this reader.'
        try:
            args = json.loads(arguments) if isinstance(arguments, str) else arguments
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
                if not isinstance(value, str) or not value.strip() or len(value) > 3000:
                    raise ValueError(f'Give a nonempty {key} of at most 3000 characters.')
                note[key] = value.strip()
            return self.challenge.receive(note, final=name == 'submit_challenge')
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
        if not _spare_inference(self.client):
            raise ResearchBudget('Model capacity reserved for the main referee.')
        yield from super().stream(payload)

    @property
    def remaining_tokens(self):
        r = self.runner
        with r._mutex:
            used = sum(c.get('stats', {}).get('eval_count', c['reserved_tokens']) for c in r.state['calls']
                       if c['role'] == 'challenge')
            return min(super().remaining_tokens, r.state['challenge']['output_allowance'] - used)


class Challenge:
    def __init__(self, runner):
        self.runner = runner
        seconds = min(CHALLENGE_SECONDS, max(0, runner.state['settings']['max_seconds'] - runner._elapsed()))
        self.cancel = ChallengeCancellation(getattr(runner, 'cancel', None), time.monotonic() + seconds)
        self.done = threading.Event()
        self.thread = None
        self.delivered = False
        self.record = runner.state.setdefault('challenge', {'status': 'new'})

    def start(self, make_workspace):
        r = self.runner
        # Do not replay a possibly dispatched challenge after a crash. Any recorded objection is still available.
        if self.record['status'] != 'new':
            if self.record['status'] in ('running', 'verifying'):
                self.record['status'] = 'interrupted'
                r._save()
            self.done.set()
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
            self.done.set()
            r._save()
            return
        available = min(r._tokens_left(), getattr(peer, 'remaining_tokens', float('inf')))
        usable = int(max(0, min(CHALLENGE_TOKENS, available * 0.15, available - r.agent.predict)))
        if usable < 256:
            self.record.update(status='skipped', reason='output allowance reserved for main referee')
            self.done.set()
            r._save()
            return
        self.record.update(status='running', experiments=[], output_allowance=usable)
        r._save()
        self.thread = threading.Thread(target=self._run, args=(peer, make_workspace), name='review-challenger')
        self.thread.start()

    def _run(self, peer, make_workspace):
        r = self.runner
        status = 'interrupted'
        try:
            peer.timeout = min(peer.timeout, max(0.1, self.cancel.until - time.monotonic()))
            client = _ChallengeClient(r, 'challenge', 'challenger')
            client.client = peer
            workspace = make_workspace(self.cancel, self.record)
            tools = ChallengeTools(workspace, self)
            budget = min(r.agent.predict, CHALLENGE_TOKENS)
            agent = Agent(client, tools, r.agent.model, r.agent.ctx, budget, r.state['thinking'], 'explore',
                          min(r.state['tool_rounds'], CHALLENGE_ROUNDS), seed=r.agent.seed,
                          temperature=r.state['temperature'], top_p=r.state['top_p'])
            agent.turn_image_refs = r.state.get('images', [])
            scope = r.state['goal'] + ('\nRequested focus: ' + r.state['target'] if r.state.get('target') else '')
            files = ', '.join(s['path'] for s in r.state['sources'])
            agent.run(scope + '\n\nPrimary manuscript: ' + r.state['sources'][0]['path'] + '\nFiles: ' + files,
                      instruction=CHALLENGE_PROMPT,
                      background_tools=False)
            status = 'no_finding'
        except _Submitted:
            status = 'submitted'
        except (KeyboardInterrupt, ResearchBudget):
            status = 'stopped'
        except Exception as exc:
            status = 'unavailable'
            with r._mutex:
                self.record['reason'] = type(exc).__name__
        finally:
            with r._mutex:
                if self.record['status'] != 'submitted':
                    self.record['status'] = status
                r._save()
            self.done.set()

    def receive(self, note, *, final):
        with self.runner._mutex:
            if self.cancel.is_set() or self.record['status'] not in ('running', 'verifying'):
                return 'The handoff is closed.'
            if not final and 'flag' in self.record:
                return 'One potential objection has already been flagged.'
            self.record['finding' if final else 'flag'] = note
            self.record['status'] = 'submitted' if final else 'verifying'
            self.runner._save()
            if final:
                self.cancel.set()
                self.done.set()
                raise _Submitted
            return 'Recorded.'

    def handoff(self, final):
        if final:
            with self.runner._mutex:
                flagged = self.record['status'] == 'verifying'
                if not flagged:
                    self.cancel.set()
            if flagged:
                end = min(self.cancel.until, time.monotonic() + VERIFICATION_GRACE)
                while not self.cancel.is_set() and time.monotonic() < end:
                    if self.done.wait(min(0.10, max(0, end - time.monotonic()))):
                        break
                self.cancel.set()
        with self.runner._mutex:
            finding = self.record.get('finding')
            unresolved = final and self.record['status'] != 'no_finding' and self.record.get('flag')
            if self.delivered or not (finding or unresolved):
                return ''
            self.delivered = True
            label = ('Independent challenger objection; verify it against the manuscript before using it.' if finding
                     else 'Unresolved challenger observation; its decisive check is incomplete. Verify independently '
                          'or identify the outstanding check as unresolved.')
            return label + '\n' + json.dumps(finding or self.record['flag'], ensure_ascii=False)

    def close(self):
        self.cancel.set()
        if self.thread is not None:
            # Wait only for cancellation/usage settlement. Never release the saved job's lock with a metered writer.
            self.thread.join()
