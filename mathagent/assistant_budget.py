# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

"""Durable inference accounting shared by a concurrent Assistant tree."""
import json
import math
import threading
import time
from contextlib import contextmanager, nullcontext

from .agent import AgentError
from .backends import TokenBudgetError, ToolCallError
from .proof import count_input


class AssistantBudget(AgentError):
    pass


class _Pool:
    def __init__(self, state, checkpoint, concurrency):
        self.state = state
        self.lock = threading.RLock()
        self.slots = threading.BoundedSemaphore(concurrency)
        self.checkpoint = checkpoint
        self.started = time.monotonic()
        self.seconds_before = state['seconds']
        self.excluded_seconds = 0
        self.excluded_since = None
        self.queue_waiters = 0
        self.active_inference = 0

    def admission_change(self, *, queued=0, active=0):
        """Exclude the union of Proof queue waits only while no inference runs."""
        with self.lock:
            now = time.monotonic()
            if self.excluded_since is not None:
                self.excluded_seconds += now - self.excluded_since
            self.queue_waiters += queued
            self.active_inference += active
            self.excluded_since = now if self.queue_waiters and not self.active_inference else None

    def seconds(self):
        now = time.monotonic()
        pending = now - self.excluded_since if self.excluded_since is not None else 0
        return self.seconds_before + now - self.started - self.excluded_seconds - pending


