"""Locale state and input safety without creating a GUI window."""
import json
import os
from pathlib import Path
import tempfile
import tkinter as tk
import unittest
from unittest.mock import patch

import i18n


class LanguageTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.environment = patch.dict(os.environ, LOCALAPPDATA=self.folder.name)
        self.environment.start()
        i18n.set_language('ko', persist=False)

    def tearDown(self):
        i18n.set_language('ko', persist=False)
        self.environment.stop()
        self.folder.cleanup()

    def test_persistent_language_keeps_other_preferences(self):
        path = i18n.settings_path();path.parent.mkdir(parents=True)
        path.write_text(json.dumps({'custom': 17}), encoding='utf-8')
        i18n.set_language('en')
        self.assertEqual(json.loads(path.read_text()), {'custom': 17, 'language': 'en'})
        i18n.set_language('ko', persist=False)
        self.assertEqual(i18n.load_language(), 'en')

    def test_bad_or_missing_settings_default_to_korean(self):
        self.assertEqual(i18n.load_language(), 'ko')
        path = i18n.settings_path();path.parent.mkdir(parents=True)
        for text in ('invalid json', '[]', '{"language":"invalid"}'):
            path.write_text(text, encoding='utf-8')
            self.assertEqual(i18n.load_language(), 'ko')

    def test_display_variable_switch_retains_source_and_numeric_inputs(self):
        interpreter = tk.Tcl()
        display = i18n.DisplayVar(interpreter, '홀드 2 · 점프 3')
        input_value = tk.StringVar(interpreter, '5')
        path = tk.StringVar(interpreter, 'examples/영상.mov')
        i18n.set_language('en', persist=False)
        self.assertEqual(display.get(), 'Hold 2 · Jump 3')
        display.set('처리 중 · 2 / 4프레임')
        self.assertEqual(display.get(), 'Processing · 2 / 4 frames')
        i18n.set_language('ko', persist=False)
        self.assertEqual(display.get(), '처리 중 · 2 / 4프레임')
        self.assertEqual(input_value.get(), '5')
        self.assertEqual(path.get(), 'examples/영상.mov')

    def test_paths_are_not_translated_inside_status_messages(self):
        i18n.set_language('en', persist=False)
        for path in (r'C:\영상\홀드 점프.mov', r'\\server\영상\교체.mov',
                     'examples/영상.mov', '영상.mov'):
            self.assertEqual(i18n.tr(path), path)
        source = r'저장 완료 · 원본 24프레임·FPS 유지 · C:\영상\홀드 점프.mov'
        self.assertEqual(i18n.tr(source), r'Saved · Source 24 frames and FPS preserved · C:\영상\홀드 점프.mov')

    def test_nested_display_values_and_reversible_static_labels(self):
        i18n.set_language('en', persist=False)
        values = i18n.tr(('홀드', '2–3', ('참조 프레임', 4)))
        self.assertEqual(values, ('Hold', '2–3', ('References', 4)))
        label = i18n.tr('영상 선택')
        i18n.set_language('ko', persist=False)
        self.assertEqual(i18n.tr(label), '영상 선택')

    def test_invalid_language_does_not_change_preferences(self):
        with self.assertRaises(ValueError):i18n.set_language('xx')
        self.assertFalse(i18n.settings_path().exists())


if __name__ == '__main__':
    unittest.main()
