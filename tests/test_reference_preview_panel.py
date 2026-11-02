"""Rendered preview must stay on the original timeline even around missing frames."""
from pathlib import Path
import tempfile
import threading
import time
import tkinter as tk
import unittest
from unittest.mock import patch

from PIL import Image, ImageTk

from i18n import language, set_language
from reference_panel import ReferencePanel


class RenderedPreviewPanelTests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(str(exc))
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        self.enterContext(patch('sdk_bridge.configured_quality', return_value='HIGH'))
        self.panel = ReferencePanel(self.root)
        self.panel.pack()
        self.addCleanup(self.panel.close)
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.source, self.edit, self.rendered = [Path(folder.name) / name for name in
                                                ('source.mp4', 'edit.mov', 'preview.mov')]
        self.source.touch()
        self.edit.touch()
        self.result = dict(
            source_path=str(self.source), change_path=str(self.edit), source_frames=4, change_frames=3,
            source_meta=dict(width=12, height=8, fps=24), change_meta=dict(width=12, height=8, fps=30),
            rows=[dict(source_frame=src, change_frame=edit, status=status, review=False, confidence=None)
                  for src, edit, status in [(1,1,'matched'),(2,1,'missing'),(3,2,'matched'),(4,3,'matched')]],
        )
        self.pending = set()
        paths = {self.source: 20, self.edit: 10, self.rendered: 30}
        def open_stream(path, meta):
            path = Path(path)
            factor = paths[path]
            class Stream:
                def frame(_, n):
                    if (path, n) not in self.pending:
                        return Image.new('RGB', (12,8), (factor*n, n, 0))
                def stop(_, wait=False):
                    pass
            return Stream()
        self.enterContext(patch('reference_panel._PreviewStream', side_effect=open_stream))
        self.enterContext(patch('reference_match.analyze_reference', return_value=self.result))
        self.panel.set_inputs(self.source, self.edit)
        self.panel.start()
        self.wait(lambda: self.panel._displayed == (1,1))

    def wait(self, predicate):
        deadline = time.monotonic() + 5
        while not predicate() and time.monotonic() < deadline:
            self.root.update()
            time.sleep(.005)
        self.assertTrue(predicate())

    def preview_file(self):
        path = self.rendered
        path.touch()
        class Artifact:
            def __init__(self):
                self.path = path
            def close(self):
                path.unlink(missing_ok=True)
        return Artifact()

    def right_pixel(self):
        return ImageTk.getimage(self.panel._photos['change']).getpixel((0,0))[:3]

    def toggle_after(self, value):
        self.panel.preview_after.set(value)
        self.panel._preview_changed()

    def test_dragging_review_divider_keeps_transport_and_graph_controls_visible(self):
        panel = self.panel
        self.root.geometry('1100x760+15000+15000')
        panel.pack(fill='both', expand=True)
        self.root.deiconify()
        self.root.update()
        split = panel.review_split

        def drag(delta):
            x, y = split.winfo_width() // 2, split.sash_coord(0)[1] + 2
            self.assertEqual(split.identify(x, y), (0, 'sash'))
            for event, pointer_y in (('<ButtonPress-1>', y), ('<B1-Motion>', y + delta),
                                     ('<ButtonRelease-1>', y + delta)):
                split.event_generate(event, x=x, y=pointer_y)
                self.root.update()

        canvas = panel.canvases['source']
        start_height = canvas.winfo_height()
        drag(-48)
        self.assertEqual(canvas.winfo_height(), start_height - 48)
        drag(48)
        self.assertEqual(canvas.winfo_height(), start_height)
        drag(1000)
        self.assertTrue(panel.confidence_graph._legend.winfo_ismapped())
        self.assertTrue(panel.confidence_graph.navigation.y_entry.winfo_ismapped())
        drag(-1000)
        panel.anchor_toggle.invoke()
        self.root.update()
        self.assertTrue(panel.pair_editor.winfo_ismapped())
        self.assertTrue(panel.play.winfo_ismapped())
        self.assertGreater(canvas.winfo_height(), 1)
        self.assertEqual(panel._displayed, (1, 1))

    def test_solo_views_keep_current_frames_preview_and_difference(self):
        panel = self.panel
        self.root.geometry('1200x760+15000+15000')
        panel.pack(fill='both', expand=True)
        self.root.deiconify()
        self.root.update_idletasks()
        source, edit = panel.canvases['source'], panel.canvases['change']
        split_width = source.winfo_width()
        self.assertEqual(panel.view_box.current(), 0)
        self.assertAlmostEqual(split_width, edit.winfo_width(), delta=1)

        def select(index):
            panel.view_box.current(index)
            panel.view_box.event_generate('<<ComboboxSelected>>')
            self.root.update_idletasks()

        panel.seek(3)
        self.wait(lambda: panel._displayed == (3, 2))
        select(1)
        self.assertFalse(edit.winfo_ismapped())
        self.assertFalse(panel.difference_button.winfo_ismapped())
        self.assertGreaterEqual(source.winfo_width(), split_width * 2 - 2)
        self.assertEqual(ImageTk.getimage(panel._photos['source']).getpixel((0, 0))[:3], (60, 3, 0))
        select(2)
        self.assertFalse(source.winfo_ismapped())
        self.assertGreaterEqual(edit.winfo_width(), split_width * 2 - 2)
        self.assertEqual(self.right_pixel(), (20, 2, 0))
        panel.difference_button.invoke()
        panel.difference_gain.set('2×')
        panel._difference_changed()
        self.assertEqual(self.right_pixel(), (80, 2, 0))

        with patch('reference_render.render_reference_preview', return_value=self.preview_file()):
            panel.preview_button.invoke()
            self.wait(lambda: panel._displayed_after and not panel.busy)
        self.assertEqual(self.right_pixel(), (60, 0, 0))
        panel.anchor_toggle.invoke()
        self.wait(lambda: not panel._displayed_after and panel._displayed == (3, 2))
        self.assertEqual(self.right_pixel(), (80, 2, 0))
        self.assertEqual(panel.view_box.current(), 2)
        self.assertFalse(source.winfo_ismapped())
        panel.anchor_toggle.invoke()
        self.wait(lambda: panel._displayed_after)
        self.addCleanup(set_language, language(), None, False)
        set_language('en', persist=False)
        panel.refresh_language()
        self.assertEqual(panel.view_box.get(), 'Edit only')
        select(0)
        self.assertTrue(source.winfo_ismapped())
        self.assertTrue(edit.winfo_ismapped())
        self.assertEqual(source.winfo_width(), split_width)
        self.assertEqual(self.right_pixel(), (60, 0, 0))

    def test_reset_settings_keeps_frame_plan_and_rendered_preview(self):
        panel = self.panel
        self.assertTrue(panel.add_range(1, 3))
        self.assertTrue(panel.set_frame_override(4, 2))
        analysis = panel.analysis
        with patch('reference_render.render_reference_preview', return_value=self.preview_file()):
            panel.preview_button.invoke()
            self.wait(lambda: panel._displayed_after and not panel.busy)
        preview = panel._preview
        panel.difference_button.invoke()
        panel.difference_gain.set('4×')
        panel.view_box.current(2)
        panel._view_changed()
        panel.only_issues.set(True)
        panel._fill_table()
        panel.set_table_expanded(False)

        panel.tools_menu.invoke(panel.tool_items['defaults'])
        self.wait(lambda: not panel._displayed_after)
        self.assertIs(panel.analysis, analysis)
        self.assertIs(panel._preview, preview)
        self.assertTrue(preview.path.is_file())
        self.assertEqual(panel._position, 4)
        self.assertEqual(panel._override_edits, {4: 2})
        self.assertTrue(panel._added_range_at(2))
        self.assertTrue(panel.table_expanded)
        self.assertFalse(panel.only_issues.get())
        self.assertEqual((panel.view_box.current(), panel.difference.get(), panel.difference_gain.get()),
                         (0, False, '1×'))
        self.toggle_after(True)
        self.wait(lambda: panel._displayed_after)

    def test_generated_frames_before_after_difference_and_locked_manual_edit(self):
        panel = self.panel
        panel.seek(2)
        self.wait(lambda: panel._displayed == (2,1))
        self.assertEqual(self.right_pixel(), (10,1,0))
        with patch('reference_render.render_reference_preview', return_value=self.preview_file()) as render:
            panel.preview_button.invoke()
            self.wait(lambda: panel._displayed_after and not panel.busy)
            self.assertIs(render.call_args.args[0], self.result)
            self.assertEqual(self.right_pixel(), (60,2,0))
            self.assertEqual(panel.table.selection(), ('2',))
            panel.difference_button.invoke()
            self.assertEqual(self.right_pixel(), (20,0,0))
            self.toggle_after(False)
            self.wait(lambda: not panel._displayed_after and self.right_pixel() == (30,1,0))
            self.assertEqual(panel._position, 2)
            panel.difference_button.invoke()
            self.pending.add((self.rendered, 2))
            self.toggle_after(True)
            self.assertFalse(panel._displayed_after)
            # A late decoder for the old target must not replace a newer seek.
            panel.seek(4)
            self.wait(lambda: panel._displayed == (4,3) and panel._displayed_after)
            self.assertEqual(self.right_pixel(), (120,4,0))
            self.assertEqual(panel._images['source'].getpixel((0,0)), (80,4,0))
            self.pending.clear()
            self.toggle_after(False)
            self.toggle_after(True)
            panel.anchor_toggle.invoke()
            panel.seek(2)
            self.wait(lambda: panel._displayed == (4,2) and not panel._displayed_after)
            self.assertEqual(self.right_pixel(), (20,2,0))
            panel.anchor_toggle.invoke()
            self.wait(lambda: panel._displayed == (4,3) and panel._displayed_after)
            self.assertEqual(render.call_count, 1)
            panel.set_inputs(change='')
            self.assertFalse(self.rendered.exists())
            self.assertIsNone(panel._preview)
            self.assertFalse(panel.preview_after.get())
            self.assertFalse(panel._photos)

    def test_cancel_discards_even_an_already_completed_worker_artifact(self):
        panel = self.panel
        started = threading.Event()
        def race(analysis, *, progress, cancel, **kwargs):
            started.set()
            cancel.wait(3)
            return self.preview_file()
        with patch('reference_render.render_reference_preview', side_effect=race):
            panel.preview_button.invoke()
            self.assertTrue(started.wait(2))
            panel.cancel_button.invoke()
            self.wait(lambda: not panel.busy)
            self.assertIsNone(panel._preview)
            self.assertFalse(self.rendered.exists())
            self.assertFalse(panel.preview_after.get())
            self.assertEqual(self.right_pixel(), (10,1,0))

    def test_shared_quality_for_preview_and_export_and_invalidation_on_return(self):
        panel = self.panel
        panel.set_frame_override(4, 2)
        panel.set_render_quality('HIGH')
        with patch('sdk_bridge.save_quality') as save:
            panel.quality_box.set('LOW')
            panel.quality_box.event_generate('<<ComboboxSelected>>')
            self.root.update()
            save.assert_called_once_with('LOW')
        with patch('reference_render.render_reference_preview', side_effect=lambda *a, **kw: self.preview_file()) as render:
            panel.preview_button.invoke()
            self.wait(lambda: panel._displayed_after and not panel.busy)
            self.assertEqual(render.call_args.kwargs['quality'], 'LOW')
            first = panel._preview
            panel.set_render_quality('LOW')
            self.assertIs(panel._preview, first)

            panel.on_hide()
            with patch('sdk_bridge.configured_quality', return_value='MEDIUM'):
                panel.on_show()
            self.wait(lambda: panel._displayed == (4, 2))
            self.assertIsNone(panel._preview)
            self.assertFalse(first.path.exists())
            self.assertEqual(panel.quality_box.get(), 'MEDIUM')
            self.assertFalse(panel.preview_after.get())
            self.assertEqual(self.right_pixel(), (20, 2, 0))
            self.assertEqual(panel._override_edits, {4: 2})
            panel.preview_button.invoke()
            self.wait(lambda: panel._displayed_after and not panel.busy)
            self.assertEqual(render.call_args.kwargs['quality'], 'MEDIUM')

        output = self.edit.with_name('export.mov')
        with patch('reference_render.render_reference', return_value={'output': str(output)}) as export:
            panel.render(output)
            self.wait(lambda: not panel.busy)
            self.assertEqual(export.call_args.kwargs['quality'], 'MEDIUM')

    def test_quality_change_discards_preview_completed_before_the_worker_observes_cancel(self):
        panel = self.panel
        panel.set_render_quality('LOW')
        started = threading.Event()
        def render(analysis, *, progress, cancel, quality):
            artifact = self.preview_file()
            started.set()
            cancel.wait(3)
            return artifact
        with patch('reference_render.render_reference_preview', side_effect=render):
            panel.preview_button.invoke()
            self.assertTrue(started.wait(2))
            panel.set_render_quality('HIGH')
            self.wait(lambda: not panel.busy)
            self.assertFalse(self.rendered.exists())
            self.assertIsNone(panel._preview)
            self.assertTrue(panel.preview_button.instate(['!disabled']))
