"""Override writes use exact edit indices without changing interpolation inputs."""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

from sdk_worker import _frame_overrides, render


class OverrideInputTests(unittest.TestCase):
    def test_worker_rejects_invalid_or_repeated_output_indices(self):
        job = dict(lookup=[1, 2], change_frames=4)
        for slot, edit in ((True, 2), (0, 1), (3, 1), (2, 5), (2, 1.1)):
            with self.subTest(slot=slot, edit=edit), self.assertRaises(ValueError):
                _frame_overrides(dict(job, frame_overrides=[dict(source_frame=slot, change_frame=edit)]), 2)
        pair = dict(source_frame=2, change_frame=4)
        with self.assertRaises(ValueError):
            _frame_overrides(dict(job, frame_overrides=[pair, pair]), 2)


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'FFmpeg required')
class OverrideWorkerTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        p = Path(folder.name)
        self.source = p / 'edit.mp4'
        self.output = p / 'result.mp4'
        levels = [20, 55, 85, 125, 180, 230]
        subprocess.run(['ffmpeg', '-v', 'error', '-nostdin', '-f', 'rawvideo', '-pixel_format', 'rgb24',
                        '-video_size', '96x64', '-framerate', '24', '-i', 'pipe:0', '-an', '-c:v', 'libx264',
                        '-pix_fmt', 'yuv420p', '-profile:v', 'high', '-crf', '12', str(self.source)],
                       input=b''.join(bytes([level]) * (96*64*3) for level in levels),
                       capture_output=True, check=True)
        self.job = dict(source=str(self.source), reference_path=str(self.source), output=str(self.output),
                        width=96, height=64, frames=6, change_frames=6, fps=24, fps_fraction='24',
                        ffmpeg='ffmpeg', ffprobe='ffprobe', log=str(p/'log'), cancel_file=str(p/'cancel'),
                        lookup=list(range(1, 7)), runs=[])

    def levels(self):
        pixels = subprocess.check_output(['ffmpeg', '-v', 'error', '-i', str(self.output),
                                          '-map', '0:v:0', '-an', '-pix_fmt', 'rgb24', '-f', 'rawvideo', 'pipe:1'])
        return np.frombuffer(pixels, dtype=np.uint8).reshape(6, -1).mean(axis=1)

    def test_backward_duplicate_and_beyond_last_lookup_without_gpu(self):
        overrides = [dict(source_frame=s, change_frame=c) for s, c in ((2, 6), (5, 1), (6, 6))]
        job = dict(self.job, lookup=[1, 1, 2, 3, 4, 5], runs=[dict(start=1, end=2, generated=1)],
                   frame_overrides=overrides)
        with patch.dict(sys.modules, {'cupy': None, 'nvvfx': None}):
            result = render(job, report=lambda *args, **kwargs: None)
        self.assertEqual((result['frames'], result['generated']), (6, 0))
        self.assertEqual(result['replacements'], [])
        np.testing.assert_allclose(self.levels(), [20, 230, 55, 85, 20, 230], atol=4)

    def test_override_not_present_in_edit_preserves_previous_output(self):
        self.output.write_bytes(b'previous output')
        job = dict(self.job, change_frames=7, frame_overrides=[dict(source_frame=2, change_frame=7)])
        with self.assertRaisesRegex(ValueError, 'edit frame 7'):
            render(job, report=lambda *args, **kwargs: None)
        self.assertEqual(self.output.read_bytes(), b'previous output')
        self.assertEqual(list(self.output.parent.glob('*.partial.mp4')), [])

    def render_with_scalar_interpolation(self, job):
        calls = []
        class ScalarGenerator:
            Mode = SimpleNamespace(HIGH='HIGH')
            def __init__(self, **kwargs): pass
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def load(self): pass
            def run_at_timestep(self, left, right, fraction, **kwargs):
                calls.append(fraction)
                values = np.rint((left & 1023)*(1-fraction) + (right & 1023)*fraction).astype(np.uint32)
                return SimpleNamespace(image=values | values<<10 | values<<20)
        cp = SimpleNamespace(asarray=np.asarray, asnumpy=np.asarray, from_dlpack=np.asarray,
                             array_equal=np.array_equal, cuda=SimpleNamespace(get_current_stream=lambda:
                             SimpleNamespace(synchronize=lambda: None)))
        sdk = SimpleNamespace(VideoFrameGeneration=ScalarGenerator, ImageEncoding=SimpleNamespace(RGB10A2=1))
        with patch.dict(sys.modules, {'cupy': cp, 'nvvfx': sdk}):
            result = render(job, report=lambda *args, **kwargs: None)
        return result, calls

    def test_overridden_anchor_and_middle_leave_neighbor_fractions_untouched(self):
        overrides = [dict(source_frame=1, change_frame=6), dict(source_frame=3, change_frame=2)]
        job = dict(self.job, runs=[dict(start=1, end=4, generated=3)], frame_overrides=overrides)
        result, calls = self.render_with_scalar_interpolation(job)
        self.assertEqual(calls, [.25, .75])
        self.assertEqual(result['generated'], 2)
        self.assertEqual(result['replacements'][0]['targets'], [2, 4])
        # Frames 2 and 4 interpolate between original levels 20 and 180,
        # although output frame 1 itself is replaced with level 230.
        np.testing.assert_allclose(self.levels(), [230, 60, 55, 140, 180, 230], atol=4)

    def test_explicit_edit_images_for_one_run_leave_adjacent_auto_anchors_alone(self):
        job = dict(self.job, runs=[dict(start=1, end=2, generated=1, left_change=2, right_change=4),
                                   dict(start=3, end=5, generated=2)],
                   range_endpoints=[dict(source_frame=1, change_frame=2), dict(source_frame=3, change_frame=4)],
                   frame_overrides=[dict(source_frame=3, change_frame=1)])
        result, calls = self.render_with_scalar_interpolation(job)
        self.assertEqual(calls, [.5, 1/3, 2/3])
        self.assertEqual(result['replacements'][0]['edit_anchors'], [2, 4])
        # S2 uses E2/E4; S3's output-only override wins on screen; S4/S5
        # still use the original automatic references E3/E6.
        np.testing.assert_allclose(self.levels(), [55, 90, 20, 133, 182, 230], atol=4)
