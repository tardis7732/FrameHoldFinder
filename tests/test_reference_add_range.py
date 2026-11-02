"""Manual fills are added on the analyzed source timeline and affect the export plan."""
from copy import deepcopy
from pathlib import Path
import tempfile
import threading
import time
import tkinter as tk
import unittest
from unittest.mock import patch

from PIL import Image

from i18n import language, set_language
from reference_panel import ReferencePanel
from reference_render import plan_reference


class ReferenceAddRangeTest(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(str(exc))
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        self.addCleanup(set_language, language(), None, False)
        set_language('ko', persist=False)
        self.panel = ReferencePanel(self.root)
        self.panel.pack()
        self.addCleanup(self.panel.close)
        folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.source, self.edit, self.preview = (folder / name for name in ('src.mp4', 'edit.mov', 'preview.mov'))
        self.source.touch()
        self.edit.touch()
        meta = dict(width=16, height=12, fps=24)
        self.report = dict(source_path=str(self.source), change_path=str(self.edit),
                           source_meta=meta, change_meta=meta, source_frames=7, change_frames=10,
                           rows=[dict(source_frame=i, change_frame=i+2, status='review' if i == 4 else 'matched',
                                      confidence=.7, review=(i == 4)) for i in range(1, 8)])
        preview = self.preview
        class Stream:
            def __init__(self, path, meta):
                self.offset = 100 if Path(path) == preview else 0
            def frame(self, frame):
                return Image.new('RGB', (16,12), (frame + self.offset,0,0))
            def stop(self, wait=False):
                pass
        self.enterContext(patch('reference_panel._PreviewStream', Stream))
        self.enterContext(patch('reference_panel.messagebox.showerror'))
        self.panel.set_inputs(self.source, self.edit)

    def wait(self, predicate):
        end = time.monotonic() + 4
        while not predicate() and time.monotonic() < end:
            self.root.update()
            time.sleep(.005)
        self.assertTrue(predicate())

    def analyze(self):
        with patch('reference_match.analyze_reference', return_value=deepcopy(self.report)):
            self.panel.analyze_button.invoke()
            self.wait(lambda: self.panel.analysis is not None and not self.panel.busy)

    def render_preview(self):
        path = self.preview
        path.touch()
        class Preview:
            def __init__(self):
                self.path = path
            def close(self):
                path.unlink(missing_ok=True)
        with patch('reference_render.render_reference_preview', return_value=Preview()):
            self.panel.preview_button.invoke()
            self.wait(lambda: self.panel._displayed_after)

    def test_add_is_available_only_after_analysis_and_outside_pair_editor(self):
        panel = self.panel
        self.assertTrue(panel.add_button.instate(['disabled']))
        self.assertFalse(panel.add_range(2,5))
        finish = threading.Event()
        def delayed_scan(*args, **kwargs):
            if not finish.wait(3):
                raise RuntimeError('Timed out waiting for test')
            return deepcopy(self.report)
        with patch('reference_match.analyze_reference', side_effect=delayed_scan):
            panel.analyze_button.invoke()
            self.assertTrue(panel.busy and panel.add_button.instate(['disabled']))
            self.assertFalse(panel.add_range(2,5))
            finish.set()
            self.wait(lambda: panel.analysis is not None and not panel.busy)
        self.assertFalse(panel.add_button.instate(['disabled']))
        panel.anchor_toggle.invoke()
        self.assertTrue(panel._editing_pair and panel.add_button.instate(['disabled']))
        self.assertFalse(panel.add_range(2,5))
        panel.anchor_toggle.invoke()
        self.assertFalse(panel.add_button.instate(['disabled']))
        panel.set_inputs(change='')
        self.assertTrue(panel.add_button.instate(['disabled']))

    def test_add_remove_refreshes_same_frame_and_filters_added_rows(self):
        panel = self.panel
        self.analyze()
        panel.seek(3)
        self.render_preview()
        self.assertEqual(panel._images['change'].getpixel((0,0))[0], 103)
        self.assertTrue(panel.add_range(2,5))
        self.wait(lambda: panel._images.get('change') and panel._images['change'].getpixel((0,0))[0] == 5)
        self.assertEqual(panel._position, 3)
        self.assertFalse(self.preview.exists() or panel.preview_after.get())
        self.assertEqual(panel.analysis['manual_ranges'], [{'left':2, 'right':5}])
        self.assertEqual(panel.analysis['rows'], self.report['rows'])
        self.assertEqual(plan_reference(panel.analysis)['runs'], [{'start':2, 'end':4, 'generated':2}])
        panel.only_issues.set(True)
        panel._fill_table()
        for frame, fraction in ((3,'1/3'), (4,'2/3')):
            self.assertTrue(panel.table.set(str(frame),'status').startswith('추가'))
            plan = panel.table.set(str(frame),'plan')
            self.assertIn('4·7', plan)
            self.assertIn(fraction, plan)
        self.assertFalse(panel.remove_range(2))
        self.render_preview()
        panel.remove_range_button.invoke()
        self.wait(lambda: not panel.analysis.get('manual_ranges'))
        self.assertFalse(self.preview.exists() or panel.preview_after.get())
        self.assertEqual(plan_reference(panel.analysis)['runs'], [])
        self.assertEqual(panel.table.get_children(), ('4',))
        self.assertEqual(panel.table.set('4','status'), '확인 필요')

    def test_add_button_dialog_validates_and_shows_the_actual_edit_references(self):
        from reference_range_dialog import _RangeDialog

        panel = self.panel
        self.analyze()
        panel.seek(3)
        observed = {}
        def choose_endpoints():
            dialog = next(w for w in self.root.winfo_children() if isinstance(w, _RangeDialog))
            try:
                observed['default_edit_refs'] = (dialog.left_var.get(), dialog.right_var.get())
                dialog.left_var.set('4')
                dialog.right_var.set('5')
                observed['blocked'] = dialog.add_button.instate(['disabled'])
                dialog.right_var.set('7')
                observed['summary'] = dialog.info.cget('text')
                dialog.add_button.invoke()
            finally:
                if dialog.winfo_exists():
                    dialog.cancel()
        panel.add_button.invoke()
        self.root.after(50, choose_endpoints)
        panel.edit_numbers_button.invoke()
        self.assertTrue(observed['blocked'])
        self.assertEqual(observed['default_edit_refs'], ('4', '6'))
        self.assertIn('2 · 5', observed['summary'])
        self.assertIn('3–4', observed['summary'])
        self.assertEqual(panel.analysis['manual_ranges'], [{'left':2, 'right':5, 'left_change':4, 'right_change':7}])

    def test_rematch_remaps_added_span_and_input_change_clears_it(self):
        panel = self.panel
        self.analyze()
        self.assertTrue(panel.add_range(2,5))
        self.render_preview()
        updated = deepcopy(self.report)
        for row in updated['rows']:
            row['change_frame'] += 1
        with patch('reference_match.analyze_reference', return_value=updated):
            panel.analyze_button.invoke()
            self.wait(lambda: not panel.busy)
        self.assertFalse(self.preview.exists())
        self.assertEqual(panel.analysis['manual_ranges'], [{'left':2, 'right':5}])
        self.assertIn('5·8', panel.table.set('3','plan'))
        self.render_preview()
        unchanged = panel.analysis
        conflict = deepcopy(self.report)
        for row in conflict['rows']:
            row['change_frame'] = 3
        with patch('reference_match.analyze_reference', return_value=conflict):
            panel.analyze_button.invoke()
            self.wait(lambda: not panel.busy)
        self.assertIs(panel.analysis, unchanged)
        self.assertTrue(self.preview.exists())
        self.assertIn('5·8', panel.table.set('3','plan'))
        next_edit = self.edit.with_name('next-edit.mov')
        next_edit.touch()
        panel.set_inputs(change=str(next_edit))
        self.report['change_path'] = str(next_edit)
        self.assertFalse(self.preview.exists())
        self.assertIsNone(panel.analysis)
        self.analyze()
        self.assertFalse(panel.analysis.get('manual_ranges'))
