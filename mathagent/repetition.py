# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

"""Recognize sustained repetition in one generated text channel."""

import re
import zlib
from collections import Counter
from hashlib import blake2b


# Each sample searches for four or more consecutive, identical copies. The
# longest anchor covers prose cycles of up to 16,384 characters. These windows
# bound the detector's working set. Fresh model output can continue indefinitely.
_STRIDE = 1024
_ANCHORS = (2048, 6144, 24576, 49152)
_HISTORY = _ANCHORS[-1] * 4 // 3
# Keep the passage detector active in the overlap with the longest exact window.
_LANDMARK_MIN_PERIOD = 8192
_PROSE_WINDOW = 12288
_SENTENCE_END = re.compile(r"(?<=[.!?。！？])\s+")
_WORD = re.compile(r"[^\W\d_]{3,}")
_TEX_COMMAND = re.compile(r"\\[a-zA-Z@]+")
_PROSE_MARK = re.compile(r"[:;!?。！？]|\.(?!\d)")
_NUMERIC_CELL = re.compile(r'(?:^|[,;])\s*"?[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"?\s*(?:[,;]|$)')
_PASSAGE_END = re.compile(r'[.!?。！？][ \t]|\n')
# A degenerate template can vary one slot per copy ("(not read) (no page) (no line) ..."), or repeat a label
# without sentence punctuation ("[unverified memory] ..."); neither forms an exact prose cycle. Measured on
# DeepSeek V4.1 Flash 1017 (November 2026): such tails compress to 0.5-4% of their size and only 0.2-5% of
# their words are distinct. These are cheap filters. Indexed cases may be equally compressible, so require
# the text between phrase occurrences to recur as well.
_TEMPLATE_WINDOW = 16384
_TEMPLATE_RATIO = 0.06
_TEMPLATE_DISTINCT = 0.08
_TEMPLATE_PHRASE = 20
# A JSON grammar allows unbounded whitespace between tokens; no healthy answer or reasoning has this long a run.
_WHITESPACE_RUN = 2048


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


def _is_compressed_template(tail: str) -> bool:
    window = tail[-_TEMPLATE_WINDOW:]
    raw = window.encode("utf-8", "surrogatepass")
    # A data table or matrix has rows; one long line (a JSON string value) is not a table.
    if len(zlib.compress(raw, 6)) >= _TEMPLATE_RATIO * len(raw) or (window.count("\n") >= 8 and _is_tabular(window)):
        return False
    words = window.split()
    if not words or len(set(words)) >= _TEMPLATE_DISTINCT * len(words):
        return False
    phrases = Counter(tuple(words[i:i + 8]) for i in range(len(words) - 7))
    if not phrases:
        return False
    phrase, count = phrases.most_common(1)[0]
    # Repeated language, not a matrix, a table of numbers or a formula.
    language = _WORD.findall(_TEX_COMMAND.sub("", " ".join(phrase)).casefold())
    if count < _TEMPLATE_PHRASE or len(set(language)) < 2:
        return False
    # Match whole words. Keep the original text between matches, including
    # numbers, punctuation, signs and line breaks that distinguish results.
    pattern = r"(?<!\S)" + r"\s+".join(map(re.escape, phrase)) + r"(?!\S)"
    starts = [match.start() for match in re.finditer(pattern, window)]
    if len(starts) < 4:
        return False
    blocks = [window[left:right] for left, right in zip(starts, starts[1:])]
    middle = len(blocks) // 2
    earlier = set(blocks[:middle])
    if any(block not in earlier for block in blocks[middle:]):
        return False
    # The final occurrence can end mid-block. It must also follow an earlier
    # occurrence up to the last character received.
    suffix = window[starts[-1]:]
    return any(window.startswith(suffix, start) for start in starts[:middle])


class _Landmark:
    """Fingerprint all passages between consecutive returns to the same text."""

    def __init__(self, offset: int, prose: int):
        self.block = blake2b(digest_size=16)
        self.previous = b''
        self.copies = 0
        self.offset = offset
        self.prose = prose

    def revisit(self, offset: int, prose: int) -> bool:
        length = offset - self.offset
        digest = self.block.digest()
        # Short cycles already have an exact text check. A stray prose heading
        # in a repeated data table does not make that table a prose cycle.
        if length > _LANDMARK_MIN_PERIOD and (prose - self.prose) * 2 >= length:
            self.copies = self.copies + 1 if digest == self.previous else 1
        else:
            self.copies = 0
        self.previous = digest
        self.offset, self.prose = offset, prose
        self.block = blake2b(digest_size=16)
        return self.copies >= 4


