import tempfile
import threading
import subprocess
import unittest
import json
from itertools import combinations_with_replacement
from pathlib import Path
from unittest.mock import patch

import numpy as np

import hold_core
import reference_match as match


def pan(count):
    rng = np.random.default_rng(21)
    image = rng.random((match.HEIGHT, match.WIDTH + count*2), dtype=np.float32)
    return [image[:, 2*i:2*i+match.WIDTH].copy() for i in range(count)]


class TimingMatcherTests(unittest.TestCase):
    def test_anchor_validation_accepts_clicks_in_any_order_but_rejects_conflicts(self):
        self.assertEqual(match.validate_anchors(iter([(4, 6), (1, np.int64(2))]), 4, 6), ((1, 2), (4, 6)))
        for bad in ([(0, 1)], [(1, -1)], [(5, 1)], [(1, 7)], [(True, 1)], [(1, 1.2)],
                    [(1, 1), (1, 1)], [(1, 3), (2, 3)], [(1, 3), (2, 1)], ['12'], [1], None):
            with self.subTest(anchors=bad), self.assertRaises(ValueError):
                match.validate_anchors(bad, 4, 6)

    def test_constrained_path_and_confidence_against_exhaustive_search(self):
        rng = np.random.default_rng(27)
        cost = rng.random((6, 5))
        anchors = [(1, 2), (4, 5)]
        path, inverse, _, confidence = match._solve(cost, anchors=anchors)
        # Independently enumerate the objective and each feasible alternative.
        adjacent = []
        for row in cost:
            best = row.argmin()
            adjacent.extend(abs(row[k] - row[best]) for k in (best-1, best+1) if 0 <= k < len(row))
        unit = np.median(adjacent)
        solutions = []
        for p in combinations_with_replacement(range(5), 6):
            if all(p[c-1] == s-1 for s, c in anchors):
                transitions = sum(abs(b-a-1) for a, b in zip(p, p[1:]))
                solutions.append((sum(cost[j,i] for j,i in enumerate(p)) + .15*unit*transitions, p))
        best_score, expected = min(solutions)
        np.testing.assert_array_equal(path, expected)
        for s, c in anchors:
            self.assertEqual(inverse[s-1], c-1)
        for j in range(len(path)):
            alternatives = [v for v,p in solutions if p[j] != expected[j]]
            margin = min(alternatives)-best_score if alternatives else 0.
            self.assertAlmostEqual(confidence[j], margin/(margin+unit), places=12)

    def test_inverse_prefers_exact_anchor_over_identical_repeated_edit(self):
        cost = np.ones((5, 3))
        cost[np.arange(5), [0, 1, 1, 1, 2]] = 0
        auto_path, auto_inverse, _, _ = match._solve(cost)
        anchored_path, anchored_inverse, _, _ = match._solve(cost, anchors=[(2, 4)])
        np.testing.assert_array_equal(anchored_path, auto_path)
        self.assertEqual(auto_inverse[1], 1)
        self.assertEqual(anchored_inverse[1], 3)

    def test_original_with_local_replacement_regrade_holds_and_drops(self):
        # Nuke matcher's changed-object fixture: the changed foreground should
        # not dominate the time estimate from the rest of the image.
        raw = pan(30)
        order = []
        for i in range(30):
            if i not in (10, 11, 23):
                order.extend([i] * (4 if i == 18 else 1))
        rng = np.random.default_rng(7)
        edited = []
        for i in order:
            frame = raw[i] * .8 + .1
            frame[35:65, 65:105] = rng.random((30, 40))
            edited.append(frame)
        src = match._sequence(raw, lambda _: None, None)
        chg = match._sequence(edited, lambda _: None, None)
        cost = match._costs(src, chg, lambda _: None, None)
        path, inverse, missing, confidence = match._solve(cost)
        np.testing.assert_array_equal(path, order)
        np.testing.assert_array_equal(np.flatnonzero(missing), [10, 11, 23])
        self.assertTrue(np.all(np.diff(inverse) >= 0))
        self.assertTrue(np.all((0 <= confidence) & (confidence <= 1)))

    def test_head_tail_and_nearest_placeholders(self):
        path = [2, 3, 4, 4, 4, 5, 8, 9, 10]
        cost = np.ones((len(path), 13))
        cost[np.arange(len(path)), path] = 0
        actual, inverse, missing, _ = match._solve(cost)
        np.testing.assert_array_equal(actual, path)
        np.testing.assert_array_equal(np.flatnonzero(missing), [0, 1, 6, 7, 11, 12])
        np.testing.assert_array_equal(inverse, [0, 0, 0, 1, 2, 5, 5, 6, 6, 7, 8, 8, 8])

    def test_ambiguous_static_scene_is_low_confidence(self):
        *_, confidence = match._solve(np.zeros((10, 10)))
        self.assertTrue(np.all(confidence < .25))
        blank = [np.zeros((match.HEIGHT, match.WIDTH), np.float32)]
        source = match._sequence(blank, lambda _: None, None)
        with self.assertRaisesRegex(ValueError, 'Insufficient textured'):
            match._costs(source, source, lambda _: None, None)

    def test_image_holds_require_new_repeat_against_moving_source(self):
        source = np.array(pan(24))
        changed = source.copy()
        rng = np.random.default_rng(5)
        changed[10:16] = changed[9] + rng.uniform(-.001, .001, size=changed[10:16].shape)
        lookup = np.arange(24)
        missing = np.zeros(24, bool)
        held = match._image_holds(source, changed, lookup, missing)
        np.testing.assert_array_equal(np.flatnonzero(held), np.arange(10, 16))
        self.assertFalse(np.any(match._image_holds(changed, changed, lookup, missing)))
        missing[9:15] = True
        self.assertFalse(np.any(match._image_holds(source, changed, lookup, missing)))

    def test_cancelled_before_opening_input(self):
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(hold_core.Cancelled):
            match.analyze_reference('unused-source', 'unused-change', cancel=cancel)


