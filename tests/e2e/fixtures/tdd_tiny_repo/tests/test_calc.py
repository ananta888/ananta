import unittest

from calc import absolute_gap


class AbsoluteGapTests(unittest.TestCase):
    def test_absolute_gap_is_symmetric(self) -> None:
        self.assertEqual(absolute_gap(2, 5), 3)
        self.assertEqual(absolute_gap(5, 2), 3)
