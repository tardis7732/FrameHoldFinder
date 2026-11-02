"""Choose edit images and see where their interpolation falls on the source timeline."""
import tkinter as tk
from tkinter import simpledialog, ttk

from i18n import language
from reference_render import edit_source_candidates, resolve_edit_range
from ui_support import PALETTE


_ERRORS = {
    'bounds': ('영상 범위 안에서 앞뒤 기준 사이에 한 프레임 이상 두세요.',
               'Choose source frames within the clip with at least one frame between them.'),
    'overlap': ('다른 추가 구간과 겹칩니다. 끝 프레임만 공유할 수 있어요.',
                'This overlaps another added range. Only endpoints can be shared.'),
    'edit_order': ('뒤 기준의 편집본 번호가 앞 기준보다 커야 합니다.',
                   'Choose endpoints that map to two different edit frames in forward order.'),
    'pinned': ('구간 안에 수동 지정한 매치가 있습니다. 기준 프레임을 조정하세요.',
               'This range contains a pinned frame match. Adjust the endpoints.'),
    'edit_bounds': ('영상 범위 안에서 앞 편집 프레임보다 큰 뒤 편집 프레임을 고르세요.',
                    'Choose edit frames within the clip, with the last frame after the first.'),
    'unmapped_edit': ('선택한 편집 프레임의 원본 위치를 찾을 수 없습니다.',
                      'No source position is available for one of these edit frames.'),
    'ambiguous': ('가능한 원본 위치가 여러 개입니다. 사용할 위치를 선택하세요.',
                  'Choose the source position for each ambiguous endpoint.'),
    'mapping': ('선택한 편집 프레임에 대응하는 원본 위치를 고르세요.',
                'Choose a listed source position for each edit frame.'),
    'source_span': ('두 원본 위치 사이에 생성할 프레임이 없습니다.',
                    'There are no source frames to generate between these positions.'),
    'shared_anchor': ('공유하는 원본 위치는 같은 편집 프레임을 참조해야 합니다.',
                      'Ranges sharing a source endpoint must use the same edit frame there.'),
    'not_direct': ('현재 편집본 이미지를 직접 사용하는 원본 행을 기준으로 고르세요.',
                   'Choose an endpoint that directly uses an edit frame.'),
}


def range_error_message(error, english=None):
    texts = _ERRORS.get(getattr(error, 'code', None),
                        ('이 구간을 추가할 수 없습니다.', 'This range cannot be added.'))
    if english is None:
        english = language() == 'en'
    return texts[1] if english else texts[0]


def range_fill_summary(analysis, left, right, english):
    count = right - left - 1
    targets = str(left+1) if count == 1 else f'{left+1}–{right-1}'
    keep = sum(left < item['source_frame'] < right for item in analysis.get('frame_overrides', ()))
    if keep:
        return (f'Source {targets} · Generate {count-keep} · Keep {keep} overridden' if english
                else f'원본 {targets} 중 {count-keep}장 생성 · 프레임 변경 {keep}장 유지')
    return (f'Source frames to generate   {targets} ({count} total)' if english
            else f'생성할 원본 프레임   {targets} ({count}장)')


