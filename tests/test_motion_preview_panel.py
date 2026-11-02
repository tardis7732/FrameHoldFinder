"""Late preview results must not leak files or replace a changed input."""
import shutil
import subprocess
import tempfile
import threading
import time
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import patch

import hold_core
from motion_app import App
from preview_file import RenderedPreview


class MotionPreviewPanelTests(unittest.TestCase):
    def test_discard_finished_worker_result_on_cancel_change_and_close(self):
        ffmpeg = shutil.which('ffmpeg')
        if not ffmpeg:
            self.skipTest('FFmpeg is required for the motion player')
        try:
            root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(str(exc))
        root.withdraw()
        self.enterContext(patch.object(App, 'check_sdk'))
        preference = self.enterContext(patch('sdk_bridge.configured_quality', return_value='MEDIUM'))
        app = App(root)
        self.addCleanup(lambda: app.close() if not app.closing else None)
        folder = self.enterContext(tempfile.TemporaryDirectory())
        source = Path(folder) / 'clip.mkv'
        subprocess.run([ffmpeg, '-nostdin', '-v', 'error', '-f', 'lavfi', '-i',
                        'testsrc2=size=32x24:rate=24', '-frames:v', '8', '-c:v', 'ffv1', str(source)],
                       check=True, creationflags=hold_core.FLAGS)
        second = source.with_name('second.mkv')
        shutil.copyfile(source, second)

        def wait(condition):
            deadline = time.monotonic() + 6
            while not condition() and time.monotonic() < deadline:
                root.update()
                time.sleep(.01)
            self.assertTrue(condition())

        app.path.set(str(source))
        wait(lambda: app.player.current_frame == 1)
        app.sdk_ready = True
        app.add_manual_range(1, 4)
        for action in ('quality', 'cancel', 'change', 'close'):
            started, finish = threading.Event(), threading.Event()
            artifact = RenderedPreview('.mkv')
            self.addCleanup(artifact.close)
            shutil.copyfile(source, artifact.path)

            expected_quality = preference.return_value
            def late_result(analysis, runs, *, progress, cancel, quality):
                self.assertEqual(quality, expected_quality)
                started.set()
                if not finish.wait(6):
                    raise TimeoutError('Test did not release the render worker')
                return artifact

            with patch('motion_preview.render_motion_preview', side_effect=late_result):
                app.preview_button.invoke()
                wait(started.is_set)
                root.update_idletasks()
                if action == 'quality':
                    preference.return_value = 'LOW'
                    app.sdk_applied(None, None)
                    self.assertTrue(app.sdk_ready)
                elif action == 'cancel':
                    app.cancel()
                elif action == 'change':
                    app.path.set(str(second))
                else:
                    app.close()
                finish.set()
                wait(lambda: not app.busy)
            self.assertFalse(artifact.path.exists(), action)
            self.assertIsNone(app._preview, action)
            if action == 'change':
                wait(lambda: app.player.path == str(second) and app.player.current_frame == 1)
                app.add_manual_range(1, 4)
