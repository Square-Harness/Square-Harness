# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

"""FIFO admission for parallel Review calls sharing a refundable input/output allowance."""
import math
import threading
from collections import deque

from .assistant_budget import AssistantAdmissionRefused, BudgetClient
from .referee import MeteredClient
from .research import ResearchBudget


class GraphMeteredClient(MeteredClient):
    def __init__(self, runner, role, who):
        super().__init__(runner, role, who)
        # Reference helpers, dynamic checks and resumed roles must join the same queue as the lead.
        with runner._mutex:
            if not hasattr(runner, '_graph_admission'):
                runner._graph_admission = (threading.Condition(runner._mutex), deque())
            self.condition, self.queue = runner._graph_admission

    def fork_reference(self, ident, transport):
        peer = GraphMeteredClient(self.runner, self.role, f'{self.who}-{ident}')
        peer.client = transport
        with self.condition:
            peer.rounds = sum(c.get('role') == peer.role and c.get('referee') == peer.who
                              for c in self.runner.state['calls'])
        return peer

    def _capacity(self):
        """Planning room after pending requests settle, admission room available now, and pending holds."""
        runner = self.runner
        active = [c for c in runner.state['calls'] if c['status'] == 'running']
        output_held = sum(c['reserved_tokens'] for c in active)
        input_held = sum(c['reserved_input_tokens'] for c in active)
        output, inputs = runner._tokens_left(), runner._input_left()
        planning = (output + output_held, inputs + input_held)
        transport = self.client
        if isinstance(transport, BudgetClient):
            # The Assistant also accounts for calls from other concurrent jobs. Its output holds are
            # separately tracked; its input ledger folds live reservations into the measured total.
            with transport.pool.lock:
                outer_output = transport.remaining_tokens
                outer_inputs = transport.remaining_input_tokens
                outer_held = transport.pool.state.get('reserved_tokens', 0)
                ceilings = (transport.input_allowance, transport.pool.state['max_input_tokens'], math.inf)
                input_ceiling = min(n for n in ceilings if n is not None)
            possible_input = input_ceiling if outer_held else outer_inputs
            planning = (min(planning[0], outer_output + outer_held), min(planning[1], possible_input))
            # Our job records its reservation just before the Assistant reserves the same call. Account
            # for this short interval. Other Assistant jobs may race dispatch; an explicit, unbilled
            # outer refusal reenters the queue below.
            outer_output -= max(0, output_held - outer_held)
            if not outer_held:
                outer_inputs -= input_held
            return planning, (min(output, outer_output), min(inputs, outer_inputs)), bool(active or outer_held)
        outer_output = getattr(transport, 'remaining_tokens', math.inf)
        outer_inputs = getattr(transport, 'remaining_input_tokens', math.inf)
        if outer_output is None:
            outer_output = math.inf
        if outer_inputs is None:
            outer_inputs = math.inf
        planning = (min(planning[0], outer_output + output_held), min(planning[1], outer_inputs + input_held))
        return planning, (min(output, outer_output), min(inputs, outer_inputs)), bool(active)

    @property
    def remaining_tokens(self):
        with self.condition:
            return self._capacity()[0][0]

    @property
    def remaining_input_tokens(self):
        with self.condition:
            return self._capacity()[0][1]

    def _round_capacity(self, cap, count):
        # MeteredClient holds the job mutex until the charge and call record are installed. Waiting
        # releases it. No model inference slot or tool thread owned by a different request is needed.
        ticket = object()
        self.queue.append(ticket)
        cancel = getattr(self.client, 'cancel', None) or getattr(self.runner, 'cancel', None)
        try:
            while True:
                if cancel is not None and cancel.is_set():
                    raise KeyboardInterrupt
                self.runner._check_time()
                if self.queue[0] is ticket:
                    possible, (output, inputs), outstanding = self._capacity()
                    if possible[0] < min(cap, 128) or count > possible[1]:
                        raise ResearchBudget("The review's settled token allowance cannot fund this request.")
                    wanted = int(min(cap, possible[0]))
                    if wanted <= output and count <= inputs:
                        return wanted
                    if not outstanding:
                        if count > inputs or output < min(cap, 128):
                            raise ResearchBudget("The review's settled token allowance cannot fund this request.")
                        return int(min(cap, output))
                # Other Assistant jobs have their own admission conditions. Periodically observe their
                # releases and any external cancellation in addition to the local settlement notification.
                self.condition.wait(0.1)
        finally:
            self.queue.remove(ticket)
            self.condition.notify_all()

    def stream(self, payload):
        # A scope-external sibling can win the outer ledger between our reservation and its transport
        # dispatch. Only this explicitly unbilled token refusal is eligible for readmission. All other
        # provider errors, quota failures, and timeouts follow the existing recovery and metering path.
        requested = payload['options']['num_predict']
        while True:
            try:
                payload['options']['num_predict'] = requested
                yield from super().stream(payload)
                return
            except AssistantAdmissionRefused as exc:
                if not isinstance(self.client, BudgetClient) or exc.resource not in ('input', 'output'):
                    raise
            finally:
                with self.condition:
                    self.condition.notify_all()