class _LongProseCycles:
    """Recognize repeated ordered blocks without buffering a block of any particular length."""

    def __init__(self):
        self.landmarks: dict[bytes, _Landmark] = {}
        self.passage = blake2b(digest_size=16)
        self.previous_passage = b''
        self.sample = ''
        self.carry = ''
        self.tabular_line = False
        self.eligible = self.offset = self.prose = self.length = 0

    def feed(self, text: str) -> bool:
        text = self.carry + text
        # A sentence delimiter may straddle two incoming samples.
        self.carry = text[-1:] if text.endswith(('.', '!', '?', '。', '！', '？')) else ''
        if self.carry:
            text = text[:-1]
        start = 0
        for boundary in _PASSAGE_END.finditer(text):
            self._append(text[start:boundary.end()])
            if self._finish(boundary.group() == '\n'):
                return True
            start = boundary.end()
        self._append(text[start:])
        return False

    def _append(self, piece: str):
        self.passage.update(piece.encode('utf-8', 'surrogatepass'))
        self.offset += len(piece)
        self.length += len(piece)
        # This sample only classifies the passage. Its digest covers every byte.
        self.sample += piece[:1024 - len(self.sample)]

    def _finish(self, newline: bool) -> bool:
        digest = self.passage.digest()
        self.passage = blake2b(digest_size=16)
        for point in self.landmarks.values():
            point.block.update(digest)
        sample, length = self.sample, self.length
        self.sample, self.length = '', 0
        tabular = self.tabular_line or _is_tabular(sample)
        self.tabular_line = tabular and not newline
        if not sample.strip():
            return False
        key = digest + self.previous_passage
        self.previous_passage = digest
        if tabular or not _is_prose_cycle(sample):
            return False
        self.prose += length
        self.eligible += 1
        point = self.landmarks.get(key)
        if point is not None and point.revisit(self.offset, self.prose):
            return True
        # Retain content-selected landmarks, which recur at the same position
        # within each pass irrespective of transport chunking. Doubling epochs
        # admits new landmarks after a unique prefix and accommodates long
        # periods without keeping ever more text or signatures.
        if (self.eligible & (self.eligible - 1)) == 0:
            self.landmarks.clear()
            if point is not None:
                self.landmarks[key] = point
        if key not in self.landmarks:
            if len(self.landmarks) == 4:
                worst = max(self.landmarks)
                if key > worst:
                    return False
                del self.landmarks[worst]
            self.landmarks[key] = _Landmark(self.offset, self.prose)
        return False


class RepetitionDetector:
    """Feed consecutive deltas from one channel. A positive signal latches."""

    def __init__(self) -> None:
        self.kind: str | None = None
        self.characters_seen = 0
        self._pending: list[str] = []
        self._pending_size = 0
        self._tail = ""
        self._long = _LongProseCycles()
        self._blank_run = 0

    def feed(self, text: str) -> bool:
        """Return true once this channel shows sustained repetitive prose or a runaway of whitespace."""
        offset = 0
        while self.kind is None and offset < len(text):
            take = min(_STRIDE - self._pending_size, len(text) - offset, _WHITESPACE_RUN)
            piece = text[offset:offset + take]
            # A piece is at most one run long. Only its leading whitespace
            # can complete a run carried from the previous piece.
            if self._blank_run + take >= _WHITESPACE_RUN:
                leading = take - len(piece.lstrip())
                if self._blank_run + leading >= _WHITESPACE_RUN:
                    self.characters_seen += _WHITESPACE_RUN - self._blank_run
                    self.kind = "whitespace"
                    break
            trailing = take - len(piece.rstrip())
            self._blank_run = self._blank_run + take if trailing == take else trailing
            self._pending.append(piece)
            offset += take
            self._pending_size += take
            self.characters_seen += take
            if self._pending_size < _STRIDE:
                break
            sample = "".join(self._pending)
            self._tail = self._tail[-(_HISTORY - _STRIDE):] + sample
            self._pending.clear()
            self._pending_size = 0
            if _has_exact_cycle(self._tail) or self._long.feed(sample):
                self.kind = "exact"
            elif (
                self.characters_seen >= _PROSE_WINDOW
                and self.characters_seen % (4 * _STRIDE) == 0
                and _has_recycled_sentences(self._tail)
            ):
                self.kind = "repeated_prose"
            elif (
                self.characters_seen >= _TEMPLATE_WINDOW
                and self.characters_seen % (4 * _STRIDE) == 0
                and _is_compressed_template(self._tail)
            ):
                self.kind = "template"
        return self.kind is not None
