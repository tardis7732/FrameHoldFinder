import unittest

from motion_settings import DEFAULT_MIN_MOVE_RATIO, default_min_move, valid_min_move


class MotionSettingsTests(unittest.TestCase):
    def test_default_number_follows_actual_analysis_width(self):
        self.assertEqual(DEFAULT_MIN_MOVE_RATIO, 0.005)
        for width, expected in ((960, 4.8), (640, 3.2), (320, 1.6)):
            with self.subTest(width=width):
                self.assertAlmostEqual(default_min_move(width), expected)

    def test_manual_pixel_value_is_used_directly(self):
        self.assertEqual(valid_min_move(1.7), 1.7)
        self.assertEqual(valid_min_move(' 1.7 '), 1.7)
        self.assertEqual(valid_min_move(19.2), 19.2)
        self.assertEqual(valid_min_move(2000), 2000.)
        self.assertEqual(valid_min_move(default_min_move(960)), 4.8)

    def test_invalid_analysis_widths_are_rejected(self):
        for value in (0, -1, float('nan'), float('inf'), -float('inf'), None, '', 'bad', True, 1j):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    default_min_move(value)

    def test_invalid_manual_pixel_values_are_rejected(self):
        for value in (0, -0.001, float('nan'), float('inf'), -float('inf'), None, '', 'bad', True, False, 1j):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    valid_min_move(value)


if __name__ == '__main__':
    unittest.main()
