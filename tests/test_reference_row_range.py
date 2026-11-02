"""Select two trusted source rows, then fill the interior with the shown edit images."""
import tkinter as tk
import unittest
from unittest.mock import patch

from PIL import Image

from i18n import language, set_language
from reference_panel import ReferencePanel
from reference_render import describe_reference_frames


class SourceRowRangeTest(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(str(exc))
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        self.addCleanup(set_language, language(), None, False)
        set_language('ko', self.root, persist=False)
        self.panel = ReferencePanel(self.root)
        self.panel.pack()
        self.addCleanup(self.panel.close)
        stream = self.enterContext(patch('reference_panel._PreviewStream'))
        stream.return_value.frame.side_effect = lambda n: Image.new('RGB', (8, 8), (n, 0, 0))
        self.panel.set_inputs('source.mov', 'edit.mov')

    def load_result(self):
        meta = dict(width=8, height=8, fps=24)
        rows = [dict(source_frame=i, change_frame=max(1, i-1 if i<99 else i-2),
                     status='missing' if i==99 else 'matched', review=False, confidence=.7)
                for i in range(1, 102)]
        self.panel._job = 'analyze'
        self.panel._job_event('done', dict(source_path='source.mov', change_path='edit.mov',
                                          rows=rows, source_frames=101, change_frames=99,
                                          source_meta=meta, change_meta=meta))
        self.root.update()

    def pick(self, first, last):
        p = self.panel
        p.seek(first)
        p.add_button.invoke()
        self.assertEqual(p._range_start, first)
        self.assertTrue(p.apply_range_button.instate(['disabled']))
        p.table.selection_set(str(last))
        self.root.update()
        self.assertEqual(p._position, last)

    def test_reverse_pick_confirms_only_the_three_interiors(self):
        p = self.panel
        self.assertTrue(p.add_button.instate(['disabled']))
        self.load_result()
        original = describe_reference_frames(p.analysis)
        self.pick(98, 94)
        self.assertFalse(p.apply_range_button.instate(['disabled']))
        for text in ('94', '98', '93', '97', '95–97', '3장'):
            self.assertIn(text, p.range_hint.get())
        self.assertFalse(p.analysis.get('manual_ranges'))
        p.apply_range_button.invoke()
        self.assertIsNone(p._range_start)
        actions = describe_reference_frames(p.analysis)
        for step, n in enumerate(range(95, 98), 1):
            row = actions[n-1]
            self.assertEqual((row['kind'], row['left_change'], row['right_change'], row['step'], row['count']),
                             ('generate', 93, 97, step, 3))
            self.assertEqual(p.table.set(str(n), 'status'), '추가')
        self.assertEqual(original[98], actions[98])  # Source 99 still interpolates edit 97/98.
        self.assertEqual(actions[93]['change_frame'], 93)
        self.assertEqual(actions[97]['change_frame'], 97)
        self.assertTrue(p.remove_range(96))
        self.assertEqual(describe_reference_frames(p.analysis), original)

    def test_invalid_pick_cancel_and_input_change_leave_no_plan(self):
        p = self.panel
        self.load_result()
        self.pick(94, 95)
        self.assertTrue(p.apply_range_button.instate(['disabled']))
        p.seek(98)
        self.assertFalse(p.apply_range_button.instate(['disabled']))
        p.seek(99)  # Generated frame has no single matched edit image to use as an endpoint.
        self.assertTrue(p.apply_range_button.instate(['disabled']))
        p.cancel_range_button.invoke()
        self.assertIsNone(p._range_start)
        self.assertFalse(p.analysis.get('manual_ranges'))
        self.pick(94, 98)
        p.set_inputs(change='other.mov')
        self.assertIsNone(p._range_start)
        self.assertIsNone(p.analysis)
        self.assertTrue(p.add_button.instate(['disabled']))

    def test_picker_can_reach_filtered_anchors_and_rejects_an_overlapping_span(self):
        p = self.panel
        self.load_result()
        self.assertTrue(p.add_range(95, 98))
        spans = list(p.analysis['manual_ranges'])
        p.only_issues.set(True)
        p._fill_table()
        self.assertFalse(p.table.exists('94'))
        self.pick(94, 100)
        self.assertTrue(p.table.exists('94'))
        self.assertTrue(p.apply_range_button.instate(['disabled']))
        p.apply_range_button.invoke()
        self.assertEqual(p.analysis['manual_ranges'], spans)
        p.cancel_range_button.invoke()
        self.assertTrue(p.only_issues.get())
        self.assertFalse(p.table.exists('94'))