class BudgetClient:
    def __init__(self, client, *, state=None, max_tokens=None, max_input_tokens=None,
                 max_seconds=900, checkpoint=lambda: None, concurrency=2, pool=None,
                 allowance=None, usage=None, adaptive_limits=None, deadline=None):
        self.client = client
        self.deadline = deadline
        self._admitted = threading.local()
        budget = dict(state or {'tokens': 0, 'input_tokens': 0, 'seconds': 0,
                               'max_tokens': max_tokens, 'max_input_tokens': max_input_tokens,
                               'max_seconds': max_seconds})
        if state is None and adaptive_limits:
            budget['adaptive_limits'] = dict(adaptive_limits)
        for key in ('tokens', 'input_tokens', 'seconds', 'max_tokens', 'max_input_tokens', 'max_seconds'):
            value = budget.get(key)
            if value is None and key in ('max_tokens', 'max_input_tokens'):
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                raise ValueError('Invalid Assistant budget: ' + key)
        for key, value in budget.get('adaptive_limits', {}).items():
            if key not in ('max_tokens', 'max_input_tokens', 'max_seconds') or budget[key] is None or isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < budget[key]:
                raise ValueError('Invalid adaptive Assistant ceiling: ' + key)
        reserved = budget.get('reserved_tokens', 0)
        if type(reserved) is not int or not 0 <= reserved <= budget['tokens']:
            raise ValueError('Invalid Assistant budget: reserved_tokens')
        # A restored process cannot still own its old streams. Their charged
        # reservations remain spent, but are no longer displayed as in flight.
        budget['reserved_tokens'] = 0
        self.pool = pool or _Pool(budget, checkpoint, concurrency)
        self.allowance = allowance
        self.usage = usage if usage is not None else {'tokens': 0, 'input_tokens': 0}

    def __getattr__(self, name):
        return getattr(self.client, name)

    @property
    def thinking_budget_policy(self):
        return getattr(self.client, 'thinking_budget_policy', {})

    @thinking_budget_policy.setter
    def thinking_budget_policy(self, value):
        self.client.thinking_budget_policy = value

    @property
    def reasoning_effort(self):
        return self.client.reasoning_effort

    @reasoning_effort.setter
    def reasoning_effort(self, value):
        self.client.reasoning_effort = value

    @property
    def timeout(self):
        return self.client.timeout

    @timeout.setter
    def timeout(self, value):
        self.client.timeout = value

    def fork(self, client, *, allowance, usage, max_seconds=None):
        deadline = None if max_seconds is None else time.monotonic() + max_seconds
        return BudgetClient(client, pool=self.pool, allowance=allowance, usage=usage, deadline=deadline)

    def ensure_limits(self, **requested):
        """Promote only saved adaptive limits; explicit caps and usage never change."""
        with self.pool.lock:
            ceilings = self.pool.state.get('adaptive_limits', {})
            for key, value in requested.items():
                if key not in ('max_tokens', 'max_input_tokens', 'max_seconds') or isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                    raise ValueError('Invalid requested Assistant limit: ' + key)
                if key in ceilings and self.pool.state[key] is not None:
                    self.pool.state[key] = max(self.pool.state[key], min(value, ceilings[key]))

    @property
    def remaining_tokens(self):
        with self.pool.lock:
            limit = self.pool.state['max_tokens']
            remaining = math.inf if limit is None else max(0, limit - self.pool.state['tokens'])
            if self.allowance is not None:
                remaining = min(remaining, max(0, self.allowance - self.usage['tokens']))
            return remaining

    @property
    def remaining_seconds(self):
        remaining = self.pool.state['max_seconds'] - self.snapshot()['seconds']
        if self.deadline is not None:
            waiting = getattr(self._admitted, 'waiting_since', None)
            extension = time.monotonic() - waiting if waiting is not None else 0
            remaining = min(remaining, self.deadline + extension - time.monotonic())
        return max(0, remaining)

    def snapshot(self):
        with self.pool.lock:
            return {**self.pool.state, 'seconds': self.pool.seconds()}

    @contextmanager
    def inference_slot(self, *, deadline=None):
        """Acquire tree capacity before GUI capacity, reentrantly per worker.

        Proof calls this before reserving a generation, without a deadline, to
        exclude capacity waiting. Ordinary calls retain their bounded wait.
        """
        if getattr(self._admitted, 'active', False):
            yield
            return
        cancel = getattr(self.client, 'cancel', None)
        if cancel is not None and cancel.is_set():
            raise KeyboardInterrupt
        if self.remaining_seconds <= 0:
            raise AssistantBudget('Assistant time budget exhausted before inference admission.')
        exempt = deadline is None
        if exempt:
            self._admitted.waiting_since = time.monotonic()
            self.pool.admission_change(queued=1)
        acquired, active, waiting = False, False, exempt
        try:
            while not self.pool.slots.acquire(timeout=0.05):
                if cancel is not None and cancel.is_set():
                    raise KeyboardInterrupt
                if deadline is not None and time.monotonic() >= deadline:
                    raise TimeoutError('Assistant inference wait exceeded its deadline')
            acquired = True
            if cancel is not None and cancel.is_set():
                raise KeyboardInterrupt
            admission = getattr(self.client, 'inference_slot', None)
            with admission(deadline=deadline) if admission is not None else nullcontext():
                if exempt:
                    elapsed = time.monotonic() - self._admitted.waiting_since
                    if self.deadline is not None:
                        self.deadline += elapsed
                    self._admitted.waiting_since = None
                self.pool.admission_change(queued=-int(waiting), active=1)
                active, waiting = True, False
                self._admitted.active = True
                yield
        finally:
            self._admitted.active = False
            if waiting:
                elapsed = time.monotonic() - self._admitted.waiting_since
                if self.deadline is not None:
                    self.deadline += elapsed
                self._admitted.waiting_since = None
            self.pool.admission_change(queued=-int(waiting), active=-int(active))
            if acquired:
                self.pool.slots.release()

    def stream(self, payload):
        try:
            with self.inference_slot(deadline=time.monotonic() + self.remaining_seconds):
                yield from self._stream(payload)
        except TimeoutError as exc:
            raise AssistantBudget('Assistant time budget exhausted while waiting for inference.') from exc

    def _stream(self, payload):
        cap = payload.get('options', {}).get('num_predict')
        if type(cap) is not int or cap <= 0:
            raise AssistantBudget('Model generation needs a positive output cap.')
        if self.remaining_seconds <= 0:
            raise AssistantBudget('Assistant time budget exhausted.')
        count, method = count_input(self.client, payload)
        if count + cap + 1 > payload['options']['num_ctx']:
            raise AgentError('The complete Assistant context does not fit. No hypotheses or worker results were clipped.')
        with self.pool.lock:
            # Older saved adaptive turns may have reached their initial cap
            # through source reads before delegating. Honor only their saved
            # ceilings when fitting the next complete request.
            self.ensure_limits(max_tokens=self.pool.state['tokens'] + cap,
                               max_input_tokens=self.pool.state['input_tokens'] + count)
            if type(cap) is not int or cap <= 0 or cap > self.remaining_tokens:
                raise AssistantBudget('Assistant output budget cannot cover the next model call.')
            input_limit = self.pool.state['max_input_tokens']
            if input_limit is not None and self.pool.state['input_tokens'] + count > input_limit:
                raise AssistantBudget('Assistant input budget exhausted; no mathematical context was clipped.')
            # Reserve before dispatch. Interrupted or unaccounted calls retain
            # their allowance, so a resume cannot spend those tokens twice.
            self.pool.state['tokens'] += cap
            self.pool.state['reserved_tokens'] += cap
            self.pool.state['input_tokens'] += count
            self.usage['tokens'] += cap
            self.usage['input_tokens'] += count
        self.pool.checkpoint()
        previous_timeout = self.client.timeout
        self.client.timeout = max(0.1, min(previous_timeout, self.remaining_seconds))
        events = None
        charged = False
        reserved = True
        try:
            events = self.client.stream(payload)
            for event in events:
                if not event.get('done') and self.remaining_seconds <= 0:
                    raise AssistantBudget('Assistant time budget exhausted during generation.')
                if event.get('done'):
                    if charged:
                        raise AssistantBudget('Model stream contained duplicate completion events.')
                    charged = True
                    used = event.get('eval_count')
                    if used is not None:
                        if type(used) is not int or used < 0 or used > cap:
                            raise AssistantBudget('Model usage exceeded the reserved Assistant allowance.')
                    with self.pool.lock:
                        self.pool.state['reserved_tokens'] -= cap
                        reserved = False
                        if used is not None:
                            self.pool.state['tokens'] -= cap - used
                            self.usage['tokens'] -= cap - used
                    self.pool.checkpoint()
                yield event
        except ToolCallError as exc:
            # The server completed and reported usage even though its tool
            # arguments are invalid. Refund only that measured unused portion.
            used = exc.stats.get('eval_count')
            if charged or type(used) is not int or not 0 <= used <= cap:
                raise AssistantBudget('Invalid usage on a rejected tool call.') from exc
            with self.pool.lock:
                self.pool.state['reserved_tokens'] -= cap
                reserved = False
                self.pool.state['tokens'] -= cap - used
                self.usage['tokens'] -= cap - used
            self.pool.checkpoint()
            raise
        except AgentError as exc:
            if not isinstance(exc, TokenBudgetError) and self.remaining_seconds <= 0:
                raise AssistantBudget('Assistant time budget exhausted during generation.') from exc
            raise
        finally:
            try:
                if hasattr(events, 'close'):
                    events.close()
            finally:
                with self.pool.lock:
                    if reserved:
                        self.pool.state['reserved_tokens'] -= cap
                self.client.timeout = previous_timeout
                self.pool.checkpoint()
