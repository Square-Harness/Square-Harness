# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

"""Decode model JSON without depending on provider grammar support.

Extraction tolerates prose, Markdown fences and thinking blocks. Consumers
still validate their own schemas; incomplete JSON is never invented.
"""
import json
import re


def loads(text):
    if not isinstance(text, str):
        raise TypeError('Expected JSON text')
    text = re.sub(r'<think>.*?</think>', '', text, flags=re.S)
    text = re.sub(r'^.*?</think>', '', text, flags=re.S).strip()  # a reasoning block whose opening tag was not sent
    decoder = json.JSONDecoder(strict=False)
    error, first_list = None, None
    for candidate in _objects(text):
        for value in (candidate, _repair_latex(candidate)):
            try:
                result = decoder.decode(value)
            except ValueError as exc:
                error = exc
                continue
            if isinstance(result, dict):
                return _repair_values(result)  # an object is the answer; "[0, 1]" in the prose before it is not
            if isinstance(result, list) and first_list is None:
                first_list = result
            break
    if first_list is not None:
        return _repair_values(first_list)
    raise ValueError('No complete JSON value in model response') from error


def complete_fields(text):
    """The fields of a top-level JSON object that were written completely, in order, when the
    object itself was cut off (a length stop). An unfinished list keeps its complete items; any
    other unfinished value is left out: nothing is completed or invented."""
    if not isinstance(text, str):
        return {}
    text = re.sub(r'<think>.*?</think>', '', text, flags=re.S)
    start = text.find('{')
    if start == -1:
        return {}
    decoder, out, index = json.JSONDecoder(strict=False), {}, start + 1
    skip = re.compile(r'[\s,]*')
    try:
        while True:
            index = skip.match(text, index).end()
            if index >= len(text) or text[index] == '}':
                return _repair_values(out)
            key, index = decoder.raw_decode(text, index)
            index = re.compile(r'\s*:\s*').match(text, index).end()
            try:
                value, index = decoder.raw_decode(text, index)
            except ValueError:
                if text.startswith('[', index) and isinstance(key, str):
                    items, index = [], index + 1
                    while True:
                        index = skip.match(text, index).end()
                        try:
                            item, index = decoder.raw_decode(text, index)
                        except ValueError:
                            break
                        items.append(item)
                    if items:
                        out[key] = items
                raise
            if isinstance(key, str):
                out[key] = value
    except (ValueError, AttributeError):
        return _repair_values(out)


def _objects(text):
    """Yield complete outer candidates, never salvage an unfinished child."""
    offset = 0
    while match := re.search(r'[\{\[]', text[offset:]):
        start = offset + match.start()
        stack, quoted, escaped = [], False, False
        for index in range(start, len(text)):
            char = text[index]
            if quoted:
                if escaped:
                    escaped = False
                elif char == '\\':
                    escaped = True
                elif char == '"':
                    quoted = False
                continue
            if char == '"':
                quoted = True
            elif char in '{[':
                stack.append(char)
            elif char in '}]':
                if not stack or stack.pop() != ('{' if char == '}' else '['):
                    offset = index + 1
                    break
                if not stack:
                    yield text[start:index + 1]
                    offset = index + 1
                    break
        else:
            if text[start] == '[' and re.match(r'\[\s*[-+\w.\\^{}]+\s*,\s*[-+\w.\\^{}]+\s*\)', text[start:]):
                offset = start + 1  # a half-open interval such as [0,1) in prose, not unfinished JSON
                continue
            # A nested object may be complete while the containing JSON is not.
            # Its fields must never be promoted to a standalone verdict.
            return


def _repair_latex(text):
    # Leave JSON escapes intact, including escaped quotes and backslashes.
    return re.sub(r'(?<!\\)\\(?!["\\/bfnrt]|u[0-9a-fA-F]{4})', r'\\\\', text)


def repair_latex(text):
    controls = {'\f': 'f', '\b': 'b', '\t': 't', '\r': 'r'}
    suffixes = ('abla', 'eq', 'otin', 'ot', 'mid', 'geq', 'leq', 'ewline', 'oindent', 'subseteq', 'otag', 'onumber')
    text = re.sub('[\f\b\t\r](?=[A-Za-z])', lambda m: '\\' + controls[m.group()], text)
    return re.sub('\n(?=(?:' + '|'.join(suffixes) + r')(?![A-Za-z]))', r'\\n', text)


def _repair_values(value):
    if isinstance(value, str):
        return repair_latex(value)
    if isinstance(value, list):
        return [_repair_values(item) for item in value]
    if isinstance(value, dict):
        return {key: _repair_values(item) for key, item in value.items()}
    return value
