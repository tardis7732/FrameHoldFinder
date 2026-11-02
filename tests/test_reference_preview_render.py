from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch

import hold_core
from reference_render import render_reference, render_reference_preview


class PreviewLifetimeTests(unittest.TestCase):
    def test_simultaneous_renders_have_separate_lifetimes(self):
        analysis = {'change_path': 'edit.MOV'}
        progress = lambda fraction: None
        cancel = threading.Event()
        with patch('reference_render.render_reference') as render:
            previews = []
            for _ in range(2):
                preview = render_reference_preview(analysis, progress=progress, cancel=cancel)
                self.addCleanup(preview.close)
                render.assert_called_with(analysis, preview.path, progress=progress, cancel=cancel, quality='HIGH')
                self.assertEqual(preview.path.suffix, '.mov')
                preview.path.touch()
                previews.append(preview)
        first, second = previews
        self.assertNotEqual(first.path.parent, second.path.parent)
        first.close()
        self.assertFalse(first.path.parent.exists())
        self.assertTrue(second.path.exists())

    def test_failed_and_late_cancelled_renders_clean_partial_files(self):
        for cancelled in (False, True):
            with self.subTest(cancelled=cancelled):
                cancel = threading.Event()
                targets = []

                def worker(analysis, output, **kwargs):
                    targets.append(output)
                    output.write_bytes(b'partial')
                    if cancelled:
                        cancel.set()
                    else:
                        raise RuntimeError('SDK error')

                with patch('reference_render.render_reference', side_effect=worker):
                    with self.assertRaises(hold_core.Cancelled if cancelled else RuntimeError):
                        render_reference_preview({'change_path': 'input.mp4'}, cancel=cancel)
                self.assertFalse(targets[0].parent.exists())

    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'FFmpeg required')
    def test_preview_decodes_to_the_same_frames_and_timing_as_export(self):
        with tempfile.TemporaryDirectory() as work:
            folder = Path(work)
            original, edit = folder/'original.mp4', folder/'edit.mp4'
            for rate, count, path in ((24, 4, original), (30, 5, edit)):
                subprocess.run(['ffmpeg', '-v', 'error', '-nostdin', '-f', 'lavfi', '-i',
                                f'testsrc2=size=64x48:rate={rate}', '-frames:v', str(count),
                                '-c:v', 'libx264', '-profile:v', 'high', str(path)], check=True)
            rows = [dict(source_frame=i, change_frame=j, status=status)
                    for i,j,status in ((1,2,'missing'), (2,2,'matched'), (3,4,'review'), (4,5,'matched'))]
            analysis = dict(source_path=str(original), change_path=str(edit), source_frames=4, change_frames=5,
                            source_meta=hold_core.probe(original), change_meta=hold_core.probe(edit), rows=rows)
            output = folder/'export.mp4'
            render_reference(analysis, output)
            preview = render_reference_preview(analysis)
            self.addCleanup(preview.close)
            def framemd5(path):
                return subprocess.check_output(['ffmpeg', '-v', 'error', '-nostdin', '-i', str(path),
                                                '-map', '0:v:0', '-f', 'framemd5', '-'])
            self.assertEqual(framemd5(preview.path), framemd5(output))
            self.assertEqual(hold_core.probe(preview.path)['fps'], 24)
            preview.close()
            self.assertFalse(preview.path.parent.exists())
