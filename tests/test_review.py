"""The three review workflows (quick, journal, explain) with a fake model that answers by schema."""
import copy
import json
from pathlib import Path
import re
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from mathagent.agent import Agent
from mathagent.research import ResearchRunner
from mathagent.review import _prose_comment, EFFORT, ExplainRunner, JournalRunner, QuickReviewRunner, SummaryReviewRunner, level_for, runner_for_state
from mathagent.tools import Workspace

WRONG = """Lemma. Let (u_n) be a bounded sequence in H^1(0,1). Then a subsequence converges strongly in L^2(0,1).

Proof. Since H^1(0,1) is a Hilbert space, a subsequence converges weakly in H^1.
The embedding of H^1(0,1) into L^2(0,1) is continuous, hence the subsequence converges strongly in L^2.
This proves the lemma.
"""

PAPER = r"""\documentclass{amsart}
\newtheorem{theorem}{Theorem}[section]
\newtheorem{lemma}[theorem]{Lemma}
\newtheorem{definition}[theorem]{Definition}
\begin{document}
\begin{abstract}
We prove compactness of bounded sequences.
\end{abstract}
\section{Introduction}
Let $H^1(0,1)$ be the Sobolev space.
\begin{theorem}\label{thm:main}
Every bounded sequence in $H^1(0,1)$ has a subsequence converging in $L^2(0,1)$.
\end{theorem}
\section{Preliminaries}
\begin{definition}\label{def:h1}
$H^1(0,1)$ is the space of $L^2$ functions with an $L^2$ weak derivative.
\end{definition}
\begin{lemma}\label{lem:embed}
The embedding $H^1(0,1)\hookrightarrow L^2(0,1)$ is compact.
\end{lemma}
\begin{proof}
This is Rellich's theorem \cite[Theorem 9.16]{brezis}.
\end{proof}
\section{Proof of the main result}
\begin{proof}[Proof of Theorem~\ref{thm:main}]
By Definition~\ref{def:h1} and Lemma~\ref{lem:embed}, the sequence is bounded in a Hilbert space.
Since the embedding is continuous, the sequence converges strongly in $L^2$.
\end{proof}
\appendix
\section{Auxiliary}
\begin{lemma}\label{lem:aux}
An auxiliary fact.
\end{lemma}
\begin{proof}
Obvious.
\end{proof}
\begin{thebibliography}{9}
\bibitem{brezis} H. Brezis. Functional Analysis, Sobolev Spaces and PDE. Springer, 2011.
\end{thebibliography}
\end{document}
"""


def line_of(prompt, phrase):
    match = re.search(r'L(\d+): [^\n]*' + re.escape(phrase), prompt)
    return int(match.group(1)) if match else 0


def default_check(prompt, schema):
    """The fake verifier flags the 'continuous, hence … strongly' step wherever it appears."""
    n = line_of(prompt, 'continuous, hence') or line_of(prompt, 'embedding is continuous')
    proof = re.search(r'\nPROOF[^\n]*:\n(.*?)(?:\n\n|\Z)', prompt, re.S)
    issues = []
    if n and proof and f'L{n}:' in proof.group(1):
        issue = {'start_line': n, 'end_line': n, 'quote': 'continuous, hence' if 'continuous, hence' in prompt else 'embedding is continuous',
                 'kind': 'invalid_inference', 'evidence': 'Continuity of the embedding does not turn weak into strong convergence; compactness (Rellich) is needed.',
                 'suggestion': 'Use the compactness of the embedding (Rellich–Kondrachov).'}
        if 'severity' in json.dumps(schema):
            issue['severity'] = 'major'
        issues.append(issue)
    value = {'explanation': 'Checked each step.', 'issues': issues, 'verdict': 'issues_found' if issues else 'no_issue_found'}
    if 'external' in schema['properties']:
        value['external'] = [{'key': 'brezis', 'place': 'Theorem 9.16', 'used_for': 'Rellich compactness theorem in one dimension'}] if 'cite' in prompt or 'Rellich' in prompt else []
    return value


def default_reply(payload, overrides):
    prompt = payload['messages'][1]['content']
    schema = payload.get('format') or {}
    props = set(schema.get('properties', {}))
    for name, test in (('confirm', {'assessment', 'argument'}), ('check', {'explanation', 'issues', 'verdict'}),
                       ('explain', {'says', 'idea'}), ('fill', {'steps'}), ('explain_check', {'problems'}),
                       ('scope', {'contribution', 'main'}), ('write', {'summary', 'recommendation'}),
                       ('novelty', {'assessment', 'related'}), ('read', {'summary', 'comments'}),
                       ('extract', {'proof_start'}), ('pick', {'unit'})):
        if test <= props and (name != 'fill' or props == {'steps'}):
            if name in overrides:
                return overrides[name](prompt, schema)
            if name == 'check':
                return default_check(prompt, schema)
            if name == 'confirm':
                return {'assessment': 'valid', 'kind': 'invalid_inference', 'argument': 'Weak convergence plus a continuous map gives only weak convergence.',
                        'suggestion': 'Invoke Rellich.'}
            if name == 'scope':
                ids = re.findall(r'(U\d+): Theorem', prompt)
                return {'overview': 'The paper proves Rellich compactness on (0,1) from a compact embedding lemma.',
                        'field': 'Sobolev spaces', 'contribution': 'Compactness of bounded sequences.', 'main': ids[:1],
                        'notation': [{'start_line': 10, 'end_line': 10}], 'closest': ['brezis'], 'queries': ['Rellich compactness Sobolev']}
            if name == 'write':
                return {'summary': 'The paper proves a compactness result.', 'significance': 'Classical.', 'correctness': 'See Major 1.',
                        'presentation': 'Clear.', 'recommendation': 'major_revision', 'reasons': 'Major 1 invalidates the proof.',
                        'confidential': 'Only the main proof and its lemma were checked.'}
            if name == 'explain_check':
                return {'problems': []}
            if name == 'novelty':
                return {'assessment': 'The result is classical (result 1).', 'related': [{'n': 1, 'relation': 'textbook source'}]}
            if name == 'extract':
                return {'statement_start': 1, 'statement_end': 1, 'proof_start': 0, 'proof_end': 0}
            if name == 'read':
                n = line_of(prompt, 'Sobolev space')
                return {'summary': 'This part sets up $H^1(0,1)$ and states the main theorem.',
                        'comments': [{'start_line': n, 'end_line': n, 'quote': 'be the Sobolev space', 'kind': 'definition',
                                      'comment': 'Say which norm.', 'suggestion': 'Add the norm.'}] if n else []}
            raise AssertionError('No fake reply for ' + name)
    raise AssertionError('Unexpected request: ' + json.dumps(sorted(props)))


