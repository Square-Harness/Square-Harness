"""Research budgets, provenance and durable workflow behavior without network/model calls."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from mathagent.agent import Agent
from mathagent.literature import LiteratureTools
from mathagent.research import ResearchRunner, _reference_prompt, _reference_snapshot
from mathagent.tools import Workspace


DOC = 'doc-' + 'a' * 24


def response(text='', calls=(), *, complete=True, count=20, input_count=100):
    return [{'message': {'content': text, 'tool_calls': list(calls)}, 'done': True,
             'done_reason': 'stop' if complete else 'length', 'eval_count': count,
             'prompt_eval_count': input_count}]


def tool(name, **args):
    return {'function': {'name': name, 'arguments': args}}


class FakeClient:
    host = 'http://fake.invalid'
    timeout = 60

    def __init__(self, replies):
        self.replies = iter(replies)
        self.requests = []

    def stream(self, payload):
        self.requests.append(copy.deepcopy(payload))
        for event in next(self.replies):
            if isinstance(event, BaseException):
                raise event
            yield event






if __name__ == '__main__':
    unittest.main()
