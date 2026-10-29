"""Small preferences panel; changing language does not unload the current clip."""
import tkinter as tk
from tkinter import ttk
from i18n import tr, language
from ui_support import PALETTE


def show_app_settings(parent, on_language, on_sdk):
    window = tk.Toplevel(parent)
    window.title(tr('설정'));window.transient(parent)
    window.configure(background=PALETTE['surface']);window.resizable(False, False)
    window.geometry(f'440x210+{max(0,parent.winfo_rootx()+80)}+{max(0,parent.winfo_rooty()+80)}')
    body = ttk.Frame(window, padding=20, style='Surface.TFrame');body.pack(fill='both', expand=True)
    row = ttk.Frame(body, style='Surface.TFrame');row.pack(fill='x')
    ttk.Label(row, text=tr('언어'), style='Section.TLabel').pack(side='left')
    chosen = tk.StringVar(window, value='English' if language() == 'en' else '한국어')
    combo = ttk.Combobox(row, values=['한국어', 'English'], textvariable=chosen, state='readonly', width=16)
    combo.pack(side='right');combo.bind('<<ComboboxSelected>>', lambda _:on_language('en' if chosen.get() == 'English' else 'ko'))
    ttk.Label(body, text=tr('변경 즉시 적용됩니다.'), style='Muted.TLabel').pack(anchor='w', pady=(12, 16))
    ttk.Separator(body).pack(fill='x')
    footer = ttk.Frame(body, style='Surface.TFrame');footer.pack(fill='x', pady=(15, 0))
    ttk.Button(footer, text=tr('SDK 설정…'), command=on_sdk).pack(side='left')
    ttk.Button(footer, text=tr('닫기'), style='Compact.TButton', command=window.destroy).pack(side='right')
    window.bind('<Escape>', lambda _:window.destroy())
    return window
