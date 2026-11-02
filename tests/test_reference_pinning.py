"""Cover the two-input pair picker before an automatic map exists."""
from pathlib import Path
import json
import shutil
import subprocess
import tempfile
import threading
import time
import tkinter as tk
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image, ImageTk

from reference_match import ReferenceReview
from reference_panel import ReferencePanel


class FirstManualMatch(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(str(exc))
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        self.panel = ReferencePanel(self.root)
        self.panel.pack()
        self.root.update_idletasks()
        self.addCleanup(self.panel.close)
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.source = Path(directory.name) / 'source.mp4'
        self.change = Path(directory.name) / 'edit.mp4'
        self.source.touch()
        self.change.touch()
        self.panel.set_inputs(source=self.source, change=self.change)

    def wait(self, condition):
        deadline = time.monotonic() + 10
        while not condition() and time.monotonic() < deadline:
            self.root.update()
            time.sleep(.005)
        self.assertTrue(condition(), self.panel.status.get())

    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'FFmpeg required')
    def test_first_pin_on_flat_footage_drives_real_analysis_and_previews(self):
        subprocess.run(['ffmpeg','-nostdin','-v','error','-y','-f','lavfi',
                        '-i','color=gray:s=80x60:r=24','-frames:v','8','-c:v','mpeg4',str(self.source)],
                       check=True, capture_output=True)
        shutil.copyfile(self.source, self.change)
        panel = self.panel
        with patch('reference_panel.messagebox.showerror') as errors:
            panel.anchor_toggle.invoke()
            self.wait(lambda: not panel.busy and panel._displayed == (1,1))
            self.assertTrue(panel._editing_pair)
            self.assertIsNone(panel.analysis)
            self.assertFalse(panel.table.get_children())
            graph = panel.confidence_graph
            self.assertFalse(graph._rows)
            self.assertIsNone(graph._candidate_costs)
            self.assertFalse(graph.canvas.find_withtag('confidence_trace'))
            self.assertFalse(graph.canvas.find_withtag('cost_trace'))
            self.assertEqual(int(panel.change_spin.cget('to')), 8)
            self.assertTrue(panel.set_reference_frame(4))
            self.assertEqual(graph._value['text'], '')
            panel.next.invoke()
            self.wait(lambda: panel._displayed == (4,2))
            panel.scale.set(5)
            self.wait(lambda: panel._displayed == (4,5))
            panel._step_key(-1)
            self.wait(lambda: panel._displayed == (4,4))
            panel.play.invoke()
            self.wait(lambda: panel._position >= 6)
            self.assertTrue(panel._editing_pair)
            self.assertEqual(panel._displayed[0], 4)
            panel.seek(5)
            panel.difference_button.invoke()
            self.wait(lambda: panel._displayed == (4,5))
            self.assertIsNotNone(panel._photos.get('change'))
            self.assertTrue(panel.export.instate(['disabled']))
            panel.pin_button.invoke()
            self.wait(lambda: panel.analysis is not None and panel._displayed == (4,5))
            self.assertEqual(panel.analysis['manual_anchors'], [{'source_frame':4,'change_frame':5}])
            self.assertEqual(panel._position, 4)
            self.assertTrue(panel.analysis['rows'][3]['manual'])
            self.assertEqual(len(panel.confidence_graph._rows), 8)
            self.assertIsNone(panel.confidence_graph._candidate_costs)
            panel.anchor_toggle.invoke()
            panel.pair_change.set('6')
            self.wait(lambda: panel._displayed == (4,6))
            panel.on_hide()
            self.assertFalse(panel._editing_pair)
            panel.on_show()
            self.wait(lambda: panel._displayed == (4,5))
            self.assertFalse(errors.called, errors.call_args_list)
            panel.set_inputs(change='')
            self.assertFalse(panel._streams)
            self.assertIsNone(panel._prepared)
            self.assertFalse(panel.table.get_children())

    def test_manual_transport_locks_source_while_the_results_stay_on_the_source_timeline(self):
        class Stream:
            def __init__(self, path, meta):
                self.factor = 20 if Path(path) == self_source else 10
            def stop(self):
                pass
            def frame(self, number):
                return Image.new('RGB', (8,6), (self.factor*number,0,0))
        self_source = self.source
        panel = self.panel
        meta = dict(width=8,height=6,fps=24)
        result = dict(source_path=str(self.source), change_path=str(self.change), source_meta=meta, change_meta=meta,
            source_frames=10, change_frames=11,
            rows=[dict(source_frame=i, change_frame=1 if i == 1 else i+1, status='matched',
                       review=False, confidence=i/12) for i in range(1,11)],
            change_rows=[dict(source_frame=max(1, j-1), change_frame=j, review=False, confidence=1,
                              event={1: 'start', 2: 'hold_candidate'}.get(j, 'normal'))
                         for j in range(1,12)])
        with patch('reference_match.analyze_reference', return_value=result), patch('reference_panel._PreviewStream', Stream):
            panel.start()
            self.wait(lambda: panel._displayed == (1,1))
            panel.seek(2)
            graph = panel.confidence_graph
            nav = graph.navigation
            nav.zoom(2, anchor=2)
            nav._y_value.set('.8')
            nav._apply_y()
            self.assertTrue(nav.view.zoomed)
            view = nav.view.fractions()
            title, legend = graph._title['text'], graph._legend['text']
            panel.anchor_toggle.invoke()
            self.root.update_idletasks()
            self.assertEqual((graph._title['text'], graph._legend['text']), (title, legend))
            self.assertEqual(nav.winfo_manager(), 'pack')
            self.assertEqual((nav.view.fractions(), nav.y_limit), (view, .8))
            self.assertEqual(graph._rows, result['rows'])
            self.assertTrue(graph.canvas.find_withtag('confidence_trace'))
            self.assertFalse(graph.canvas.find_withtag('cost_trace'))
            self.assertEqual(graph._position, 2)
            self.assertEqual(panel._position, 3)
            self.assertEqual(panel.table.get_children(), tuple(str(i) for i in range(1,11)))
            self.assertEqual(str(panel.table['selectmode']), 'none')
            self.assertEqual(panel.table.selection(), ('2',))
            panel.difference_button.invoke()
            panel.seek(5)
            self.wait(lambda: panel._displayed == (2,5))
            self.assertEqual(ImageTk.getimage(panel._photos['source']).getpixel((0,0))[:3], (40,0,0))
            self.assertEqual(ImageTk.getimage(panel._photos['change']).getpixel((0,0))[:3], (10,0,0))
            graph._seek(9)  # Clicking or arrowing on the graph cannot retarget a locked pair.
            self.assertEqual(panel._displayed, (2,5))
            self.assertEqual(graph._position, 2)
            self.assertEqual(nav.view.fractions(), view)
            panel.seek(4)
            self.wait(lambda: panel._displayed == (2,4))
            self.assertEqual(ImageTk.getimage(panel._photos['change']).getpixel((0,0))[:3], (0,0,0))
            with patch('reference_panel.simpledialog.askinteger', return_value=3):
                panel.reference_button.invoke()
            self.assertEqual(panel._displayed, (3,4))
            self.assertEqual(graph._position, 3)
            panel.anchor_toggle.invoke()
            self.assertEqual(panel.table.selection(), ('2',))
            self.assertEqual(panel._displayed, (2,3))
            self.assertEqual(graph._position, 2)
            self.assertEqual((nav.view.fractions(), nav.y_limit), (view, .8))
            graph._seek(4)
            self.wait(lambda: panel._displayed == (4,5))

    def test_cancel_during_probe_cannot_open_a_stale_editor(self):
        began = threading.Event()
        def prepare(source, change, *, progress, cancel):
            began.set()
            cancel.wait(5)
            return {}  # Model completion racing the cancellation.
        with patch('reference_match.prepare_candidate_costs', side_effect=prepare):
            self.panel.anchor_toggle.invoke()
            self.assertTrue(began.wait(1))
            self.panel.cancel()
            self.wait(lambda: not self.panel.busy)
        self.assertIsNone(self.panel._prepared)
        self.assertFalse(self.panel._editing_pair)
        self.assertFalse(self.panel._streams)

    def test_output_frame_chooser_uses_cached_scores_and_keeps_them_out_of_json(self):
        # Source 2 has a manually pinned Edit 15, but its original automatic choice was Edit 12.
        from i18n import language, set_language
        meta = dict(width=8, height=6, fps=24)
        rows = [dict(source_frame=i, change_frame=j, status='matched', review=False,
                     confidence=None if i == 2 else 1., manual=(i==2))
                for i,j in ((1,1),(2,15),(3,26))]
        changes = [dict(source_frame=1 if j < 15 else 2 if j < 26 else 3, change_frame=j,
                        event='normal', review=False, confidence=.8) for j in range(1,33)]
        report = dict(source_path=str(self.source), change_path=str(self.change),
                      source_frames=3, change_frames=32, source_meta=meta, change_meta=meta,
                      rows=rows, change_rows=changes, manual_anchors=[{'source_frame':2,'change_frame':15}])
        matrix = np.arange(96, dtype=float).reshape(32,3) / 64
        matrix[9,1] = np.nan
        review = ReferenceReview(report, matrix, (1,12,26))
        panel, graph = self.panel, self.panel.confidence_graph
        self.addCleanup(set_language, language(), None, False)
        with patch('reference_match.analyze_reference', return_value=review) as analyze, \
                patch('reference_panel._PreviewStream') as stream, \
                patch.object(graph, 'set_candidates', wraps=graph.set_candidates) as bind:
            stream.return_value.frame.side_effect = lambda n: Image.new('RGB', (8,6), (n,0,0))
            panel.start()
            self.wait(lambda: panel.analysis is review)
            self.assertTrue(analyze.call_args.kwargs['review_cache'])
            panel.seek(2)
            panel.override_button.invoke()
            self.assertEqual(bind.call_count, 1)
            np.testing.assert_equal(graph._candidate_costs, matrix[:,1])
            self.assertEqual((graph._candidate_source, graph._position, graph._automatic_frame), (2,15,12))
            first = graph._first_candidate
            graph._seek(16)
            self.wait(lambda: panel._displayed == (2,16))
            panel.only_issues.set(True)
            panel._fill_table()
            set_language('en', self.root, persist=False)
            panel.refresh_language()
            self.assertEqual(bind.call_count, 1)
            self.assertEqual(graph._first_candidate, first)
            panel.anchor_toggle.invoke()  # Close, select a different source, and choose its output frame.
            panel.seek(3)
            panel.override_button.invoke()
            self.assertEqual(bind.call_count, 2)
            np.testing.assert_equal(graph._candidate_costs, matrix[:,2])
            self.assertEqual((graph._candidate_source, graph._position, graph._automatic_frame), (3,26,26))
            panel.anchor_toggle.invoke()
            self.assertIsNone(graph._candidate_costs)
            self.assertEqual(graph._rows, rows)
            path = self.source.with_suffix('.json')
            with patch('reference_panel.filedialog.asksaveasfilename', return_value=str(path)):
                panel.save_report()
            self.assertEqual(json.loads(path.read_text(encoding='utf-8')), report)
