"""Exercise two-input review navigation through the panel's worker boundary."""
from pathlib import Path
import tempfile
import time
import tkinter as tk
import unittest
from unittest.mock import patch

from PIL import Image

import hold_core
from i18n import language, set_language
from reference_panel import ReferencePanel


class FakePreviewStream:
    def __init__(self, path, meta):
        pass

    def frame(self, number):
        return Image.new('RGB', (24, 16), (number, 0, 0))

    def stop(self):
        pass


class ReferenceReviewTest(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(str(exc))
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        previous = language()
        self.addCleanup(set_language, previous, None, False)
        self.panel = ReferencePanel(self.root)
        self.panel.pack()
        self.addCleanup(self.panel.close)
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.source, self.edit = [Path(folder.name) / name for name in ('source.mp4','edit.mp4')]
        self.source.touch()
        self.edit.touch()
        self.panel.set_inputs(self.source, self.edit)

    def wait(self, predicate):
        deadline = time.monotonic() + 3
        while not predicate() and time.monotonic() < deadline:
            self.root.update()
            time.sleep(.005)
        self.assertTrue(predicate())

    def test_source_list_explains_interpolation_lookup_and_nearest_frame(self):
        panel = self.panel
        meta = dict(width=24, height=16, fps=24)
        result = dict(source_path=str(self.source), change_path=str(self.edit), source_meta=meta, change_meta=meta,
                      source_frames=7, change_frames=6,
                      rows=[dict(source_frame=i, change_frame=j, status=status, review=(status=='review'),
                                 confidence=None if status=='missing' else .5)
                            for i,j,status in [(1,2,'missing'), (2,2,'matched'), (3,2,'missing'), (4,3,'held'),
                                               (5,5,'review'), (6,6,'matched'), (7,6,'missing')]],
                      change_rows=[dict(source_frame=i, change_frame=j, event=event, review=False, confidence=1)
                                   for i,j,event in [(2,1,'start'), (2,2,'hold_candidate'), (4,3,'drop_candidate'),
                                                     (5,4,'normal'), (5,5,'hold_candidate'), (6,6,'normal')]])
        with patch('reference_match.analyze_reference', return_value=result), patch('reference_panel._PreviewStream', FakePreviewStream):
            set_language('ko', self.root, persist=False)
            panel.start()
            self.wait(lambda: panel.analysis is result)
            self.assertEqual(panel.table.get_children(), tuple(str(i) for i in range(1,8)))
            self.assertEqual(panel.confidence_graph._rows, result['rows'])
            self.assertEqual(panel.table['columns'], ('source', 'status', 'plan', 'confidence'))
            self.assertEqual(panel.table.set('1', 'plan'), '편집 2 사용 · 참조 부족')
            self.assertEqual(panel.table.set('2', 'plan'), '편집 2 사용')
            self.assertEqual(panel.table.set('3', 'plan'), '편집 2·5 사이 1/3 지점 생성')
            self.assertEqual(panel.table.set('4', 'plan'), '편집 2·5 사이 2/3 지점 생성')
            self.assertEqual(panel.table.set('5', 'plan'), '편집 5 사용')
            panel.only_issues.set(True)
            panel._fill_table()
            self.assertEqual(panel.table.get_children(), ('1','3','4','5','7'))
            panel.table.selection_set('3')
            self.wait(lambda: panel._displayed == (3,2))
            panel.toggle_playback()
            panel.on_hide()
            self.assertFalse(panel.playing)
            set_language('en', self.root, persist=False)
            panel.on_show()
            self.assertEqual(panel.table.set('5', 'status'), 'Review')
            self.assertEqual(panel.table.set('3', 'plan'), 'Generate at 1/3 between edits 2 and 5')
            self.assertEqual(panel.table.set('5', 'plan'), 'Use edit 5')
            self.assertEqual(panel.table.set('7', 'plan'), 'Use edit 6 · no bracketing pair')
            self.assertEqual(panel._displayed, (3,2))
            panel.set_inputs(change='')
            self.assertIsNone(panel.analysis)
            self.assertFalse(panel.table.get_children())
            self.assertTrue(panel.export.instate(['disabled']))

    def test_full_table_beyond_250_restores_selection_and_split_when_expanded(self):
        panel = self.panel
        self.root.geometry('1200x800+4800+80')
        self.root.deiconify()
        panel.pack(fill='both', expand=True)
        count = 601
        rows = [dict(source_frame=n, change_frame=n, status='matched', review=n in (251, 599),
                     confidence=.47) for n in range(1, count + 1)]
        meta = dict(width=24, height=16, fps=24)
        result = dict(source_path=str(self.source), change_path=str(self.edit), source_meta=meta,
                      change_meta=meta, source_frames=count, change_frames=count, rows=rows)
        with patch('reference_match.analyze_reference', return_value=result), patch('reference_panel._PreviewStream', FakePreviewStream):
            panel.start()
            self.wait(lambda: panel._displayed == (1, 1))
            self.assertEqual(panel.table.get_children(), tuple(str(i) for i in range(1, count + 1)))
            default_width = panel.results_split.sashpos(0) / panel.results_split.winfo_width()
            panel.table.selection_set('599')
            self.wait(lambda: panel._displayed == (599, 599))
            self.assertEqual(panel.confidence_graph._position, 599)
            panel.results_split.sashpos(0, 430)
            self.root.update()
            split = panel.results_split.sashpos(0)
            old_graph_width = panel.confidence_graph.winfo_width()
            panel.table_toggle.invoke()
            self.root.update()
            self.assertFalse(panel.table.winfo_ismapped())
            self.assertGreater(panel.confidence_graph.winfo_width(), old_graph_width)
            panel.confidence_graph._seek(count)
            self.wait(lambda: panel._displayed == (count, count))
            panel.table_toggle.invoke()
            self.root.update()
            self.assertTrue(panel.table.winfo_ismapped())
            self.assertLessEqual(abs(panel.results_split.sashpos(0) - split), 1)
            self.assertEqual(panel.table.selection(), (str(count),))
            self.assertTrue(panel.table.bbox(str(count)))
            panel.only_issues.set(True)
            panel._fill_table()
            self.assertEqual(panel.table.get_children(), ('251', '599'))
            panel.table_toggle.invoke()
            panel.add_button.invoke()
            self.root.update()
            self.assertTrue(panel.table.winfo_ismapped())
            self.assertEqual(len(panel.table.get_children()), count)
            panel._cancel_range_pick()
            panel.table_toggle.invoke()
            self.root.geometry('1380x800+4800+80')
            self.root.update()
            panel.reset_view_settings()
            self.root.update()
            self.assertTrue(panel.table.winfo_ismapped())
            self.assertEqual(panel.table.selection(), (str(count),))
            self.assertLessEqual(abs(panel.results_split.sashpos(0) - default_width * panel.results_split.winfo_width()), 1)

    def test_cancel_keeps_inputs_and_reenables_analysis(self):
        def blocking_scan(source, change, *, cancel, progress, anchors, review_cache):
            if not cancel.wait(2):
                self.fail('Cancellation did not reach analysis worker')
            raise hold_core.Cancelled()
        with patch('reference_match.analyze_reference', side_effect=blocking_scan):
            self.panel.start()
            self.panel.cancel()
            self.wait(lambda: not self.panel.busy)
        self.assertIsNone(self.panel.analysis)
        self.assertEqual(self.panel.paths['change'].get(), str(self.edit))
        self.assertTrue(self.panel.analyze_button.instate(['!disabled']))