class ReferenceVideoTests(unittest.TestCase):
    def setUp(self):
        try:
            self.ffmpeg = hold_core.binary('ffmpeg')
            hold_core.binary('ffprobe')
        except RuntimeError as error:
            self.skipTest(str(error))

    def encode(self, path, frames, *, output_args=()):
        pixels = np.rint(np.array(frames) * 255).astype('uint8')
        height, width = pixels.shape[1:3]
        cmd = [self.ffmpeg, '-v', 'error', '-nostdin', '-f', 'rawvideo',
               '-pix_fmt', 'gray', '-s', f'{width}x{height}', '-r', '24',
               '-i', 'pipe:0', '-c:v', 'ffv1', *output_args, '-an', str(path)]
        subprocess.run(cmd, input=pixels.tobytes(), check=True, capture_output=True, creationflags=hold_core.FLAGS)

    def test_width_aligned_match_samples_only_the_shared_vertical_region(self):
        frames = np.array(pan(9))
        order = [0, 1, 2, 4, 5, 7, 8]
        edit = frames[order].repeat(2, axis=2)
        # In the first case only the source has extra rows. The edit is anamorphic,
        # with twice the stored width but the same display width as the source.
        tall_source = np.pad(frames, ((0, 0), (17, 17), (0, 0)), constant_values=1)
        # In the second case a square-pixel 2x edit has 33 extra rows at each end.
        tall_edit = np.pad(edit.repeat(2, axis=1), ((0, 0), (33, 33), (0, 0)))
        cases = [(tall_source, edit, ('-vf', 'setsar=1/2')), (frames, tall_edit, ())]
        for source_frames, edit_frames, edit_args in cases:
            with self.subTest(edit_height=edit_frames.shape[1]), tempfile.TemporaryDirectory() as tmp:
                source, change = Path(tmp)/'source.mkv', Path(tmp)/'edit.mkv'
                self.encode(source, source_frames, output_args=('-pix_fmt', 'yuv420p'))
                self.encode(change, edit_frames, output_args=('-pix_fmt', 'yuv420p', *edit_args))
                result = match.analyze_reference(source, change, review_cache=True)
                self.assertEqual(result['change_to_source'], [i+1 for i in order])
                self.assertEqual([r['source_frame'] for r in result['rows'] if r['status']=='missing'], [4, 7])
                prepared = match.prepare_candidate_costs(source, change)
                np.testing.assert_allclose(result.candidate_costs, prepared.candidate_costs)
                for j, i in enumerate(order):
                    self.assertLess(result.candidate_costs[j, i], .001)

    def test_real_files_and_decoded_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, change = Path(tmp)/'원본.mkv', Path(tmp)/'편집.mkv'
            raw = pan(9)
            self.encode(source, raw)
            order = [0, 1, 2, 2, 4, 6, 7, 8]
            self.encode(change, [raw[i] for i in order])
            progress = []
            result = match.analyze_reference(source, change, progress=progress.append)
            self.assertEqual(result['change_to_source'], [i+1 for i in order])
            self.assertEqual([r['source_frame'] for r in result['rows'] if r['status']=='missing'], [4, 6])
            self.assertEqual([r['change_frame'] for r in result['change_rows'] if r['event']=='hold_candidate'], [4])
            self.assertEqual(result['source_frames'], 9)
            self.assertEqual(result['change_frames'], 8)
            self.assertEqual(result['source_stat']['size'], source.stat().st_size)
            self.assertEqual(result['change_stat']['mtime_ns'], change.stat().st_mtime_ns)
            self.assertEqual(progress, sorted(progress))
            self.assertEqual(progress[-1], 1.)
            self.assertTrue(all(isinstance(r['change_frame'], int) for r in result['rows']))

            # Incorrect estimates must not bypass the actual pair cap.
            meta = dict(result['source_meta'], estimated=1)
            with patch.object(match, 'MAX_PAIRS', 70), patch.object(hold_core, 'probe', return_value=meta):
                with self.assertRaisesRegex(ValueError, 'Shot too long'):
                    match.analyze_reference(source, change)

    def test_anchor_protects_held_image_in_both_reports_and_render(self):
        from reference_render import plan_reference
        with tempfile.TemporaryDirectory() as tmp:
            source, change = Path(tmp)/'source.mkv', Path(tmp)/'change.mkv'
            raw = pan(8)
            self.encode(source, raw)
            self.encode(change, [raw[i] for i in [0, 1, 2, 2, 4, 5, 6, 7]])
            automatic = match.analyze_reference(source, change)
            self.assertIn(automatic['rows'][3]['status'], ('missing', 'held'))
            pins = [(8, 8), (4, 4), (1, 1)]
            result = match.analyze_reference(source, change, anchors=iter(pins))
            self.assertEqual(result['manual_anchors'], [dict(source_frame=s,change_frame=c) for s,c in sorted(pins)])
            for s,c in pins:
                self.assertEqual(result['source_to_change'][s-1], c)
                self.assertEqual(result['change_to_source'][c-1], s)
                self.assertEqual(result['rows'][s-1]['status'], 'matched')
                for row in (result['rows'][s-1], result['change_rows'][c-1]):
                    self.assertTrue(row['manual'])
                    self.assertIsNone(row['confidence'])
                    self.assertFalse(row['review'])
            self.assertFalse(result['rows'][4].get('manual', False))
            plan = plan_reference(result)
            self.assertEqual(plan['lookup'][3], 4)
            self.assertFalse(any(run['start'] < 4 <= run['end'] for run in plan['runs']))
            restored = match.analyze_reference(source, change, anchors=())
            self.assertEqual(automatic, restored)

    def test_manual_anchors_can_resolve_untextured_row_without_implying_certainty(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'black.mkv'
            self.encode(path, [np.zeros((match.HEIGHT, match.WIDTH), np.float32)] * 4)
            result = match.analyze_reference(path, path, anchors=[(2, 2)])
            for r in result['change_rows']:
                if r['change_frame'] == 2:
                    self.assertTrue(r['manual'])
                    self.assertIsNone(r['confidence'])
                else:
                    self.assertTrue(r['review'])
                    self.assertTrue(np.isfinite(r['confidence']))

    def test_review_costs_are_actual_pairs_and_manual_anchor_preserves_auto_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, change = Path(tmp)/'source.mkv', Path(tmp)/'change.mkv'
            raw = pan(9)
            # Source 3 appears twice; 4 and 6 never appear in Change.
            order = [0, 1, 2, 2, 4, 6, 7, 8]
            self.encode(source, raw)
            self.encode(change, [raw[i] for i in order])
            prep = match.prepare_candidate_costs(source, change)
            self.assertEqual(prep.candidate_costs.shape, (8, 9))
            self.assertNotIn('rows', prep)
            self.assertNotIn('source_to_change', prep)
            self.assertEqual(prep.automatic_change, (None,)*9)
            self.assertLess(prep.candidate_costs[2,2], 1e-5)
            self.assertLess(prep.candidate_costs[3,2], 1e-5)
            self.assertGreater(prep.candidate_costs[4,2], .1)  # This edit matches Source 5 instead.
            self.assertLess(prep.candidate_costs[4,4], 1e-5)

            plain = match.analyze_reference(source, change)
            auto = match.analyze_reference(source, change, review_cache=True)
            manual = match.analyze_reference(source, change, anchors=[(3, 5)], review_cache=True)
            self.assertEqual(auto, plain)
            self.assertFalse(hasattr(plain, 'candidate_costs'))
            for report in (auto, manual):
                np.testing.assert_array_equal(report.candidate_costs, prep.candidate_costs)
                self.assertEqual(json.loads(json.dumps(report, allow_nan=False)), dict(report))
                self.assertNotIn('automatic_change', report)
                self.assertNotIn('candidate_costs', report)
                for row in report['change_rows']:
                    self.assertEqual(row['cost'], report.candidate_costs[row['change_frame']-1, row['source_frame']-1])
            self.assertEqual(manual.automatic_change, auto.automatic_change)
            self.assertEqual(manual.automatic_change[2], 3)
            self.assertIsNone(manual.automatic_change[3])
            self.assertEqual(manual['source_to_change'][2], 5)

    def test_flat_review_pairs_are_unavailable_and_remain_out_of_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'flat.mkv'
            self.encode(path, [np.zeros((match.HEIGHT, match.WIDTH), np.float32)] * 3)
            for cache in (match.prepare_candidate_costs(path, path),
                          match.analyze_reference(path, path, anchors=[(2, 2)], review_cache=True)):
                self.assertTrue(np.isnan(cache.candidate_costs).all())
                self.assertEqual(cache.automatic_change, (None,)*3)
                self.assertNotIn('candidate_costs', json.loads(json.dumps(cache, allow_nan=False)))


if __name__ == '__main__':
    unittest.main()
