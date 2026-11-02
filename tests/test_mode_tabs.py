"""The workspace mode switch should not resize when selection or language changes."""
import tkinter as tk
from tkinter import ttk
import unittest

from i18n import language, set_language
from ui_support import apply_design_style


class ModeTabTests(unittest.TestCase):
    def test_mode_tabs_have_stable_equal_geometry(self):
        try:
            root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(str(exc))
        self.addCleanup(root.destroy)
        self.addCleanup(set_language, language(), None, False)
        root.attributes('-alpha', 0)
        root.geometry('800x160')
        style = ttk.Style(root)
        style.theme_use('clam')
        apply_design_style(style)
        tabs = ttk.Notebook(root, style='Mode.TNotebook')
        tabs.pack(fill='both', expand=True)
        for label in ('단일 영상 · 홀드 / 점프', 'Video Edit · 원본 비교'):
            tabs.add(ttk.Frame(tabs), text=label)

        geometry = []
        for locale in ('ko', 'en'):
            set_language(locale, root, persist=False)
            for index in (0, 1):
                tabs.select(index)
                root.update()
                page_top = root.nametowidget(tabs.select()).winfo_y()
                y = page_top // 2
                widths = [0, 0]
                for x in range(tabs.winfo_width()):
                    if tabs.identify(x, y):
                        widths[tabs.index(f'@{x},{y}')] += 1
                self.assertGreater(widths[0], 0)
                self.assertEqual(widths[0], widths[1])
                centers = (widths[0] // 2, widths[0] + widths[1] // 2)
                tops = [next(y for y in range(page_top) if tabs.identify(x, y)) for x in centers]
                self.assertEqual(tops[0], tops[1])
                geometry.append((widths, tops, page_top))
        self.assertTrue(all(size == geometry[0] for size in geometry))
