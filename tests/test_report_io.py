import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from report_io import validate_report_path, write_report


class ReportIOTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.source = self.root / 'Source.mov'
        self.output = self.root / 'Output.mov'
        self.report = self.root / 'Output.mov.analysis.json'
        self.source.write_bytes(b'original video')
        self.output.write_bytes(b'output video')
        self.report.write_bytes(b'old report')
        self.protected = (self.source, self.output)

    def assert_unchanged_videos(self):
        self.assertEqual(self.source.read_bytes(), b'original video')
        self.assertEqual(self.output.read_bytes(), b'output video')
        self.assertEqual(list(self.root.glob('.*.tmp')), [])

    def test_existing_report_replaced_only_after_complete_utf8_write(self):
        data = {'상태': '완료', 'frames': 34}
        real_replace = os.replace
        def publish(temporary, target):
            self.assertEqual(target.read_bytes(), b'old report')
            self.assertEqual(temporary.parent, target.parent)
            self.assertEqual(temporary.read_text(encoding='utf-8'), json.dumps(data, ensure_ascii=False, indent=2))
            real_replace(temporary, target)
        with mock.patch('report_io.os.replace', side_effect=publish) as replace:
            write_report(self.report, data, self.protected)
        replace.assert_called_once()
        self.assertEqual(json.loads(self.report.read_text(encoding='utf-8')), data)
        self.assert_unchanged_videos()

    def test_new_report_and_none_protected_entries(self):
        target = self.root / 'new.json'
        self.assertEqual(validate_report_path(target, (None, self.source)), target)
        write_report(target, {'ok': True}, (None, self.source, self.output))
        self.assertEqual(json.loads(target.read_text(encoding='utf-8')), {'ok': True})
        self.assertEqual(self.report.read_bytes(), b'old report')
        self.assert_unchanged_videos()

    def test_direct_source_and_output_collisions_are_rejected(self):
        for target in self.protected:
            with self.subTest(target=target), self.assertRaises(ValueError):
                write_report(target, {}, self.protected)
        self.assert_unchanged_videos()

    def test_source_hardlink_alias_is_rejected(self):
        self.report.unlink()
        os.link(self.source, self.report)
        with self.assertRaises(ValueError):
            write_report(self.report, {}, self.protected)
        self.assert_unchanged_videos()

    def test_output_hardlink_alias_is_rejected(self):
        self.report.unlink()
        os.link(self.output, self.report)
        with self.assertRaises(ValueError):
            write_report(self.report, {}, self.protected)
        self.assert_unchanged_videos()

    def test_source_symlink_alias_is_rejected(self):
        self.report.unlink()
        try:
            self.report.symlink_to(self.source)
        except OSError as error:
            if getattr(error, 'winerror', None) == 1314:
                self.skipTest('Windows symlink permission is unavailable')
            raise
        with self.assertRaises(ValueError):
            write_report(self.report, {}, self.protected)
        self.assert_unchanged_videos()

    def test_directory_is_rejected(self):
        with self.assertRaises(ValueError):
            write_report(self.root, {}, self.protected)
        self.assertEqual(self.report.read_bytes(), b'old report')
        self.assert_unchanged_videos()

    def test_replace_failure_preserves_previous_report(self):
        with mock.patch('report_io.os.replace', side_effect=PermissionError('busy report')):
            with self.assertRaises(PermissionError):
                write_report(self.report, {'new': True}, self.protected)
        self.assertEqual(self.report.read_bytes(), b'old report')
        self.assert_unchanged_videos()

    def test_serialization_failure_preserves_previous_report(self):
        with self.assertRaises(TypeError):
            write_report(self.report, {'bad': {1}}, self.protected)
        self.assertEqual(self.report.read_bytes(), b'old report')
        self.assert_unchanged_videos()

    def test_late_alias_rechecked_with_generator_protected_paths(self):
        real_dumps = json.dumps
        def change_destination(*args, **kwargs):
            self.report.unlink()
            os.link(self.source, self.report)
            return real_dumps(*args, **kwargs)
        with mock.patch('report_io.json.dumps', side_effect=change_destination):
            with self.assertRaises(ValueError):
                write_report(self.report, {'new': True}, (p for p in self.protected))
        self.assert_unchanged_videos()


if __name__ == '__main__':
    unittest.main()
