"""Modeless selection of original anchor frames for a manual replacement."""
import tkinter as tk
from i18n import tr, DisplayVar
from tkinter import ttk

from ui_support import PALETTE


def _parse_frame(value, frames, label):
    value = str(value).strip()
    if not value:
        raise ValueError(f'{label} 기준 프레임 번호를 입력하세요.')
    try:
        frame = int(value)
    except (TypeError, ValueError):
        raise ValueError(f'{label} 기준 프레임은 정수로 입력하세요.') from None
    if not 1 <= frame <= frames:
        raise ValueError(f'{label} 기준 프레임은 1–{frames} 사이로 입력하세요.')
    return frame


def _selection(left, right, frames):
    if frames < 3:
        raise ValueError('3프레임 이상인 영상에서 사용할 수 있습니다.')
    left = _parse_frame(left, frames, '앞')
    right = _parse_frame(right, frames, '뒤')
    if right - left < 2:
        raise ValueError('뒤 기준은 앞 기준보다 최소 2프레임 뒤여야 합니다.')
    first, last = left + 1, right - 1
    span = str(first) if first == last else f'{first}–{last}'
    info = f'{left}·{right} 참조 → {span} 생성 · {right-left-1}장 · 길이 유지'
    return left, right, info


class ManualReplacementDialog(tk.Toplevel):
    def __init__(self, parent, frames, current_frame, on_add, on_seek):
        super().__init__(parent)
        self.frames = int(frames)
        self.current_frame = current_frame
        self.on_add = on_add
        self.on_seek = on_seek
        self._submitting = False
        self.title(tr('구간 추가'))
        self.configure(background=PALETTE['surface'])
        self.transient(parent.winfo_toplevel())
        self.protocol('WM_DELETE_WINDOW', self.destroy)
        self.bind('<Escape>', self._close)
        width, height = 600, 330
        x = max(0, min(parent.winfo_rootx() + 32, self.winfo_screenwidth() - width))
        y = max(0, min(parent.winfo_rooty() + 64, self.winfo_screenheight() - height - 40))
        self.geometry(f'{width}x{height}+{x}+{y}')
        self.minsize(540, 310)

        try:
            current = int(current_frame())
        except (TypeError, ValueError):
            current = 1
        left = max(1, min(current, self.frames - 2))
        right = max(1, min(self.frames, left + 2))
        self.left_var = tk.StringVar(self, value=str(left))
        self.right_var = tk.StringVar(self, value=str(right))
        self.info = DisplayVar(self)
        self.error = DisplayVar(self)

        body = ttk.Frame(self, padding=18, style='Surface.TFrame')
        body.pack(fill='both', expand=True)
        ttk.Label(body, text=tr('앞뒤 기준 프레임 선택'), style='Surface.TLabel',
                  font=('맑은 고딕', 12, 'bold')).pack(anchor='w')
        self.description_label = ttk.Label(body,
            text=tr('양 끝은 유지하고, 사이 프레임을 같은 간격으로 생성해 기존 위치에 적용합니다.'),
            style='Muted.TLabel', wraplength=555)
        self.description_label.pack(anchor='w', pady=(5, 13))

        fields = ttk.Frame(body, style='Surface.TFrame')
        fields.pack(fill='x')
        fields.columnconfigure(1, weight=1)
        for row, name, variable in ((0, 'left', self.left_var), (1, 'right', self.right_var)):
            label = '앞' if name == 'left' else '뒤'
            ttk.Label(fields, text=tr(f'{label} 기준 프레임'), style='Surface.TLabel').grid(
                row=row, column=0, sticky='w', padx=(0, 12), pady=5)
            entry = ttk.Spinbox(fields, from_=1, to=max(1, self.frames), increment=1,
                                textvariable=variable, width=9, justify='right')
            entry.grid(row=row, column=1, sticky='ew', padx=(0, 10), pady=5)
            current_button = ttk.Button(fields, text=tr('현재 프레임'), style='Compact.TButton',
                                        command=lambda side=name: self._use_current(side))
            current_button.grid(row=row, column=2, padx=(0, 7), pady=5)
            seek_button = ttk.Button(fields, text=tr('이동'), style='Compact.TButton',
                                     command=lambda side=name: self._seek(side))
            seek_button.grid(row=row, column=3, pady=5)
            entry.bind('<Return>', lambda event, side=name: self._seek(side))
            setattr(self, f'{name}_entry', entry)
            setattr(self, f'{name}_current_button', current_button)
            setattr(self, f'{name}_seek_button', seek_button)

        self.info_label = ttk.Label(body, textvariable=self.info, style='Surface.TLabel',
                                    wraplength=555, anchor='w')
        self.info_label.pack(fill='x', pady=(12, 4))
        self.error_label = ttk.Label(body, textvariable=self.error, style='Muted.TLabel',
                                     wraplength=555, anchor='nw')
        self.error_label.pack(fill='x')
        footer = ttk.Frame(body, style='Surface.TFrame')
        footer.pack(side='bottom', fill='x', pady=(8, 0), before=fields)
        self.add_button = ttk.Button(footer, text=tr('추가'), style='Primary.TButton', command=self._add)
        self.add_button.pack(side='right')
        self.close_button = ttk.Button(footer, text=tr('닫기'), style='Compact.TButton', command=self.destroy)
        self.close_button.pack(side='right', padx=(0, 8))
        body.bind('<Configure>', lambda event: self._wrap(event.width - 36))
        for variable in (self.left_var, self.right_var):
            variable.trace_add('write', self._refresh)
        self._refresh()
        # No grab_set or wait_window: the user may move through the original
        # video and then use either row's current-frame button.

    def _wrap(self, width):
        width = max(280, width)
        self.description_label.configure(wraplength=width)
        self.info_label.configure(wraplength=width)
        self.error_label.configure(wraplength=width)

    def _close(self, _event=None):
        self.destroy()
        return 'break'

    def _refresh(self, *_):
        try:
            _, _, info = _selection(self.left_var.get(), self.right_var.get(), self.frames)
        except ValueError as error:
            self.info.set('')
            self.error.set(str(error))
            self.add_button.configure(state='disabled')
        else:
            self.info.set(info)
            self.error.set('')
            self.add_button.configure(state='disabled' if self._submitting else 'normal')

    def _use_current(self, side):
        try:
            frame = _parse_frame(self.current_frame(), self.frames, '현재')
        except ValueError as error:
            self.error.set(str(error))
            return
        getattr(self, f'{side}_var').set(str(frame))

    def _seek(self, side):
        try:
            frame = _parse_frame(getattr(self, f'{side}_var').get(), self.frames,
                                 '앞' if side == 'left' else '뒤')
            self.on_seek(frame)
        except ValueError as error:
            self.error.set(str(error))
        return 'break'

    def _add(self):
        if self._submitting:
            return
        try:
            left, right, _ = _selection(self.left_var.get(), self.right_var.get(), self.frames)
        except ValueError as error:
            self.error.set(str(error))
            self.add_button.configure(state='disabled')
            return
        self._submitting = True
        self.add_button.configure(state='disabled')
        try:
            self.on_add(left, right)
        except ValueError as error:
            self.error.set(str(error))
            self.add_button.configure(state='normal')
        else:
            self.destroy()
        finally:
            self._submitting = False


def show_manual_replacement(parent, frames, current_frame, on_add, on_seek):
    return ManualReplacementDialog(parent, frames, current_frame, on_add, on_seek)
