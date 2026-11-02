"""The player switches clips without changing the source timeline or reusing stale pixels."""
import shutil
import subprocess
import tempfile
import time
import tkinter as tk
import unittest
from pathlib import Path

import hold_core
from review_player import ReviewPlayer


class SinglePreviewPlayerTest(unittest.TestCase):
    def test_switch_at_the_same_frame_and_release_active_preview(self):
        ffmpeg = shutil.which('ffmpeg')
        if not ffmpeg:
            self.skipTest('FFmpeg is required for decoder checks')
        try:
            root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(str(exc))
        root.withdraw()
        self.addCleanup(root.destroy)
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)

        def movie(name, channel):
            path = Path(folder.name) / name
            pixels = bytearray()
            for n in range(1, 21):
                rgb = bytearray(3)
                rgb[channel] = 10 * n
                pixels.extend(rgb * (24 * 16))
            subprocess.run([ffmpeg, '-v', 'error', '-nostdin', '-f', 'rawvideo', '-pix_fmt', 'rgb24',
                            '-s', '24x16', '-r', '24', '-i', 'pipe:0', '-c:v', 'ffv1',
                            '-pix_fmt', 'bgr0', str(path)], input=pixels, check=True,
                           creationflags=hold_core.FLAGS)
            return path

        source = movie('source.mkv', 0)
        preview = movie('corrected.mkv', 2)
        analysis = {'path':str(source), 'frames':20, 'metadata':{'width':24, 'height':16, 'fps':24}}
        player = ReviewPlayer(root, analysis, embedded=True, show_plot=False)
        self.addCleanup(player.close)

        def wait_pixel(number, color):
            until = time.monotonic() + 5
            while time.monotonic() < until:
                root.update()
                if player.current_frame == number and player.current_bytes and player.current_bytes[:3] == color:
                    return
                time.sleep(.005)
            self.fail(f'Did not display frame {number} in expected clip: {player.info.get()}')

        player.loop.set(False)
        player.speed.set('0.25×')
        player.seek(5)
        wait_pixel(5, bytes((50, 0, 0)))
        player.set_preview_path(preview)
        self.assertIsNone(player.current_bytes)
        self.assertTrue(player.vector_check.instate(['disabled']))
        wait_pixel(5, bytes((0, 0, 50)))
        player.step(1)
        wait_pixel(6, bytes((0, 0, 60)))
        player.set_preview_path(None)
        self.assertTrue(player.vector_check.instate(['!disabled']))
        wait_pixel(6, bytes((60, 0, 0)))
        player.seek(20, play=True)
        player.set_preview_path(preview)
        self.assertTrue(player.playing)
        wait_pixel(20, bytes((0, 0, 200)))
        self.assertIs(player.analysis, analysis)
        self.assertEqual(player.path, str(source))
        self.assertEqual(player.speed.get(), '0.25×')
        self.assertFalse(player.loop.get())
        player.clear()
        preview.unlink()  # Windows must release the temporary file before returning.
