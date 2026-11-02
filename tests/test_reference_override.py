from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from reference_render import (FrameOverrideError, describe_reference_frames, plan_reference,
                              render_reference_preview, validate_frame_override)


def report(lookup, statuses):
    return dict(source_frames=len(lookup), change_frames=8, rows=[
        dict(source_frame=n, change_frame=edit, status=status)
        for n, (edit, status) in enumerate(zip(lookup, statuses), 1)
    ])


def override(source, change):
    return dict(source_frame=source, change_frame=change)


class OutputFrameOverrideTests(unittest.TestCase):
    def test_override_can_go_backwards_and_does_not_affect_neighboring_fills(self):
        analysis = report([1, 2, 3, 4, 5, 6, 6],
                          ['missing', 'matched', 'held', 'held', 'missing', 'matched', 'missing'])
        analysis['rows'][1]['manual'] = True
        base = describe_reference_frames(analysis)
        chosen = [override(7, 1), override(4, 8), override(2, 8), override(1, 7)]
        analysis['frame_overrides'] = chosen
        original = deepcopy(analysis)
        plan = plan_reference(analysis)
        self.assertEqual(plan['lookup'], [1, 2, 3, 4, 5, 6, 6])
        self.assertEqual(plan['runs'], [dict(start=2, end=5, generated=3)])
        self.assertEqual(plan['unresolved_frames'], [])
        self.assertEqual(plan['frame_overrides'], sorted(chosen, key=lambda item: item['source_frame']))
        actions = describe_reference_frames(analysis)
        for item in chosen:
            self.assertEqual(actions[item['source_frame']-1], {'kind': 'override', **item})
        for frame in (3, 5, 6):
            self.assertEqual(actions[frame-1], base[frame-1])
        self.assertEqual(analysis, original)
        self.assertEqual(describe_reference_frames(json.loads(json.dumps(analysis))), actions)
        del analysis['frame_overrides']
        self.assertEqual(describe_reference_frames(analysis), base)

    def test_override_wins_only_at_its_slot_in_a_manual_range(self):
        analysis = report([1, 2, 4, 5, 6, 8], ['matched'] * 6)
        analysis['manual_ranges'] = [dict(left=2, right=5)]
        base = describe_reference_frames(analysis)
        analysis['frame_overrides'] = [override(3, 8), override(5, 2)]
        actions = describe_reference_frames(analysis)
        self.assertEqual(actions[2], dict(kind='override', source_frame=3, change_frame=8))
        self.assertEqual(actions[4], dict(kind='override', source_frame=5, change_frame=2))
        self.assertEqual(actions[3], base[3])  # Still the second third between edits 2 and 6.
        self.assertEqual(actions[1], base[1])
        self.assertEqual(sum(a['kind'] == 'generate' for a in actions), 1)

    def test_all_generated_slots_overridden_preview_requests_cpu_and_exact_edits(self):
        analysis = report([2, 3, 6], ['matched', 'missing', 'matched'])
        analysis['frame_overrides'] = [override(2, 8)]
        with tempfile.TemporaryDirectory() as folder:
            source, edit = (Path(folder) / name for name in ('original.mp4', 'edit.mov'))
            source.touch()
            edit.touch()
            analysis.update(source_path=str(source), change_path=str(edit),
                            source_meta=dict(fps=24), change_meta=dict(width=64, height=64))
            with patch('reference_render.sdk_bridge.run_render_job', return_value={}) as worker:
                preview = render_reference_preview(analysis)
                preview.close()
            job = worker.call_args.args[0]
            self.assertFalse(worker.call_args.kwargs['needs_sdk'])
            self.assertEqual(job['change_frames'], 8)
            self.assertEqual(job['frame_overrides'], analysis['frame_overrides'])
            self.assertEqual(job['lookup'], [2, 3, 6])
            self.assertEqual(job['runs'], [])

    def test_bounds_duplicates_and_external_report_validation(self):
        analysis = report([1, 1], ['matched', 'missing'])
        for source, edit in [(0, 3), (3, 1), (True, 1), (1, 0), (1, 9), (1, 1.0), (1, '8')]:
            with self.subTest(source=source, edit=edit):
                with self.assertRaises(FrameOverrideError) as error:
                    validate_frame_override(analysis, source, edit)
                self.assertEqual(error.exception.code, 'bounds')
                with self.assertRaises(FrameOverrideError):
                    plan_reference({**analysis, 'frame_overrides': [override(source, edit)]})
        self.assertIsNone(validate_frame_override(analysis, 2, 1))
        for malformed in (None, {}, ['1=2'], [{'source_frame': 2}]):
            with self.assertRaises(FrameOverrideError):
                plan_reference({**analysis, 'frame_overrides': malformed})
        with self.assertRaises(FrameOverrideError) as duplicate:
            plan_reference({**analysis, 'frame_overrides': [override(1, 2), override(1, 3)]})
        self.assertEqual(duplicate.exception.code, 'duplicate')
        self.assertEqual(plan_reference(analysis), plan_reference({**analysis, 'frame_overrides': []}))
