"""Modeless, illustrated reference for the detector's existing behavior."""
import math
import tkinter as tk
from i18n import tr, DisplayVar
from tkinter import ttk

from ui_support import PALETTE, remove_dotted_focus


SEMANTIC = {
    'hold': '#fbe7ee', 'hold_edge': '#edceda', 'resolved': '#c8bfe8',
    'jump': '#d97b24', 'predicted': '#827d9f', 'excluded': '#98a5b4',
    'ordinary': '#4c86a8', 'threshold': '#dd8b24',
}


def _display(value, suffix=''):
    """The speed field may be empty or partially edited when help is opened."""
    if value is None or not str(value).strip():
        return '미입력'
    try:
        number = float(value)
    except (TypeError, ValueError):
        return '확인 필요'
    if not math.isfinite(number):
        return '확인 필요'
    return f'{number:g}{suffix}'


class DetectionHelp(tk.Toplevel):
    def __init__(self, parent, values):
        super().__init__(parent)
        self.values = dict(values)
        self.title(tr('감지 도움말'))
        self.configure(bg=PALETTE['surface'])
        self.transient(parent.winfo_toplevel())
        self.protocol('WM_DELETE_WINDOW', self.destroy)
        self.bind('<Escape>', self._close)
        self._fit_window(parent)

        style = ttk.Style(self)
        style.configure('DetectionHelp.TNotebook', background=PALETTE['surface'], borderwidth=0)
        style.configure('DetectionHelp.TNotebook.Tab', padding=(14, 7))
        style.map('DetectionHelp.TNotebook.Tab',
                  background=[('selected', 'focus', PALETTE['primary_hover']),
                              ('selected', PALETTE['primary']), ('active', PALETTE['hover']),
                              ('!selected', PALETTE['subtle'])],
                  foreground=[('selected', PALETTE['primary_fg']), ('!selected', PALETTE['muted'])],
                  bordercolor=[('selected', 'focus', PALETTE['primary_pressed']),
                               ('selected', PALETTE['primary']), ('!selected', PALETTE['border'])])
        remove_dotted_focus(style, 'DetectionHelp.TNotebook.Tab', padding=(14, 7), focus_padding=1)
        body = ttk.Frame(self, padding=(18, 12), style='Surface.TFrame')
        body.pack(fill='both', expand=True)
        ttk.Label(body, text=tr('감지 도움말'), style='Surface.TLabel',
                  font=('맑은 고딕', 13, 'bold')).pack(anchor='w', pady=(0, 10))
        footer = ttk.Frame(body, style='Surface.TFrame')
        footer.pack(side='bottom', fill='x', pady=(10, 0))
        self.close_button = ttk.Button(footer, text=tr('닫기'), command=self.destroy, style='Compact.TButton')
        self.close_button.pack(side='right')

        self.notebook = ttk.Notebook(body, style='DetectionHelp.TNotebook')
        self.notebook.pack(fill='both', expand=True)
        self.settings_tab = ttk.Frame(self.notebook, padding=(12, 12), style='Surface.TFrame')
        self.graph_tab = ttk.Frame(self.notebook, padding=(12, 12), style='Surface.TFrame')
        self.replacement_tab = ttk.Frame(self.notebook, padding=(12, 10), style='Surface.TFrame')
        self.notebook.add(self.settings_tab, text=tr('설정 기준'))
        self.notebook.add(self.graph_tab, text=tr('그래프 읽기'))
        self.notebook.add(self.replacement_tab, text=tr('교체 방식'))
        self._settings()
        self._graph()
        self._replacement()
        # No grab or wait_window: the video and settings stay usable beside help.
        self.after_idle(self._focus)

    def _fit_window(self, parent):
        screen_w, screen_h = self.winfo_screenwidth(), self.winfo_screenheight()
        width, height = min(840, max(320, screen_w - 48)), min(660, max(360, screen_h - 80))
        self.minsize(min(800, width), min(620, height))
        x = parent.winfo_rootx() + (parent.winfo_width() - width) // 2
        y = parent.winfo_rooty() + (parent.winfo_height() - height) // 2
        x, y = max(0, min(screen_w - width, x)), max(0, min(screen_h - height - 45, y))
        self.geometry(f'{width}x{height}+{x}+{y}')

    def _focus(self):
        if self.winfo_exists():
            self.lift()
            self.notebook.focus_set()

    def _close(self, _event=None):
        self.destroy()
        return 'break'

    def _settings(self):
        v, tab = self.values, self.settings_tab
        self.settings_table = table = ttk.Frame(tab, style='Surface.TFrame')
        table.pack(fill='x')
        table.columnconfigure(0, minsize=142)
        table.columnconfigure(1, minsize=120)
        table.columnconfigure(2, weight=1)
        for col, text in enumerate(('설정', '현재값', '의미')):
            ttk.Label(table, text=tr(text), style='Muted.TLabel').grid(row=0, column=col, sticky='w', pady=(0, 8))
        self.settings_rows = [
            ('분석 폭', _display(v.get('analysis_width', 960), ' px'), '추적에 사용할 영상의 가로 크기'),
            ('홀드 허용 이동', _display(v.get('quiet_px', .5), ' px 이하'),
             '같은 이미지로 볼 이동량. 원본 픽셀 기준이며 기본값은 0.5px'),
            ('최소 기준 이동', _display(v.get('min_move', 4.8), ' px'),
             '점프 비교에 사용할 추적점의 최소 움직임. 원본 픽셀 기준'),
            ('속도 기준', _display(v.get('threshold', 5), '% 초과'),
             '같은 점이 바로 앞·뒤 구간보다 모두 얼마나 빨라야 하는지'),
            ('추적점 비율', _display(v.get('support_percent', 30), '% 이상'),
             '비교 가능한 추적점 중 속도 기준을 넘는 점의 최소 비율'),
            ('최소 추적점', _display(v.get('min_points', 20), '개 이상'),
             '비율과 함께 충족할 실제 점 개수. 점수가 아니며 1~10000개 조절'),
        ]
        self.setting_description_labels = []
        for index, (name, value, meaning) in enumerate(self.settings_rows):
            row = 1 + index * 2
            ttk.Separator(table).grid(row=row, column=0, columnspan=3, sticky='ew')
            ttk.Label(table, text=tr(name), style='Surface.TLabel').grid(row=row + 1, column=0, sticky='w', pady=11)
            ttk.Label(table, text=tr(value), style='Surface.TLabel', font=('맑은 고딕', 10, 'bold')).grid(
                row=row + 1, column=1, sticky='w', padx=(0, 10), pady=11)
            label = ttk.Label(table, text=tr(meaning), style='Muted.TLabel', wraplength=440, justify='left')
            label.grid(row=row + 1, column=2, sticky='w', pady=9)
            self.setting_description_labels.append(label)
        table.bind('<Configure>', lambda e: self._wrap_settings(e.width))
        note = (
            f'최소 이동 기본값: 분석 가로 × 0.005 = {_display(v.get("min_move_default", 4.8), "px")}\n'
            '직접 입력한 px 값은 같은 영상 재분석에서 유지합니다. 새 영상·분석 폭 선택 시 기본값으로 돌아갑니다.\n'
            '홀드 기본값은 바뀌지 않습니다. 비율·최소 점 개수 변경은 다시 추적하지 않고 평가만 갱신합니다.'
        )
        self.settings_note = ttk.Label(tab, text=tr(note), style='Muted.TLabel', justify='left',
                                       wraplength=730, font=('맑은 고딕', 9))
        self.settings_note.pack(fill='x', pady=(12, 0))
        tab.bind('<Configure>', lambda e: self.settings_note.configure(wraplength=max(280, e.width - 26)))

    def _wrap_settings(self, width):
        for label in self.setting_description_labels:
            label.configure(wraplength=max(220, width - 268))

    def _graph(self):
        tab, v = self.graph_tab, self.values
        ttk.Label(tab, text=tr('설명용 예시 · 속도 기준 5%'), style='Surface.TLabel',
                  font=('맑은 고딕', 10, 'bold')).pack(anchor='w')
        self.graph_canvas = tk.Canvas(tab, height=206, background=PALETTE['surface'],
                                      highlightthickness=0, takefocus=False)
        self.graph_canvas.pack(fill='x', pady=(6, 3))
        self.graph_canvas.bind('<Configure>', self._draw_graph)
        self.legend_canvas = tk.Canvas(tab, height=48, background=PALETTE['surface'],
                                       highlightthickness=0, takefocus=False)
        self.legend_canvas.pack(fill='x')
        self.legend_canvas.bind('<Configure>', self._draw_legend)
        ttk.Separator(tab).pack(fill='x', pady=(7, 10))
        rules = (
            ('속도', '같은 추적점의 현재 움직임이 앞·뒤 구간보다 모두 기준을 초과해야 합니다.'),
            ('점 개수', f'유효점 {_display(v.get("support_percent", 30), "%")} 이상과 '
             f'최소 {_display(v.get("min_points", 20), "개")}를 동시에 충족해야 합니다.'),
            ('그래프 높이', '위 두 점 개수 조건을 만족하는 속도 초과율입니다. 기준선 위면 점프 후보입니다.'),
        )
        self.graph_rule_labels = []
        for title, text in rules:
            row = ttk.Frame(tab, style='Surface.TFrame');row.pack(fill='x', pady=4)
            ttk.Label(row, text=tr(title), width=12, style='Surface.TLabel', font=('맑은 고딕', 10, 'bold')).pack(side='left', anchor='n')
            label = ttk.Label(row, text=tr(text), style='Muted.TLabel', wraplength=590, justify='left')
            label.pack(side='left', fill='x', expand=True)
            self.graph_rule_labels.append(label)
        self.graph_note = ttk.Label(tab, text=(
            tr('보라 빈 원도 교체 대상입니다. 홀드 보간을 가정한 값이며 실제 생성 결과의 측정값은 아닙니다.\n'
            '점프 후보는 누락 확정이 아닙니다. 위 그림은 설명용 예시입니다.')),
            style='Muted.TLabel', wraplength=730, justify='left', font=('맑은 고딕', 9))
        self.graph_note.pack(fill='x', pady=(10, 0))
        tab.bind('<Configure>', self._wrap_graph)

    def _wrap_graph(self, event):
        for label in self.graph_rule_labels:
            label.configure(wraplength=max(220, event.width - 140))
        self.graph_note.configure(wraplength=max(280, event.width - 26))

    def _draw_graph(self, _event=None):
        c = self.graph_canvas;c.delete('all')
        w, h = max(320, c.winfo_width()), c.winfo_height()
        x0, x1, y0, y1 = 68, w - 20, 12, h - 28
        x = lambda i: x0 + i / 7 * (x1 - x0)
        maximum = 41 * 1.1  # Highest example point, with the same 10% headroom as the app.
        y = lambda value: y1 - value / maximum * (y1 - y0)
        for index in (0, 1):
            c.create_rectangle(x(index) - 14, y0, x(index) + 14, y1,
                               fill=SEMANTIC['hold'], outline='', tags='hold_example')
        for value in (0, maximum/2, maximum):
            c.create_line(x0, y(value), x1, y(value), fill=PALETTE['track'])
            c.create_text(x0 - 30, y(value), text=tr(f'{value:.3g}%'), anchor='e', fill=PALETTE['muted'], font=('맑은 고딕', 8))
        threshold_y = y(5)
        c.create_line(x0, threshold_y, x1, threshold_y, fill=SEMANTIC['threshold'], width=2,
                      dash=(5, 3), tags='threshold_line')
        # Match the real graph: a text-free grip sits left of the plot.
        c.create_rectangle(x0 - 22, threshold_y - 7, x0 - 4, threshold_y + 7,
                           fill=SEMANTIC['threshold'], outline=SEMANTIC['threshold'], tags='threshold_handle')
        c.create_line(x0 - 18, threshold_y - 2, x0 - 8, threshold_y - 2,
                      fill=PALETTE['surface'], tags='threshold_handle')
        c.create_line(x0 - 18, threshold_y + 2, x0 - 8, threshold_y + 2,
                      fill=PALETTE['surface'], tags='threshold_handle')
        c.create_line(x(3), y1, x(3), y(41), fill=SEMANTIC['resolved'], width=2,
                      dash=(2, 3), tags='resolved_example')
        c.create_oval(x(3)-4, y(41)-4, x(3)+4, y(41)+4, fill=SEMANTIC['resolved'],
                      outline='', tags='resolved_example')
        samples = [(1, 'ordinary'), (0, 'ordinary'), (3, 'ordinary'), (2, 'ordinary'),
                   (37, 'jump'), (4, 'ordinary'), (29, 'predicted'), (33, 'excluded')]
        for i, (value, kind) in enumerate(samples):
            color = SEMANTIC[kind]
            c.create_line(x(i), y1, x(i), y(value), fill=color, width=2)
            c.create_oval(x(i)-4, y(value)-4, x(i)+4, y(value)+4,
                          fill=PALETTE['surface'] if kind in ('predicted', 'excluded') else color,
                          outline=color, width=2, tags=f'{kind}_example')
            c.create_text(x(i), y1 + 15, text=tr(str(i + 2)), fill=PALETTE['muted'], font=('맑은 고딕', 8))

    def _draw_legend(self, _event=None):
        c = self.legend_canvas;c.delete('all')
        width = max(320, c.winfo_width())
        items = [('hold', '홀드'), ('resolved', '보정 시 해소'), ('jump', '점프'),
                 ('predicted', '보정 반영 점프'), ('excluded', '제외')]
        # Two short rows remain readable at the minimum window width.
        for i, (kind, text) in enumerate(items):
            row, col = divmod(i, 3)
            left, top = col * width / 3 + 8, row * 23 + 11
            color = SEMANTIC[kind]
            if kind == 'hold':
                c.create_rectangle(left - 5, top - 5, left + 5, top + 5,
                                   fill=color, outline=SEMANTIC['hold_edge'])
            else:
                c.create_oval(left - 4, top - 4, left + 4, top + 4,
                              fill=PALETTE['surface'] if kind in ('predicted', 'excluded') else color,
                              outline=color, width=2)
            c.create_text(left + 13, top, text=tr(text), anchor='w', fill=PALETTE['text'], font=('맑은 고딕', 9))

    def _replacement(self):
        tab = self.replacement_tab
        examples = [
            ('홀드 2–3 · 같은 이미지', [2, 3, 4], [3], ['1/2'], '2·4 참조 → 3 교체', True),
            ('점프 3→4', [3, 4, 5], [4], ['1/2'], '3·5 참조 → 4 교체', False),
            ('연속 점프 3→4, 4→5', [3, 4, 5, 6], [4, 5], ['1/3', '2/3'], '3·6 참조 → 4·5 교체', False),
        ]
        self.replacement_canvases = []
        for example in examples:
            canvas = tk.Canvas(tab, height=120, background=PALETTE['surface'], highlightthickness=0, takefocus=False)
            canvas.pack(fill='x', pady=(0, 7))
            canvas.bind('<Configure>', lambda e, c=canvas, data=example: self._draw_replacement(c, data))
            self.replacement_canvases.append(canvas)
        self.replacement_note = ttk.Label(tab, text=(
            tr('전체 프레임 수·FPS·영상 길이를 유지하며 오디오는 그대로 복사합니다.\n'
            '추가: 양끝 번호를 선택하면 내부 프레임만 생성합니다. 3·6 선택 → 4·5 생성.\n'
            '추가 구간과 겹치는 자동 항목은 비활성화됩니다. 추가 항목을 제외·삭제하면 다시 평가합니다.')),
            style='Muted.TLabel', wraplength=730, justify='left', font=('맑은 고딕', 9))
        self.replacement_note.pack(fill='x', pady=(3, 0))
        tab.bind('<Configure>', lambda e: self.replacement_note.configure(wraplength=max(280, e.width - 26)))

    def _draw_replacement(self, canvas, example):
        title, frames, targets, fractions, caption, hold = example
        c = canvas;c.delete('all');w = max(320, c.winfo_width())
        c.create_text(0, 10, text=tr(title), anchor='w', fill=PALETTE['text'], font=('맑은 고딕', 10, 'bold'))
        c.create_text(w - 2, 10, text=tr(caption), anchor='e', fill=PALETTE['muted'], font=('맑은 고딕', 9))
        c.create_text(8, 45, text=tr('원본'), anchor='w', fill=PALETTE['muted'], font=('맑은 고딕', 9))
        c.create_text(8, 95, text=tr('교체'), anchor='w', fill=PALETTE['muted'], font=('맑은 고딕', 9))
        gap = min(102, (w - 160) / max(1, len(frames) - 1))
        first = (w - gap * (len(frames) - 1)) / 2
        for index, frame in enumerate(frames):
            x = first + index * gap
            target = frame in targets
            for after, y in ((False, 45), (True, 95)):
                generated = after and target
                fill = PALETTE['primary'] if generated else SEMANTIC['hold'] if hold and not after and frame in (2, 3) else PALETTE['subtle']
                c.create_rectangle(x - 24, y - 13, x + 24, y + 13, fill=fill,
                                   outline=PALETTE['primary'] if generated else PALETTE['border'],
                                   tags='generated_tile' if generated else 'source_tile')
                text = '3 = 2' if hold and target and not after else str(frame)
                c.create_text(x, y, text=tr(text), fill=PALETTE['primary_fg'] if generated else PALETTE['text'],
                              font=('맑은 고딕', 9, 'bold'))
            if target:
                c.create_line(x, 60, x, 79, arrow='last', fill=PALETTE['primary'], width=2, tags='replacement_arrow')
                c.create_text(x + 9, 69, text=tr(fractions[targets.index(frame)]), anchor='w',
                              fill=PALETTE['muted'], font=('맑은 고딕', 8), tags='fraction_label')
        c.create_line(0, 119, w, 119, fill=PALETTE['track'])


def show_detection_help(parent, values):
    """Return a modeless help window; callers may keep and reuse the handle."""
    return DetectionHelp(parent, values)
