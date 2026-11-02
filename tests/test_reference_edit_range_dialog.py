"""The edit-range chooser must show and explicitly resolve ambiguous source slots."""
import tkinter as tk
import unittest

from i18n import language, set_language
from reference_range_dialog import _RangeDialog, ask_reference_range


class EditEndpointDialogTest(unittest.TestCase):
    def test_distinguishes_actual_and_missing_positions_in_both_languages(self):
        try:
            root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(str(exc))
        root.withdraw()
        self.addCleanup(root.destroy)
        self.addCleanup(set_language, language(), None, False)
        analysis = {
            'source_frames': 5, 'change_frames': 3, 'change_to_source': [1, 2, 5],
            'frame_overrides': [dict(source_frame=3, change_frame=1)],
            'rows': [dict(source_frame=i, change_frame=e, status=status)
                     for i, (e, status) in enumerate(((1,'matched'), (2,'matched'), (2,'missing'),
                                                      (3,'missing'), (3,'matched')), 1)],
        }
        for lang, match, missing in [('ko', '대응', '누락 위치'), ('en', 'match', 'missing slot')]:
            with self.subTest(lang=lang):
                set_language(lang, persist=False)
                seen = {}
                def choose():
                    dialog = next(w for w in root.winfo_children() if isinstance(w, _RangeDialog))
                    try:
                        dialog.left_var.set('2')
                        dialog.right_var.set('3')
                        seen['blocked'] = dialog.add_button.instate(['disabled'])
                        first, last = [box for _, box, _ in dialog.source_choices]
                        seen['first'] = first['values']
                        first.current(first['values'].index(f'2 · {match}'))
                        last.current(last['values'].index(f'5 · {match}'))
                        last.event_generate('<<ComboboxSelected>>')
                        seen['summary'] = dialog.info.cget('text')
                        dialog.add_button.invoke()
                    finally:
                        if dialog.winfo_exists():
                            dialog.cancel()
                root.after(50, choose)
                result = ask_reference_range(root, analysis, 3)
                self.assertTrue(seen['blocked'])
                self.assertIn(f'2 · {match}', seen['first'])
                self.assertIn(f'3 · {missing}', seen['first'])
                self.assertIn('3–4', seen['summary'])
                self.assertIn('1장 생성' if lang == 'ko' else 'Generate 1', seen['summary'])
                self.assertIn('프레임 변경 1장 유지' if lang == 'ko' else 'Keep 1 overridden', seen['summary'])
                self.assertEqual(result, dict(left=2, right=5, left_change=2, right_change=3))
