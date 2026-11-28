# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

"""Recognize sustained repetition in one generated text channel."""

import re
from collections import Counter


# Each sample searches for four or more consecutive, identical copies. The
# longest anchor covers prose cycles of up to 8,192 characters. These windows
# bound the detector's working set. Fresh model output can continue indefinitely.
_STRIDE = 1024
_ANCHORS = (2048, 6144, 24576)
_HISTORY = _ANCHORS[-1] * 4 // 3
_PROSE_WINDOW = 12288
_SENTENCE_END = re.compile(r"(?<=[.!?。！？])\s+")
_WORD = re.compile(r"[^\W\d_]{3,}")
_TEX_COMMAND = re.compile(r"\\[a-zA-Z@]+")
_PROSE_MARK = re.compile(r"[:;!?。！？]|\.(?!\d)")
_NUMERIC_CELL = re.compile(r'(?:^|[,;])\s*"?[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"?\s*(?:[,;]|$)')


def _is_tabular(text: str) -> bool:
    lines = text.splitlines()
    table_rows = sum(
        "\t" in line or line.lstrip().startswith("|") or line.rstrip().endswith(r"\\")
        or (("," in line or ";" in line) and _NUMERIC_CELL.search(line) is not None)
        for line in lines
    )
    return bool(table_rows) and table_rows * 2 >= len(lines)


def _is_prose_cycle(cycle: str) -> bool:
    # A matching unit can start mid-word. Rotate it at a word boundary before
    # examining its language. Ignore repeated digits, delimiters and formulae.
    boundary = cycle.find("\n") if "\n" in cycle else cycle.find(" ")
    if boundary < 0:
        return False
    unit = (cycle[boundary + 1:] + cycle[:boundary + 1]).strip()
    if _is_tabular(unit) or ("\n" not in unit and re.search(r"\|\s*\|", unit + unit)):
        return False
    prose = _TEX_COMMAND.sub("", unit)
    if not _PROSE_MARK.search(prose):
        return False
    words = _WORD.findall(prose.casefold())
    return len(set(words)) >= 2 and sum(map(len, words)) >= len(unit) // 3


def _has_exact_cycle(tail: str) -> bool:
    for size in _ANCHORS:
        start = len(tail) - size
        if start <= 0:
            break
        # Both matching anchors may overlap. A lag of at most size / 3
        # proves at least four full copies, including for a long paragraph.
        previous = tail.rfind(tail[start:], max(0, start - size // 3), len(tail) - 1)
        if previous >= 0 and _is_prose_cycle(tail[previous:start]):
            return True
    return False


def _has_recycled_sentences(tail: str) -> bool:
    # A model can vary the order of nearly identical statements. Require a
    # common opening AND reuse of complete statements, including their numbers,
    # mathematical symbols and case. A common frame with new results is progress.
    window = tail[-_PROSE_WINDOW:]
    sentences = _SENTENCE_END.split(window)[1:-1]
    if len(sentences) < 64:
        return False
    middle = len(sentences) // 2
    earlier = set(sentences[:middle])
    recent = sentences[middle:]
    if any(sentence not in earlier for sentence in recent):
        return False
    openings = Counter()
    for sentence in recent:
        words = sentence.split(None, 5)
        if len(words) == 6 and all(_WORD.search(word) for word in words[:5]):
            openings[tuple(words[:5])] += 1
    return bool(openings) and max(openings.values()) >= 0.85 * len(recent) and not _is_tabular(window)


class RepetitionDetector:
    """Feed consecutive deltas from one channel. A positive signal latches."""

    def __init__(self) -> None:
        self.kind: str | None = None
        self.characters_seen = 0
        self._pending: list[str] = []
        self._pending_size = 0
        self._tail = ""

    def feed(self, text: str) -> bool:
        """Return true once this channel shows sustained repetitive prose."""
        offset = 0
        while self.kind is None and offset < len(text):
            take = min(_STRIDE - self._pending_size, len(text) - offset)
            self._pending.append(text[offset:offset + take])
            offset += take
            self._pending_size += take
            self.characters_seen += take
            if self._pending_size < _STRIDE:
                break
            self._tail = self._tail[-(_HISTORY - _STRIDE):] + "".join(self._pending)
            self._pending.clear()
            self._pending_size = 0
            if _has_exact_cycle(self._tail):
                self.kind = "exact"
            elif (
                self.characters_seen >= _PROSE_WINDOW
                and self.characters_seen % (4 * _STRIDE) == 0
                and _has_recycled_sentences(self._tail)
            ):
                self.kind = "repeated_prose"
        return self.kind is not None
