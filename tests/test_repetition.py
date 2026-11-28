# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

import unittest

from mathagent.repetition import RepetitionDetector


def argument(index):
    return (f"The hypothesis in case {index} gives the bound x_{index} ≤ {index ** 2}. "
            f"Upon inserting this bound in the integral for case {index}, "
            f"its contribution is at most {index ** 2 + 1}.")


class RepetitionTests(unittest.TestCase):
    def test_distinctive_passages_recognize_long_exact_cycles(self):
        for count, separator in [(90, "\n\n"), (1000, " ")]:
            with self.subTest(count=count):
                block = separator.join(argument(n) for n in range(count)) + separator
                self.assertGreater(len(block), 8192)
                prefix = "\n".join(argument(-n) for n in range(150)) + "\n"
                text = prefix + block * 16
                detector = RepetitionDetector()
                self.assertTrue(detector.feed(text))
                self.assertEqual(detector.kind, 'exact')
                self.assertGreater(detector.characters_seen, len(prefix) + 3 * len(block))
                fragmented = RepetitionDetector()
                for offset in range(0, len(text), 17):
                    if fragmented.feed(text[offset:offset + 17]):
                        break
                self.assertEqual(fragmented.characters_seen, detector.characters_seen)

    def test_changes_in_intervening_math_break_the_cycle(self):
        common = "\n".join(argument(n) for n in range(100))
        variants = [common + f"\nFor this case the hypothesis is x ≥ {j}.\n" for j in range(20)]
        self.assertFalse(RepetitionDetector().feed("".join(variants)))

    def test_reader_cycle_after_a_long_nonrepeating_prefix(self):
        prefix = "".join(argument(-n - 1) + "\n" for n in range(250))
        block = "".join(argument(n) + "\n" for n in range(85))
        detector = RepetitionDetector()
        self.assertFalse(detector.feed(prefix + block * 3))
        self.assertTrue(detector.feed(block * 4))
        self.assertEqual(detector.kind, 'exact')

    def test_repeated_prose_headers_in_a_table(self):
        labels = "The following entries list the estimates for each component.\n"
        rows = "".join(f"| Component {n} | This value is exact. It is unchanged. |\n"
                       for n in range(250))
        self.assertFalse(RepetitionDetector().feed((labels + rows) * 12))


if __name__ == '__main__':
    unittest.main()
