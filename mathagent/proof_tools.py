# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

"""The tools a Proof role may use and the primary evidence it can share with the other role."""
from copy import copy
import json
from urllib.parse import unquote_plus

from .numerics.artifacts import ExperimentStore
from .numerics.policy import SANITY
from .numerics.session import normalize_code
from .proof_policy import evidence_policy
from .review_position import copied_phrases

PAPER_TOOLS = ('search_papers', 'open_paper', 'search_paper', 'read_paper')
SANDBOX_TOOLS = ('execute_python', 'read_experiment')


class ProofDesk:
    def __init__(self, agent, private_texts=()):
        self.literature = getattr(agent.workspace, 'literature', None)
        session = getattr(agent.workspace, 'numerics', None)
        self.numerics = session if (getattr(session, 'invocation', None) == 'automatic' and
                                    getattr(getattr(session, 'policy', None), 'name', None) == SANITY) else None
        self.experiments = self.numerics.store if self.numerics else ExperimentStore(agent.workspace.root)
        self.online_allowed = True
        self.protect(private_texts)

    instructions = staticmethod(evidence_policy)
    new_receipt_id = staticmethod(ExperimentStore.new_id)

    def protect(self, private_texts):
        self.private_texts = (private_texts,) if isinstance(private_texts, str) else tuple(private_texts)

    def restrict_online(self, allowed):
        self.online_allowed = self.online_allowed and allowed

    def _library(self):
        # Ordinary calls use the live library: disabling online in a running chat also interrupts future fetches.
        if self.literature is None or self.online_allowed or not self.literature.online:
            return self.literature
        library = copy(self.literature)
        library.online = False  # shares the live statistics and reservation callback
        return library

    def restore_literature(self, snapshot):
        if self.literature is not None and snapshot:
            self.restrict_online(snapshot.get('online') is True)
            self.literature.restore({**snapshot, 'online': self.literature.online})

    def schemas(self):
        library = self._library()
        schemas = ([s for s in library.schemas() if s['function']['name'] in PAPER_TOOLS] if library else [])
        if self.numerics is not None:
            for item in self.numerics.schemas():
                name = item['function']['name']
                if name in SANDBOX_TOOLS:
                    if name == 'execute_python':
                        # The full skill also describes tools outside this role. Keep its actual parameter contract.
                        description = ('Run a calculation in the isolated Python sandbox. Returns its status, '
                                       'output and experiment_id. Cite this ID if the argument uses the calculation.')
                        item = {**item, 'function': {**item['function'], 'description': description}}
                    schemas.append(item)
        return schemas

    def execute(self, name, arguments, *, receipt_id=None):
        try:
            if isinstance(arguments, str):
                arguments = json.loads(arguments)
            if not isinstance(arguments, dict):
                raise ValueError('Tool arguments must be an object')
            if name in PAPER_TOOLS and (library := self._library()) is not None:
                if name in ('search_papers', 'open_paper') and library.online:
                    query = arguments.get('query') if name == 'search_papers' else arguments.get('identifier_or_url')
                    if isinstance(query, str) and any(copied_phrases({'question': unquote_plus(query)}, text)
                                                      for text in self.private_texts):
                        raise ValueError('The query repeats private source text. Use public mathematical terms or a paper identifier.')
                return library.execute(name, arguments)
            if name in SANDBOX_TOOLS and self.numerics is not None:
                if name == 'execute_python':
                    return self.numerics.execute(name, arguments, experiment_id=receipt_id)
                return self.numerics.execute(name, arguments)
            raise ValueError(f'Unavailable Proof tool: {name}')
        except (ValueError, TypeError, OSError) as exc:
            return json.dumps({'error': str(exc)})

    def _experiment(self, experiment_id, arguments=None):
        if not experiment_id:
            return None
        try:
            record = self.experiments.read_saved(experiment_id)
        except (OSError, ValueError):
            return None
        if arguments is not None and (record.get('code') != normalize_code(arguments['code']) or
                                      record.get('parameters') != (arguments.get('parameters') or {}) or
                                      record.get('seed') != arguments.get('seed')):
            return None
        return record

    def recover(self, name, arguments, receipt_id):
        """Return a completed native run from a preallocated ID; an ambiguous dispatch stays unresolved."""
        if name != 'execute_python' or not receipt_id or not isinstance(arguments.get('code'), str):
            return None
        record = self._experiment(receipt_id, arguments)
        if record is None:
            return None
        value = {key: record.get(key) for key in ('status', 'stdout', 'stderr', 'result', 'error', 'warnings',
                                                  'stdout_truncated', 'stderr_truncated')}
        if self.numerics is None:
            return json.dumps({'experiment_id': receipt_id, 'status': record['status'],
                               'message': 'The saved run was recovered. The sandbox is currently unavailable.'})
        return self.numerics._render({'experiment_id': receipt_id, **value})

    def primary_evidence(self, name, arguments, result):
        """Return the inspectable observation. Metadata hits, opinions, and failed checks are not proof evidence."""
        try:
            value = json.loads(result)
        except (ValueError, TypeError):
            return None
        if not isinstance(value, dict) or value.get('error'):
            return None
        if name == 'read_paper' and all(value.get(k) for k in ('passage', 'document_id', 'text_sha256', 'citation')):
            return {'kind': 'passage', **value}
        if name == 'read_experiment' and self.numerics is not None:
            experiment_id = arguments.get('experiment_id')
            if not experiment_id or (value.get('id') or value.get('experiment_id')) != experiment_id:
                return None
            record = self._experiment(experiment_id)
            if record and record.get('status') == 'ok':
                # A summary and a paged read can both establish the identity of this native run.
                # Project the observable result into the same result allowance as the tool itself.
                observable = self.numerics._render({'experiment_id': experiment_id, 'status': 'ok',
                                                     'result': record.get('result'), 'stdout': record.get('stdout')})
                observed = json.loads(observable)
                if 'text' in value and value.get('part') in ('result', 'stdout'):
                    observed['observed_page'] = {k: value[k] for k in ('part', 'offset', 'length', 'text', 'next_offset') if k in value}
            else:
                return None
        elif name == 'execute_python' and value.get('status') == 'ok' and isinstance(arguments.get('code'), str):
            record = self._experiment(value.get('experiment_id'), arguments)
            if not record or record.get('status') != 'ok':
                return None
            observed = {k: record[k] for k in ('result', 'stdout') if k in value}
        else:
            return None
        evidence = {k: record.get(k) for k in ('code', 'seed', 'parameters', 'stdout_truncated', 'stderr_truncated')}
        return {'kind': 'experiment', 'experiment_id': record['id'], 'status': 'ok', **evidence,
                **{k: observed[k] for k in ('result', 'stdout', 'observed_page') if k in observed}}
