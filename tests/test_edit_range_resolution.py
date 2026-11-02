"""Edit range references and destination source slots are independent axes."""
from copy import deepcopy
import unittest

from reference_render import (ManualRangeError, describe_reference_frames, edit_source_candidates,
                              plan_reference, resolve_edit_range)


class EditRangeResolutionTests(unittest.TestCase):
    def setUp(self):
        # Edits 2 and 4 belong to duplicate forward matches and are absent from
        # the inverse. Source 3 is a nearest-image placeholder using edit 3.
        self.report = dict(source_frames=6, change_frames=7,
                           rows=[dict(source_frame=s, change_frame=e, status='missing' if s == 3 else 'matched')
                                 for s, e in enumerate([1, 3, 3, 5, 6, 7], 1)],
                           change_to_source=[1, 2, 2, 4, 4, 5, 6])

    def reason(self, code, function, *args, **kwargs):
        with self.assertRaises(ManualRangeError) as failure:
            function(*args, **kwargs)
        self.assertEqual(failure.exception.code, code)

    def test_explicit_frames_absent_from_inverse_reach_the_plan(self):
        original = deepcopy(self.report)
        self.assertEqual(edit_source_candidates(self.report, 2), (2,))
        span = resolve_edit_range(self.report, 2, 4)
        self.assertEqual(span, dict(left=2, right=4, left_change=2, right_change=4))
        amended = {**self.report, 'manual_ranges': [span]}
        plan = plan_reference(amended)
        self.assertEqual(plan['lookup'], [1, 3, 3, 5, 6, 7])
        self.assertEqual(plan['runs'], [dict(start=2, end=3, generated=1, left_change=2, right_change=4)])
        actions = describe_reference_frames(amended)
        self.assertEqual(actions[1]['change_frame'], 2)
        self.assertEqual(actions[3]['change_frame'], 4)
        self.assertEqual((actions[2]['left_change'], actions[2]['right_change'], actions[2]['manual']), (2, 4, True))
        self.assertEqual(self.report, original)

    def test_adjacent_edit_frames_can_enclose_a_source_gap(self):
        self.assertEqual(edit_source_candidates(self.report, 3), (2, 3))
        self.reason('ambiguous', resolve_edit_range, self.report, 3, 4)
        self.assertEqual(resolve_edit_range(self.report, 3, 4, left_source=2)['right'], 4)
        self.reason('source_span', resolve_edit_range, self.report, 3, 4, left_source=3)
        self.reason('mapping', resolve_edit_range, self.report, 3, 4, left_source=6)
        self.reason('edit_bounds', resolve_edit_range, self.report, 7, 8)
        old = dict(self.report)
        del old['change_to_source']
        self.reason('unmapped_edit', resolve_edit_range, old, 2, 4)

    def test_conflicting_shared_edit_or_rematched_anchor_rejected(self):
        span = resolve_edit_range(self.report, 2, 4)
        amended = {**self.report, 'manual_ranges': [span]}
        self.reason('shared_anchor', resolve_edit_range, amended, 5, 7)
        self.assertEqual(resolve_edit_range(amended, 4, 7)['left_change'], 4)
        new_match = {**amended, 'change_to_source': [1,1,2,4,4,5,6]}
        self.reason('mapping', plan_reference, new_match)
        pin = {**self.report, 'manual_anchors': [{'source_frame':2, 'change_frame':3}]}
        self.reason('pinned', resolve_edit_range, pin, 2, 4)
