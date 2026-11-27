# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

"""Link passages in a rendered paper to physical lines in a matching TeX manuscript."""
from bisect import bisect_left
import re
import unicodedata

from .. import manuscript


_WORD = re.compile(r"[^\W_]+|[=<>+*/^|−-]")
_ENV = re.compile(r"\\(?:begin|end)\{[^{}]*\}")
_STYLE = re.compile(r"\\(?:emph|textit|textbf|textrm|textnormal|mathrm|left|right|big[glr]?|Big[glr]?)\b")
# A phrase must occur exactly once in each document to establish a correspondence.
_PHRASE_WORDS = 5
_PROSE = re.compile(r"(?<![\w\\])[^\W\d_]{2,}(?:\s+[^\W\d_]{2,})+\b")


def _tex_lines(tex):
    """Keep physical rows, restoring names and references printed by TeX counters."""
    structure = manuscript.build(tex, "paper.tex")
    numbers = {unit["label"]: unit["number"] for unit in structure["units"]
               if unit["label"] and unit["number"]}
    headings = {unit["statement"][0]: unit["name"] for unit in structure["units"]
                if unit["statement"] and unit["number"]}
    for number, line in enumerate(manuscript._active_tex(tex.splitlines()), 1):
        line = manuscript.TEX_LABEL.sub(" ", line)
        line = manuscript.TEX_REF.sub(lambda m: numbers.get(m[1], " "), line)
        line = manuscript.TEX_CITE.sub(" ", line)
        line = line.replace(r"\begin{proof}", " Proof ").replace(r"\end{proof}", " ∎ ")
        line = _STYLE.sub("", _ENV.sub(" ", line))
        yield headings.get(number, "") + " " + line


def _tokens(lines):
    words, positions, prose_runs = [], [], []
    for number, line in enumerate(lines, 1):
        line = unicodedata.normalize("NFKC", line).replace("−", "-").replace("∎", " qedmarker ")
        start = len(words)
        offsets = []
        for token in _WORD.finditer(line):
            word = token.group()
            words.append(word.casefold() if len(word) > 1 else word)
            positions.append(number)
            offsets.append(token.start())
        for phrase in _PROSE.finditer(line):
            left = start + bisect_left(offsets, phrase.start())
            right = start + bisect_left(offsets, phrase.end())
            prose_runs.append((number, left, right))
    return words, positions, prose_runs


def _unmatched_prose(words, runs, matches, other_phrases):
    missing = set()
    for line, start, end in runs:
        for i in range(start, end - _PHRASE_WORDS + 1):
            end_word = i + _PHRASE_WORDS
            if not any(matches[i:end_word]) and tuple(words[i:end_word]) not in other_phrases:
                missing.add(line)
                break
    return missing


def _unique_phrases(words):
    """Record the position of each phrase, or None when it occurs more than once."""
    index = {}
    for i in range(len(words) - _PHRASE_WORDS + 1):
        phrase = tuple(words[i:i + _PHRASE_WORDS])
        index[phrase] = None if phrase in index else i
    return index


class PaperSourceLines:
    """Index one confirmed paper/TeX pair. All coordinates are one-based and inclusive."""

    def __init__(self, lines, tex):
        if isinstance(lines, str):
            lines = lines.splitlines()
        paper, paper_lines, paper_prose = _tokens(lines)
        source, source_lines, source_prose = _tokens(_tex_lines(tex))
        source_phrases = _unique_phrases(source)
        paper_phrases = _unique_phrases(paper)
        # 0 means that no unique phrase covers this token, -1 that two disagree.
        targets = [0] * len(paper)
        matched_source = bytearray(len(source))
        for phrase, i in paper_phrases.items():
            j = source_phrases.get(phrase)
            if i is None or j is None:
                continue
            for offset in range(_PHRASE_WORDS):
                matched_source[j + offset] = 1
                target = source_lines[j + offset]
                previous = targets[i + offset]
                targets[i + offset] = target if previous in (0, target) else -1

        self._lines = {}
        self._source = {}
        for number, target in zip(paper_lines, targets):
            if not target:
                continue
            if target == -1:
                self._lines[number] = None
                continue
            if number not in self._lines:
                self._lines[number] = (target, target)
            else:
                span = self._lines[number]
                if span:
                    self._lines[number] = (span[0], target) if target >= span[1] else None
            first = self._source.get(target, (number, number))[0]
            self._source[target] = (first, number)
        # A run of unaccounted-for prose between anchors may be an addition
        # or deletion in another edition. Equations and layout can change
        # substantially during rendering even with identical TeX input.
        self._unmatched_paper = _unmatched_prose(paper, paper_prose, targets, source_phrases)
        self._unmatched_source = _unmatched_prose(source, source_prose, matched_source, paper_phrases)

    def range(self, start, end):
        """Return the matched source passage, or None for ambiguous/unmatched endpoints."""
        if start < 1 or end < start:
            return None
        first, last = self._lines.get(start), self._lines.get(end)
        if not first or not last:
            return None
        if first[0] in self._unmatched_source or last[1] in self._unmatched_source:
            return None
        previous = first[0]
        for number in range(start, end + 1):
            if number in self._unmatched_paper:
                return None
            if number in self._lines:
                span = self._lines[number]
                if not span or span[0] < previous:
                    return None
                previous = span[1]
        for number in range(first[0] + 1, last[1]):
            if number in self._unmatched_source:
                return None
            span = self._source.get(number)
            if span and (span[0] < start or span[1] > end):
                return None
        return first[0], last[1]
