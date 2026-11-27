# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

"""A referee's final verdict must account for confirmed errors in the lemmas a theorem uses."""
import json
import re
import tempfile
import unittest
from pathlib import Path

from mathagent import referee as R
from mathagent.agent import Agent
from mathagent.literature import LiteratureTools
from mathagent.tools import Workspace
from tests.test_research import response, tool

PAPER = r"""\documentclass{article}
\begin{document}
\title{On doubling sequences}
\begin{abstract}
We prove a bound for doubling sequences.
\end{abstract}
\section{Introduction}
\begin{theorem}[Main theorem]\label{thm:main}
For every integer $n\ge 1$ we have $a_n \le 2^n$.
\end{theorem}
\begin{lemma}\label{lem:step}
For every $n$ we have $a_{n+1}\le 2a_n$.
\end{lemma}
\begin{proof}
By definition $a_{n+1}=a_n+b_n$ and $b_n\le a_n$.
Hence the doubling bound follows at once.
\end{proof}
\begin{proof}[Proof of Theorem \ref{thm:main}]
We argue by induction using Lemma \ref{lem:step}.
The base case is clear since $a_1\le 2$.
The inductive step multiplies by two.
\end{proof}
\end{document}
"""


class ScriptClient:
    host = 'http://fake.invalid'
    timeout = 60

    def __init__(self, rounds, judges):
        self.rounds, self.judges = rounds, judges
        self.verdict_prompt = None

    def stream(self, payload):
        messages = payload['messages']
        first = next(message['content'] for message in messages if message['role'] == 'user')
        if payload.get('tools') and 'YOUR TASK' in first:
            who = 'lead' if 'YOUR TASK (lead referee)' in first else re.search(r'co-referee (C\d)', first).group(1)
            index = sum(message['role'] == 'assistant' for message in messages)
            rounds = self.rounds.get(who, [])
            return response(calls=rounds[index]) if index < len(rounds) else response('Done.')
        if payload.get('format') == R.VERDICT_SCHEMA:
            self.verdict_prompt = messages[-1]['content']
            return response(json.dumps({'paragraph': self.verdict}))
        if payload.get('format') == R.READER_SCHEMA:
            return response(json.dumps({'explanation': 'Read line by line.', 'issues': [],
                                        'verdict': 'no_issue_found', 'external': []}))
        issue = re.search(r'THE REPORTED PROBLEM \((I\d+),', first).group(1)
        return response(json.dumps(self.judges[issue]))


class StatusTests(unittest.TestCase):
    def test_an_error_in_a_used_lemma_reaches_the_main_theorem(self):
        doubling = 'Hence the doubling bound follows'
        step = next(n for n, line in enumerate(PAPER.splitlines(), 1) if doubling in line)
        co = [[tool('report_issue', start_line=step, end_line=step, quote=doubling, kind='proof',
                    claim='The doubling bound needs $b_n\\le a_n$, which fails for $n=1$.', evidence='Take $b_1=2a_1$.')],
              [tool('mark_result', result='lem:step', status='issue', how_checked='Line by line.', issue_ids=['I1'])],
              [tool('finish', summary='The lemma fails.')], [tool('finish', summary='The lemma fails.')]]
        lead = [[tool('mark_result', result='thm:main', status='no_error_found', how_checked='The induction is fine.')],
                [tool('finish', summary='The theorem is fine.')]]
        client = ScriptClient({'lead': lead, 'C1': co},
                              judges={'I1': {'verdict': 'confirmed', 'effect': 'breaks_proof', 'reason': 'The bound fails.',
                                             'fix': '', 'fill': '', 'new_ingredient': ''}})
        client.verdict = ('The paper proves a bound for doubling sequences. The main theorem has no error found. '
                          'The lemma it uses has the confirmed error E1.')
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        root = Path(folder.name)
        (root / 'paper.tex').write_text(PAPER)
        agent = Agent(client, Workspace(root, literature=LiteratureTools(root, online=False)), ctx=400_000)
        runner = R.RefereeRunner(agent, concurrency=4)
        result = runner.start('Review this paper.', source_files=['paper.tex'], max_tokens=2_000_000, max_seconds=900)
        c = R.classify(runner)
        main = c['mains'][0]
        self.assertEqual(main['status'], 'relies_on_error')
        self.assertIn('relies on a result with a confirmed error: Lemma (lem:step) (E1)', main['text'])
        self.assertEqual(c['recommendation'], 'major_revision')
        record = runner.state['verdict_text']
        self.assertEqual(record['by'], 'code')  # the model called the theorem free of error
        self.assertIn('calls Theorem (Main theorem) free of error', record['rejected']['why'])
        self.assertIn('relies on a result with a confirmed error', client.verdict_prompt)
        report = result['report']
        self.assertIn('| Theorem (Main theorem) | relies on a result with a confirmed error: Lemma (lem:step) (E1) |', report)
        self.assertIn('Main results: Theorem (Main theorem): relies on', report)