class _RangeDialog(simpledialog.Dialog):
    def __init__(self, parent, analysis, source_frame):
        self.analysis = analysis
        self.frames = analysis['change_frames']
        first = max(1, min(source_frame - 1, analysis['source_frames'] - 2))
        rows = analysis['rows']
        self.left = rows[first-1]['change_frame']
        self.right = rows[first+1]['change_frame']
        if self.left >= self.right:
            self.left = max(1, min(self.left-1, self.frames-1))
            self.right = min(self.frames, self.left+2)
        self.english = language() == 'en'
        self.traces = []
        self.source_choices = []
        self.choice_edits = [None, None]
        super().__init__(parent.winfo_toplevel(), title=self.text('구간 추가', 'Add range'))

    def text(self, ko, en):
        return en if self.english else ko

    def body(self, parent):
        self.configure(background=PALETTE['surface'])
        parent.configure(background=PALETTE['surface'])
        self.resizable(False, False)
        body = ttk.Frame(parent, style='Surface.TFrame', padding=(12, 8))
        body.pack()
        limits = f'1–{self.frames}'
        heading = self.text(f'편집본 참조 프레임 ({limits})', f'Edit reference frames ({limits})')
        ttk.Label(body, style='Section.TLabel', text=heading).grid(
            row=0, column=0, columnspan=4, sticky='w', pady=(0, 8))
        self.left_var = tk.StringVar(self, self.left)
        self.right_var = tk.StringVar(self, self.right)
        fields = ((self.text('앞', 'First'), self.left_var), (self.text('뒤', 'Last'), self.right_var))
        for i, (label, var) in enumerate(fields):
            ttk.Label(body, text=label, style='Surface.TLabel').grid(row=1, column=2*i, sticky='w')
            entry = ttk.Spinbox(body, from_=1, to=self.frames, textvariable=var, width=6)
            entry.grid(row=1, column=2*i+1, sticky='w', padx=(6, 14))
            if i == 0:
                first_entry = entry
        self.left_source_var = tk.StringVar(self)
        self.right_source_var = tk.StringVar(self)
        for i, (var, label) in enumerate(((self.left_source_var, self.text('앞 원본 위치', 'First source position')),
                                          (self.right_source_var, self.text('뒤 원본 위치', 'Last source position')))):
            group = ttk.Frame(body, style='Surface.TFrame')
            group.grid(row=2, column=2*i, columnspan=2, sticky='w', padx=(0, 12), pady=(8, 0))
            ttk.Label(group, text=label, style='Surface.TLabel').pack(anchor='w')
            box = ttk.Combobox(group, textvariable=var, state='readonly', width=18)
            box.pack()
            box.bind('<<ComboboxSelected>>', self.validate)
            group.grid_remove()
            self.source_choices.append((group, box, var))
        self.info = ttk.Label(body, style='Muted.TLabel', justify='left', width=46, wraplength=360)
        line_height = self.tk.getint(self.tk.call('font', 'metrics', 'TkDefaultFont', '-linespace'))
        body.rowconfigure(3, minsize=2*line_height + 14)
        self.info.grid(row=3, column=0, columnspan=4, sticky='w', pady=(12, 0))
        first_entry.selection_range(0, 'end')
        return first_entry

    def buttonbox(self):
        buttons = ttk.Frame(self, style='Surface.TFrame', padding=(12, 0, 12, 12))
        buttons.pack(fill='x')
        self.add_button = ttk.Button(buttons, text=self.text('추가', 'Add'), style='Primary.TButton', command=self.ok)
        self.add_button.pack(side='right')
        ttk.Button(buttons, text=self.text('취소', 'Cancel'), style='Compact.TButton',
                   command=self.cancel).pack(side='right', padx=6)
        self.bind('<Return>', self.ok)
        self.bind('<Escape>', self.cancel)
        self.traces = [(v, v.trace_add('write', self.validate)) for v in (self.left_var, self.right_var)]
        self.validate()

    def _source_choice(self, i, edit, sources):
        group, box, var = self.source_choices[i]
        if len(sources) <= 1:
            group.grid_remove()
            self.choice_edits[i] = None
            return sources[0] if sources else None
        if self.choice_edits[i] != edit:
            labels = []
            for source in sources:
                row = self.analysis['rows'][source-1]
                status = self.text('누락 위치', 'missing slot') if row['status'] == 'missing' else self.text('대응', 'match')
                labels.append(f'{source} · {status}')
            box.configure(values=labels)
            var.set('')
            self.choice_edits[i] = edit
        group.grid()
        value = var.get()
        if value in box['values']:
            return int(value.split()[0])
        return None

    def validate(self, *_):
        self.selection = None
        try:
            left, right = int(self.left_var.get()), int(self.right_var.get())
        except ValueError:
            for i in range(2):
                self._source_choice(i, None, ())
            message = self.text('앞뒤 편집 프레임 번호를 정수로 입력하세요.',
                                'Enter a whole edit frame number for each endpoint.')
        else:
            try:
                left_options = edit_source_candidates(self.analysis, left)
                right_options = edit_source_candidates(self.analysis, right)
                a = self._source_choice(0, left, left_options)
                b = self._source_choice(1, right, right_options)
                self.selection = resolve_edit_range(self.analysis, left, right, left_source=a, right_source=b)
            except ValueError as exc:
                message = range_error_message(exc, self.english)
            else:
                a, b = self.selection['left'], self.selection['right']
                fill = range_fill_summary(self.analysis, a, b, self.english)
                message = self.text(f'대응 원본   {a} · {b}', f'Source endpoints   {a} · {b}') + '\n' + fill
                self.info.configure(text=message)
                self.add_button.state(['!disabled'])
                return True
        self.info.configure(text=message)
        self.add_button.state(['disabled'])
        return False

    def apply(self):
        self.result = self.selection

    def destroy(self):
        for var, trace in self.traces:
            var.trace_remove('write', trace)
        super().destroy()


def ask_reference_range(parent, analysis, source_frame):
    if not analysis or analysis['source_frames'] < 3 or analysis['change_frames'] < 2:
        return None
    return _RangeDialog(parent, analysis, source_frame).result
