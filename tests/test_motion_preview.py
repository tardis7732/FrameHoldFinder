from pathlib import Path
import threading
import unittest
from unittest.mock import patch

from hold_core import Cancelled
from motion_preview import render_motion_preview


class MotionPreviewTests(unittest.TestCase):
    def test_reuses_source_render_request_and_frame_progress_in_private_folder(self):
        analysis = dict(path='take.MOV', frames=8, source_stat={'size': 321},
                        metadata=dict(width=256, height=128, fps=24, fps_fraction='24/1'))
        runs = [dict(start=2, end=3, generated=1, targets=[3])]
        cancel = threading.Event()
        updates = []
        def run(request, progress, stop):
            self.assertIs(stop, cancel)
            self.assertEqual((request['source'], request['quality'], request['format_mode']),
                             ('take.MOV', 'MEDIUM', 'source'))
            self.assertEqual((request['runs'], request['frames'], request['fps_fraction']), (runs, 8, '24/1'))
            self.assertTrue(request['preserve_frame_count'])
            Path(request['output']).write_bytes(b'preview')
            progress(8, 8)

        with patch('sdk_bridge.run_render_job', side_effect=run):
            movie = render_motion_preview(analysis, runs, quality='MEDIUM',
                                          progress=lambda f, n: updates.append((f, n)), cancel=cancel)
        self.addCleanup(movie.close)
        self.assertEqual(updates, [(8, 8)])
        self.assertEqual(movie.path.suffix, '.mov')
        self.assertEqual(list(movie.path.parent.iterdir()), [movie.path])
        movie.close()
        self.assertFalse(movie.path.parent.exists())

    def test_pre_cancelled_request_never_starts_worker(self):
        cancel = threading.Event()
        cancel.set()
        with patch('sdk_bridge.repair') as repair, self.assertRaises(Cancelled):
            render_motion_preview({'path': 'input.mp4'}, [], cancel=cancel)
        repair.assert_not_called()
