"""A chosen edit frame replaces just one output slot on the source timeline."""
from copy import deepcopy
from pathlib import Path
import tempfile
import time
import tkinter as tk
import unittest
from unittest.mock import patch

from PIL import Image

from i18n import language, set_language
from reference_panel import ReferencePanel


class FrameOverridePanelTest(unittest.TestCase):
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
        source, edit, preview = (folder / name for name in ('source.mov', 'edit.mov', 'preview.mov'))
        source.touch()
        edit.touch()
        self.preview = preview
        meta = dict(width=12, height=8, fps=24)
        self.report = dict(source_frames=6, change_frames=8, source_path=str(source), change_path=str(edit),
                           source_meta=meta, change_meta=meta,
                           rows=[dict(source_frame=i, change_frame=i+1, status='held' if i==4 else 'matched',
                                      review=False, confidence=.5) for i in range(1, 7)])
        class Stream:
            def __init__(self, path, meta):
                self.base = 100 if Path(path) == preview else 0
            def frame(self, frame):
                return Image.new('RGB', (12,8), (self.base+frame,0,0))
            def stop(self, wait=False):
                pass
        self.scan = self.enterContext(patch('reference_match.analyze_reference',
                                           side_effect=lambda *a, **kw: deepcopy(self.report)))
        self.enterContext(patch('reference_panel._PreviewStream', Stream))
        self.panel.set_inputs(source, edit)

    def wait(self, condition):
        deadline = time.monotonic()+4
        while not condition() and time.monotonic() < deadline:
            self.root.update()
            time.sleep(.005)
        self.assertTrue(condition())

    def analyze(self):
        self.panel.analyze_button.invoke()
        self.wait(lambda: self.panel.analysis and not self.panel.busy)

    def test_selecting_a_displayed_candidate_changes_one_output_without_rematching(self):
        p = self.panel
        self.assertTrue(p.override_button.instate(['disabled']))
        self.assertFalse(p.set_frame_override(3,1))
        self.analyze()
        rows = deepcopy(p.analysis['rows'])
        p.seek(3)
        p.override_button.invoke()
        self.assertTrue(p._editing_pair)
        p.seek(1)
        self.wait(lambda: p._displayed == (3,1))
        self.assertEqual(p._images['change'].getpixel((0,0))[0], 1)
        self.assertFalse(p.use_frame_button.instate(['disabled']))
        p.use_frame_button.invoke()
        self.wait(lambda: not p._editing_pair)
        self.assertEqual(p._images['change'].getpixel((0,0))[0], 1)
        self.assertEqual(self.scan.call_count, 1)
        self.assertEqual(p._position, 3)
        self.assertEqual(p.analysis['rows'], rows)
        self.assertFalse(p.analysis.get('manual_anchors'))
        self.assertEqual(p.analysis['frame_overrides'], [dict(source_frame=3,change_frame=1)])
        p.only_issues.set(True)
        p._fill_table()
        self.assertIn('3', p.table.get_children())
        self.assertEqual(p.table.set('3','status'), '프레임 변경')
        self.assertIn('1', p.table.set('3','plan'))
        self.assertIn('4·6', p.table.set('4','plan'))
        self.assertTrue(p.set_frame_override(4,1))
        self.assertEqual(len(p.analysis['frame_overrides']), 2)
        self.assertTrue(p.remove_frame_override(4))
        self.assertIn('4·6', p.table.set('4','plan'))
        p.seek(3)
        p.clear_override_button.invoke()
        self.assertFalse(p.analysis.get('frame_overrides'))
        self.assertNotIn('3', p.table.get_children())
        self.assertEqual(p.analysis['rows'],rows)

    def test_change_invalidates_preview_reanalysis_preserves_choice_and_new_inputs_clear_it(self):
        self.analyze()
        p = self.panel
        path = self.preview
        path.touch()
        class Preview:
            def __init__(self):
                self.path = path
            def close(self):
                path.unlink(missing_ok=True)
        with patch('reference_render.render_reference_preview', return_value=Preview()):
            p.preview_button.invoke()
            self.wait(lambda: p._displayed_after)
        self.assertTrue(p.set_frame_override(2,8))
        self.assertFalse(path.exists() or p.preview_after.get())
        self.assertFalse(p._displayed_after)
        self.assertFalse(p.set_frame_override(2,9))
        self.assertEqual(p.analysis['frame_overrides'], [dict(source_frame=2,change_frame=8)])
        self.analyze()
        self.assertEqual(p.analysis['frame_overrides'], [dict(source_frame=2,change_frame=8)])
        p.set_inputs(change='')
        self.assertIsNone(p.analysis)
        self.assertTrue(p.override_button.instate(['disabled']))