class FakeClient:
    host = 'http://fake.invalid'
    timeout = 60

    def __init__(self, overrides=None, fail_on=None, delay=0):
        self.overrides = overrides or {}
        self.requests = []
        self.fail_on = fail_on  # (role keyword in prompt, call number) → KeyboardInterrupt
        self.delay = delay
        self.lock = threading.Lock()

    def stream(self, payload):
        with self.lock:
            self.requests.append(copy.deepcopy(payload))
            count = len(self.requests)
        if self.fail_on and self.fail_on(payload, count):
            raise KeyboardInterrupt
        if self.delay:
            time.sleep(self.delay)
        value = default_reply(payload, self.overrides)
        text = value if isinstance(value, str) else json.dumps(value)
        yield {'message': {'content': text}, 'done': True, 'done_reason': 'stop',
               'eval_count': min(50, payload['options']['num_predict']), 'prompt_eval_count': 200}


class ReviewTestCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def agent(self, client, ctx=16384, predict=4096):
        workspace = Workspace(self.root)
        workspace.literature = None
        agent = Agent(client, workspace, ctx=ctx, predict=predict)
        if predict != 4096:  # these fixtures deliberately exercise an explicit specialist cap
            agent.document_output_cap = predict
        agent.seed = 7
        return agent

    def kinds(self, client):
        return [sorted(r.get('format', {}).get('properties', {})) for r in client.requests]


class QuickTests(ReviewTestCase):
    def test_planted_error_is_found_confirmed_and_located(self):
        client = FakeClient()
        runner = QuickReviewRunner(self.agent(client))
        result = runner.start(WRONG, max_rounds=3)
        self.assertEqual(result['status'], 'reviewed')
        self.assertEqual(runner.state['verdict'], 'issues_found')
        self.assertEqual(runner.state['level'], 'medium')
        self.assertEqual([s['id'] for s in runner.state['sources']], ['P1'])
        self.assertEqual(runner.state['unit']['proofs'], [[3, 5]])
        self.assertEqual(len(runner.state['checks']), 2)  # medium: line by line + adversarial
        self.assertEqual(len(runner.state['confirms']), 1)
        report = result['report']
        self.assertIn('Issues found', report)
        self.assertIn('[P1:L4]', report)
        self.assertIn('confirmed by an independent re-check; raised by 2 of 2 passes', report)
        self.assertIn('Rellich', report)
        self.assertIn('L4: The embedding', report)  # the numbered proof in the appendix
        lenses = [r['messages'][1]['content'] for r in client.requests if 'LENS FOR THIS PASS' in r['messages'][1]['content']]
        self.assertTrue(any('LINE BY LINE' in p for p in lenses) and any('ADVERSARIAL' in p for p in lenses))














class JournalTests(ReviewTestCase):
    def setUp(self):
        super().setUp()
        (self.root / 'paper.tex').write_text(PAPER)

    def test_report_from_checked_findings(self):
        client = FakeClient()
        runner = JournalRunner(self.agent(client))
        result = runner.start('Review this paper for a journal.', source_files=['paper.tex'], max_rounds=3)
        self.assertEqual(result['status'], 'reviewed')
        names = {u['id']: u['name'] for u in runner.state['map']['units']}
        self.assertEqual([names[u] for u in runner.state['plan_units']], ['Theorem 1.1', 'Lemma 2.2'])  # medium: no appendix
        report = result['report']
        self.assertIn('## Major comments', report)
        self.assertIn('1. *Theorem 1.1.* **Invalid inference** at [M1:L27]', report)
        self.assertIn('**Major revision**', report)
        self.assertNotIn('(provisional)', report)
        self.assertIn('| Theorem 1.1 (main) | L25-L28 | 1 | issues found |', report)
        self.assertIn('| Lemma A.1 | L34-L36 | 0 | not checked |', report)
        self.assertIn('Online search was off', report)
        # Understanding and presentation come first, read over the whole paper before any proof is checked.
        self.assertLess(report.index('## The paper in brief'), report.index('## Typos and presentation'))
        self.assertLess(report.index('## Typos and presentation'), report.index('## Major comments'))
        self.assertIn('[M1:L10] *definition* — "be the Sobolev space": Say which norm.', report)
        self.assertIn('Read for understanding and presentation:', report)
        roles = [r['messages'][1]['content'].split('\n')[0][:30] for r in client.requests]
        self.assertTrue(roles[0].startswith('Read this part'))
        # every check got the notation line the scope chose, before the statement
        checks = [r['messages'][1]['content'] for r in client.requests if 'LENS FOR THIS PASS' in r['messages'][1]['content']]
        self.assertTrue(all(c.index('L10: Let $H^1(0,1)$') < c.index('STATEMENT:') for c in checks))











if __name__ == '__main__':
    unittest.main()
