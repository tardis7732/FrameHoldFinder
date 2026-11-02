from copy import deepcopy
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import unittest
from unittest import mock

import numpy as np

import hold_core
from reference_render import (ManualRangeError, describe_reference_frames, plan_reference,
                              render_reference, validate_reference_range)
from sdk_worker import replacement_runs


def analysis_rows(lookup, statuses):
    return [{'source_frame': f, 'change_frame': mapped, 'status': status}
            for f, (mapped, status) in enumerate(zip(lookup, statuses), 1)]


class GapPlanTests(unittest.TestCase):
    def test_manual_endpoints_split_an_automatic_gap_and_stay_mapped(self):
        rows = analysis_rows([1, 2, 4, 4, 4, 7, 8, 9],
                             ['matched', 'held', 'missing', 'review', 'matched', 'held', 'missing', 'matched'])
        analysis = dict(source_frames=8, change_frames=9, rows=rows, manual_ranges=[dict(left=3, right=6)])
        original = deepcopy(analysis)
        plan = plan_reference(analysis)
        self.assertEqual(replacement_runs(plan['runs'], 8), [(1, 2, 1), (3, 5, 2), (6, 7, 1)])
        self.assertEqual(plan['lookup'], [1, 2, 4, 4, 4, 7, 8, 9])
        self.assertFalse(plan['unresolved_frames'])
        actions = describe_reference_frames(analysis)
        for i, edit in ((3, 4), (6, 7)):
            self.assertEqual(actions[i - 1], dict(kind='use', source_frame=i, change_frame=edit, manual_anchor=True))
        for i in (4, 5):
            self.assertEqual(actions[i - 1], dict(kind='generate', source_frame=i, left_source=3, right_source=6,
                             left_change=4, right_change=7, step=i - 3, count=2, manual=True))
        self.assertNotIn('manual', actions[1])
        self.assertEqual(analysis, original)
        self.assertEqual(describe_reference_frames({**analysis, 'manual_ranges': []})[3]['kind'], 'use')

    def test_manual_ranges_can_share_one_kept_endpoint(self):
        rows = analysis_rows(range(1, 9), ['matched'] * 8)
        rows[3]['manual'] = True
        analysis = dict(source_frames=8, change_frames=8, rows=rows, manual_ranges=[dict(left=4, right=8)])
        self.assertIsNone(validate_reference_range(analysis, 1, 4))
        self.assertEqual(len(analysis['manual_ranges']), 1)
        analysis['manual_ranges'].append(dict(left=1, right=4))
        self.assertEqual(replacement_runs(plan_reference(analysis)['runs'], 8), [(1, 3, 2), (4, 7, 3)])
        self.assertEqual(describe_reference_frames(analysis)[3], dict(kind='use', source_frame=4,
                         change_frame=4, manual_anchor=True))

    def test_invalid_manual_ranges_are_rejected_by_both_editor_and_renderer(self):
        rows = analysis_rows([1, 2, 2, 2, 3, 5, 6, 7], ['matched'] * 8)
        analysis = dict(source_frames=8, change_frames=7, rows=rows)
        inputs = [
            (True, 3, 'bounds'), ('1', 3, 'bounds'), (2, 2, 'bounds'), (1, 2, 'bounds'),
            (4, 3, 'bounds'), (0, 4, 'bounds'), (1, 9, 'bounds'), (2, 4, 'edit_order'),
            (1, 5, 'pinned'), (4, 7, 'overlap'), (5, 8, 'overlap'),
        ]
        analysis['manual_ranges'] = [dict(left=5, right=8)]
        analysis['manual_anchors'] = [dict(source_frame=2, change_frame=2)]
        for left, right, reason in inputs:
            with self.subTest(left=left, right=right):
                with self.assertRaises(ManualRangeError) as error:
                    validate_reference_range(analysis, left, right)
                self.assertEqual(error.exception.code, reason)
                with self.assertRaises(ManualRangeError) as error:
                    plan_reference({**analysis, 'manual_ranges': [*analysis['manual_ranges'], dict(left=left, right=right)]})
                self.assertEqual(error.exception.code, reason)

    def test_frame_descriptions_follow_mapped_endpoints_and_sdk_steps(self):
        rows = analysis_rows([1, 2, 2, 6, 7, 9, 9],
                             ['missing', 'review', 'missing', 'held', 'matched', 'matched', 'held'])
        rows[5]['manual'] = True
        actions = describe_reference_frames(dict(source_frames=7, change_frames=9, rows=rows))
        self.assertEqual(actions, [
            {'kind': 'fallback', 'source_frame': 1, 'change_frame': 1},
            {'kind': 'use', 'source_frame': 2, 'change_frame': 2},
            {'kind': 'generate', 'source_frame': 3, 'left_source': 2, 'right_source': 5,
             'left_change': 2, 'right_change': 7, 'step': 1, 'count': 2},
            {'kind': 'generate', 'source_frame': 4, 'left_source': 2, 'right_source': 5,
             'left_change': 2, 'right_change': 7, 'step': 2, 'count': 2},
            {'kind': 'use', 'source_frame': 5, 'change_frame': 7},
            {'kind': 'use', 'source_frame': 6, 'change_frame': 9},
            {'kind': 'fallback', 'source_frame': 7, 'change_frame': 9},
        ])

    def test_bounded_gap_uses_original_slots_and_edges_stay_mapped(self):
        lookup = [1, 1, 2, 2, 2, 3, 4, 5, 5]
        statuses = ['missing', 'matched', 'matched', 'missing', 'held', 'review', 'matched', 'matched', 'held']
        p = plan_reference(dict(source_frames=9, change_frames=5, rows=analysis_rows(lookup, statuses)))
        self.assertEqual(p['runs'], [{'start': 3, 'end': 5, 'generated': 2}])
        self.assertEqual(p['unresolved_frames'], [1, 9])
        self.assertEqual(p['lookup'], lookup)

    def test_same_mapped_endpoints_remain_unresolved(self):
        p = plan_reference(dict(source_frames=3, change_frames=2, rows=analysis_rows(
            [1, 1, 1], ['matched', 'missing', 'matched'])))
        self.assertEqual(p['unresolved_frames'], [2])
        self.assertFalse(p['runs'])

    def test_reject_reversed_and_incomplete_lookup(self):
        for lookup in ([2, 1], [1, 3], [1]):
            with self.subTest(lookup=lookup), self.assertRaises(ValueError):
                plan_reference(dict(source_frames=2, change_frames=2, rows=analysis_rows(
                    lookup, ['matched'] * len(lookup))))


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'FFmpeg required')
class ReferenceExportTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        folder = Path(directory.name)
        self.original = folder/'original.mp4'
        self.edited = folder/'edit.mp4'
        self.output = folder/'aligned.mp4'

        def make_clip(path, fps, levels, frequency):
            duration = len(levels)/fps
            args = ['ffmpeg','-loglevel','error','-nostdin','-f','rawvideo','-pix_fmt','rgb24',
                    '-s','96x64','-framerate',str(fps),'-i','pipe:0','-f','lavfi','-i',
                    f'sine=f={frequency}:r=48000:d={duration}','-c:v','libx264','-profile:v','high',
                    '-crf','15','-pix_fmt','yuv420p','-c:a','aac',str(path)]
            pixels = b''.join(bytes([n]) * (96*64*3) for n in levels)
            subprocess.run(args,input=pixels,capture_output=True,check=True)
        make_clip(self.original, 24, [35]*6, 440)
        make_clip(self.edited, 30, [20, 70, 125, 175, 230], 880)
        self.analysis = dict(source_path=str(self.original), change_path=str(self.edited),
                             source_meta=hold_core.probe(self.original), change_meta=hold_core.probe(self.edited),
                             source_frames=6, change_frames=5, rows=analysis_rows(
                                 [1, 2, 2, 4, 5, 5], ['matched', 'matched', 'review', 'matched', 'matched', 'missing']))

    def test_cpu_only_retime_uses_edit_images_and_original_timing_audio(self):
        def audio(path):
            return subprocess.check_output(['ffmpeg','-loglevel','error','-i',str(path),'-map','0:a:0',
                                            '-c:a','copy','-f','hash','-hash','sha256','pipe:1'])
        with mock.patch('sdk_bridge.runtime',side_effect=AssertionError('no SDK needed')):
            result = render_reference(self.analysis,self.output)
        self.assertEqual(result['generated'], 0)
        self.assertEqual(result['unresolved_frames'], [6])
        self.assertEqual(result['frames'], 6)
        self.assertEqual(result['fps_fraction'], '24')
        self.assertEqual(audio(self.output), audio(self.original))
        self.assertNotEqual(audio(self.output), audio(self.edited))
        decoded = subprocess.check_output(['ffmpeg','-loglevel','error','-i',str(self.output),
            '-map','0:v:0','-an','-pix_fmt','rgb24','-f','rawvideo','pipe:1'])
        levels = np.frombuffer(decoded,np.uint8).reshape(6,-1).mean(axis=1)
        np.testing.assert_allclose(levels, [20,70,70,175,230,230], atol=4)

    def test_cancel_preserves_previous_output_and_cleans_partial(self):
        self.output.write_bytes(b'previous movie')
        cancel = threading.Event()
        with self.assertRaises(hold_core.Cancelled):
            render_reference(self.analysis, self.output, progress=lambda _: cancel.set(),cancel=cancel)
        self.assertEqual(self.output.read_bytes(), b'previous movie')
        self.assertEqual(list(self.output.parent.glob('*.partial.mp4')), [])

    def test_rejects_both_input_paths_before_rendering(self):
        for path in (self.original,self.edited):
            with self.subTest(path=path), self.assertRaises(ValueError):
                render_reference(self.analysis,path)
