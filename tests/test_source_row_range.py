import unittest

from reference_render import ManualRangeError, describe_reference_frames, plan_reference, resolve_source_row_range


class SourceRowRangeTests(unittest.TestCase):
    def report(self):
        # Source 98 uses edit 97; source 99 is an automatic missing-frame fill.
        rows = [dict(source_frame=s, change_frame=max(1, s - (1 if s <= 98 else 2)),
                     status='missing' if s == 99 else 'matched')
                for s in range(1, 102)]
        return dict(source_frames=len(rows), change_frames=100, rows=rows)

    def test_two_rows_capture_displayed_edits_and_keep_neighboring_fill(self):
        report = self.report()
        before = describe_reference_frames(report)
        span = resolve_source_row_range(report, 98, 94)
        self.assertEqual(span, dict(left=94, right=98, left_change=93, right_change=97))
        actions = describe_reference_frames({**report, 'manual_ranges': [span]})
        self.assertEqual(actions[:93], before[:93])
        self.assertEqual(actions[98:], before[98:])
        self.assertEqual([(a['left_change'], a['right_change'], a['step'], a['count'])
                          for a in actions[94:97]], [(93, 97, step, 3) for step in (1, 2, 3)])
        self.assertTrue(all(a['manual'] for a in actions[94:97]))

    def test_endpoint_needs_direct_image_and_an_interior_frame(self):
        report = self.report()
        report['rows'][0]['status'] = 'missing'
        for first, last, code in [(99, 95, 'not_direct'), (1, 4, 'not_direct'),
                                  (94, 95, 'source_span'), (False, 10, 'bounds')]:
            with self.subTest(first=first, last=last):
                with self.assertRaises(ManualRangeError) as error:
                    resolve_source_row_range(report, first, last)
                self.assertEqual(error.exception.code, code)

    def test_overridden_endpoint_is_captured_without_changing_other_fill_references(self):
        report = self.report()
        report['rows'][97]['manual'] = True  # The output-only override can coexist with a match pin.
        report['frame_overrides'] = [dict(source_frame=98, change_frame=81)]
        span = resolve_source_row_range(report, 98, 78)
        self.assertEqual((span['right_change'], span['right_override_base']), (81, 97))
        report['manual_ranges'] = [span]
        # The other auto run, source 99, still uses edits 97 & 98 as before.
        plan = plan_reference(report)
        self.assertEqual(plan['runs'], [dict(start=78, end=97, generated=19, left_change=77, right_change=81),
                                        dict(start=98, end=99, generated=1)])
        report.pop('frame_overrides')
        actions = describe_reference_frames(report)
        self.assertEqual(actions[97]['change_frame'], 81)
        self.assertEqual((actions[98]['left_change'], actions[98]['right_change']), (97, 98))
        next_span = resolve_source_row_range(report, 98, 101)
        self.assertEqual((next_span['left_change'], next_span['left_override_base']), (81, 97))
        # A different match at the saved source boundary cannot silently
        # authorize the previous arbitrary edit.
        report['rows'][97]['change_frame'] = 96
        with self.assertRaises(ManualRangeError) as error:
            plan_reference(report)
        self.assertEqual(error.exception.code, 'mapping')
