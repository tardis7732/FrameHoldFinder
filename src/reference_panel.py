"""Two-input review workspace for Source ↔ Video Edit timing matches."""
from collections import OrderedDict
import os
from pathlib import Path
import queue
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

from PIL import Image, ImageChops, ImageTk
from tkinterdnd2 import COPY, DND_FILES, REFUSE_DROP

import hold_core
import sdk_bridge
from i18n import language, refresh_widgets, tr
from reference_confidence import ConfidenceGraph
from reference_geometry import sample_aspect_ratio, width_overlap_boxes
from reference_help import ReferenceHelp
from reference_range_dialog import ask_reference_range, range_error_message, range_fill_summary
from reference_render import (FrameOverrideError, ManualRangeError, describe_reference_frames,
                              plan_reference, resolve_edit_range, resolve_source_row_range,
                              validate_frame_override, validate_reference_range)
from report_io import write_report
from review_player import _Decoder
from ui_support import PALETTE, attach_tooltip, make_render_quality_control, make_tools_menu, show_task_cancel


_STATUS = {
    'matched': ('일치', 'Matched'), 'review': ('확인 필요', 'Review'),
    'missing': ('누락 후보', 'Missing'), 'held': ('이미지 홀드', 'Image hold'),
    'manual': ('수동 지정', 'Manual match'),
    'added': ('추가', 'Added'),
    'override': ('프레임 변경', 'Frame override'),
}

_VIEW_LAYOUTS = (('both', '나란히', 'Side by side'),
                 ('source', '원본만', 'Source only'),
                 ('change', '편집본만', 'Edit only'))


def _label(ko, en):
    return en if language() == 'en' else ko


def _plan_text(action):
    if action['kind'] == 'generate':
        left, right = action['left_change'], action['right_change']
        fraction = f"{action['step']}/{action['count'] + 1}"
        return _label(f'편집 {left}·{right} 사이 {fraction} 지점 생성',
                      f'Generate at {fraction} between edits {left} and {right}')
    frame = action['change_frame']
    text = _label(f'편집 {frame} 사용', f'Use edit {frame}')
    if action['kind'] == 'fallback':
        text += _label(' · 참조 부족', ' · no bracketing pair')
    elif action.get('manual_anchor'):
        text += _label(' · 추가 구간 참조', ' · added range endpoint')
    return text


def _rgb_difference(source, change, gain=1):
    """Compare full widths at the vertical center on the square-pixel preview grid."""
    if source.size == change.size:
        diff = ImageChops.difference(source, change)
    else:
        source_box, change_box = width_overlap_boxes(source.size, change.size)
        common = source.crop(source_box)
        edit = change.crop(change_box).resize(common.size, Image.Resampling.BILINEAR)
        diff = ImageChops.difference(common, edit)
        if diff.size != source.size:
            masked = Image.new('RGB', source.size)
            masked.paste(diff, source_box[:2])
            diff = masked
    return diff if gain == 1 else diff.point(lambda channel: min(255, channel * gain))


class _PreviewStream:
    """Read exact decoded indices using the motion player's bounded FFmpeg decoder."""
    def __init__(self, path, meta):
        self.path = path
        display_width = meta['width'] * float(sample_aspect_ratio(meta.get('sample_aspect_ratio')))
        ratio = min(1, 720 / display_width, 440 / meta['height'])
        self.size = (max(1, round(display_width * ratio)), max(1, round(meta['height'] * ratio)))
        self.cache = OrderedDict()
        self.decoder = None
        self.received = 0

    def stop(self, wait=False):
        if self.decoder is not None:
            self.decoder.stop()
            if wait:
                self.decoder.thread.join(timeout=2)
            self.decoder = None

    def frame(self, number):
        if number in self.cache:
            return self.cache[number]
        if (self.decoder is None or number < self.decoder.start or number <= self.received
                or number > self.received + 24):
            self.stop()
            self.decoder = _Decoder(self.path, number, *self.size)
            self.received = number - 1
        for _ in range(8):
            try:
                event = self.decoder.events.get_nowait()
            except queue.Empty:
                return None
            if event[0] == 'error':
                raise RuntimeError(event[1])
            if event[0] == 'end':
                raise ValueError(_label('미리보기 프레임을 읽지 못했습니다.', 'Could not decode this preview frame.'))
            _, self.received, pixels = event
            image = Image.frombytes('RGB', self.size, pixels)
            self.cache[self.received] = image
            if len(self.cache) > 16:
                self.cache.popitem(last=False)
            if self.received == number:
                return image
        return None


class ReferencePanel(ttk.Frame):
    """Standalone mode; the host calls on_hide/on_show and refresh_language."""

    def __init__(self, parent, *, settings_command=None, sdk_command=None, on_quality_change=None):
        super().__init__(parent, padding=(8, 4, 8, 6))
        self.analysis = None
        self._prepared = None
        self._preview = None
        self._render_quality = sdk_bridge.configured_quality()
        self._displayed_after = False
        self._graph_binding = None
        self.busy = self.playing = self.closed = False
        self.visible = True
        self.stop = threading.Event()
        self.events = queue.Queue()
        self._strings = []
        self.help_window = None
        self._job = ''
        self.last_output = None
        self._resume_frame = 1
        self._editing_pair = False
        self._output_editor = False
        self._override_edits = {}
        self._selected_edits = {}
        self._review_cursor = 1
        self._range_start = None
        self._range_span = None
        self._updating_fields = False
        self._message = ('원본과 편집본을 선택하세요.', 'Select the original and edited videos.')
        self._tick_id = None
        self._displayed = None
        self._requested = None
        self._deadline = 0
        self._preview_due = 0
        self._position = 0
        self.table_expanded = True
        self._table_split_fraction = None
        self._streams = {}
        self._images = {}
        self._photos = {}
        self.difference = tk.BooleanVar(self, False)
        self.preview_after = tk.BooleanVar(self, False)
        self.difference_gain = tk.StringVar(self, '1×')
        self.paths = {role: tk.StringVar(self) for role in ('source', 'change')}
        self.only_issues = tk.BooleanVar(self, False)
        self.timeline = tk.DoubleVar(self, 1)
        self.frame_number = tk.StringVar(self)
        self.status = tk.StringVar(self)
        self.summary = tk.StringVar(self)
        self.headers = {role: tk.StringVar(self) for role in self.paths}
        self.preview_headers = {}
        self.entries, self.open_buttons, self.canvases = {}, {}, {}
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        inputs = ttk.Frame(self, style='Surface.TFrame', padding=(12, 8))
        inputs.grid(row=0, column=0, sticky='ew', pady=(0, 5))
        inputs.columnconfigure(1, weight=1)
        input_rows = (
            ('source', ('기준 원본', 'Original / Source'), ('원본 선택', 'Select Source')),
            ('change', ('편집본', 'Video Edit / Change'), ('편집본 선택', 'Select Edit')),
        )
        for line, (role, labels, button_labels) in enumerate(input_rows):
            self._localized(ttk.Label(inputs, style='Section.TLabel'), *labels).grid(
                row=line, column=0, sticky='w', padx=(0, 10))
            entry = ttk.Entry(inputs, textvariable=self.paths[role], font=('맑은 고딕', 10))
            entry.grid(row=line, column=1, sticky='nsew', pady=3)
            entry.bind('<Control-a>', lambda event: self._select_text(event.widget))
            browse = self._localized(ttk.Button(inputs, style='Compact.TButton',
                command=lambda r=role: self.choose(r)), *button_labels)
            browse.grid(row=line, column=2, sticky='nsew', padx=(8, 6), pady=3)
            self.entries[role], self.open_buttons[role] = entry, browse
            self._drop_target(entry, role)
        self.analyze_button = self._localized(ttk.Button(inputs, style='Primary.TButton',
            command=self.start), '프레임 매치', 'Match Frames')
        self.analyze_button.grid(row=0, column=3, rowspan=2, sticky='ew')
        self.cancel_button = self._localized(ttk.Button(inputs, style='Tertiary.TButton',
            command=self.cancel), '취소', 'Cancel')
        self.cancel_button.grid(row=0, column=3, rowspan=2, sticky='ew')
        self.tools_button, self.tools_menu, self.tool_items = make_tools_menu(inputs, (
            (('preferences', '설정…', settings_command), ('sdk', 'SDK 설정…', sdk_command)),
            (('result', '최근 결과 열기', self._open_result),
             ('folder', '결과 폴더 열기', lambda: self._open_result(folder=True))),
            (('source_path', '원본 경로 복사', lambda: self._copy_path('source')),
             ('change_path', '편집본 경로 복사', lambda: self._copy_path('change'))),
            (('report', '대응표 JSON 저장…', self.save_report),),
            (('defaults', '설정 초기화', self.reset_view_settings), ('help', '원본 비교 가이드', self.show_help)),
        ), postcommand=self._refresh_tools)
        self.tools_button.grid(row=0, column=4, rowspan=2, padx=(6, 0))

        self.review_split = tk.PanedWindow(self, orient='vertical', bd=0, sashwidth='5p',
                                          sashrelief='flat', opaqueresize=True, bg=PALETTE['border'])
        self.review_split.grid(row=1, column=0, sticky='nsew')
        player_area = ttk.Frame(self.review_split, style='Surface.TFrame')
        player_area.columnconfigure(0, weight=1)
        player_area.rowconfigure(0, weight=1)
        review_area = ttk.Frame(self.review_split, style='Surface.TFrame')
        review_area.columnconfigure(0, weight=1)
        review_area.rowconfigure(1, weight=1)
        self.review_split.add(player_area, minsize='118p', stretch='always')
        self.review_split.add(review_area, stretch='always')

        self.review_split.bind('<Map>', self._reset_player_height)
        previews = ttk.Frame(player_area, style='Surface.TFrame')
        previews.grid(row=0, column=0, sticky='nsew')
        previews.rowconfigure(1, weight=1)
        for column, role in enumerate(self.paths):
            previews.columnconfigure(column, weight=1, uniform='preview')
            header = ttk.Frame(previews, style='Surface.TFrame')
            self.preview_headers[role] = header
            header.grid(row=0, column=column, sticky='ew')
            header.columnconfigure(0, weight=1)
            ttk.Label(header, textvariable=self.headers[role], style='Section.TLabel', width=1,
                      padding=(6, 3)).grid(row=0, column=0, sticky='ew')
            if role == 'change':
                self.preview_switch = ttk.Frame(header, style='Surface.TFrame')
                self.preview_switch.grid(row=0, column=1, padx=4)
                for value, ko, en in ((False, '보정 전', 'Before'), (True, '보정 후', 'After')):
                    radio = ttk.Radiobutton(self.preview_switch, variable=self.preview_after, value=value,
                                            command=self._preview_changed)
                    self._localized(radio, ko, en).pack(side='left', padx=2)
                self.difference_button = self._localized(ttk.Checkbutton(
                    header, variable=self.difference, command=self._difference_changed,
                    style='Surface.TCheckbutton'), '차이 보기', 'Difference')
                self.difference_button.grid(row=0, column=2, padx=4)
                self.gain_label = self._localized(ttk.Label(header, style='Muted.TLabel'), '차이 강도', 'Difference gain')
                self.gain_label.grid(row=0, column=3, padx=(2, 3))
                self.gain_box = ttk.Combobox(header, state='readonly', width=3,
                    textvariable=self.difference_gain, values=('1×', '2×', '4×', '8×'))
                self.gain_box.grid(row=0, column=4, padx=(0, 4))
                self.gain_box.bind('<<ComboboxSelected>>', self._difference_changed)
                attach_tooltip(self.difference_button, lambda: _label(
                    '두 영상의 가로폭과 세로 중심을 맞춰 겹치는 영역의 RGB 차이를 표시합니다.\n같은 픽셀과 겹치지 않는 영역은 검정입니다.',
                    'The full widths and vertical centers are aligned to compare the overlapping RGB pixels.\nMatching pixels and areas outside the overlap appear black.'))
                attach_tooltip(self.gain_box, lambda: _label('차이 표시 배율', 'Difference display gain'))
            canvas = tk.Canvas(previews, height=130, width=100, bd=0, highlightthickness=0,
                               bg='#191c21', takefocus=True)
            canvas.grid(row=1, column=column, sticky='nsew', padx=(1, 1))
            canvas.bind('<Configure>', lambda _, r=role: self._draw(r))
            canvas.bind('<space>', lambda _: self._play_key())
            canvas.bind('<Left>', lambda _: self._step_key(-1))
            canvas.bind('<Right>', lambda _: self._step_key(1))
            canvas.bind('<Button-1>', lambda event: event.widget.focus_set())
            self.canvases[role] = canvas
            self._drop_target(canvas, role)

        controls = ttk.Frame(player_area, style='Surface.TFrame', padding=(6, 4))
        controls.grid(row=1, column=0, sticky='ew')
        for side in (0, 2):
            controls.columnconfigure(side, weight=1, uniform='transport_sides')
        self.scale = ttk.Scale(controls, from_=1, to=2, variable=self.timeline,
                               style='Player.Horizontal.TScale',
                               command=lambda raw: self.seek(round(float(raw)), scrub=True))
        self.scale.grid(row=0, column=0, columnspan=3, sticky='ew', pady=(0, 3))
        self.scale.bind('<Left>', lambda _: self._step_key(-1))
        self.scale.bind('<Right>', lambda _: self._step_key(1))
        transport = ttk.Frame(controls, style='Surface.TFrame')
        transport.grid(row=1, column=1)
        self.prev = ttk.Button(transport, text='−1', width=3, style='Compact.TButton',
                               command=lambda: self.seek(self._position - 1))
        self.prev.pack(side='left')
        self.play = ttk.Button(transport, width=10, style='Accent.TButton', command=self.toggle_playback)
        self.play.pack(side='left', padx=4)
        self.next = ttk.Button(transport, text='+1', width=3, style='Compact.TButton',
                               command=lambda: self.seek(self._position + 1))
        self.next.pack(side='left')
        view_controls = ttk.Frame(controls, style='Surface.TFrame')
        view_controls.grid(row=1, column=2, sticky='e')
        self._localized(ttk.Label(view_controls, style='Muted.TLabel'), '화면', 'View').pack(side='left', padx=(0, 4))
        self.view_box = ttk.Combobox(view_controls, state='readonly', width=12)
        self.view_box.pack(side='left')
        self.view_box.bind('<<ComboboxSelected>>', self._view_changed)
        ttk.Label(view_controls, textvariable=self.frame_number, width=11, anchor='e',
                  style='Surface.TLabel').pack(side='left')
        self.anchor_toggle = self._localized(ttk.Button(controls, style='Compact.TButton',
            command=self._toggle_pair_editor), '매치 프레임 지정…', 'Set frame match…')
        self.anchor_toggle.grid(row=1, column=0, sticky='w')
        self._make_pair_editor(controls)

        review = self.review_tools = ttk.Frame(review_area, style='Surface.TFrame', padding=(6, 4))
        review.grid(row=0, column=0, sticky='ew')
        review.columnconfigure(1, weight=1)
        self.table_toggle = ttk.Button(review, style='Compact.TButton',
            command=lambda: self.set_table_expanded(not self.table_expanded))
        self.table_toggle.grid(row=0, column=0, sticky='w', padx=(0, 6))
        ttk.Label(review, textvariable=self.summary, style='Section.TLabel', width=1).grid(row=0, column=1, sticky='ew')
        self._localized(ttk.Checkbutton(review, variable=self.only_issues, command=self._fill_table,
                        style='Surface.TCheckbutton'), '검토 대상만', 'Issues only').grid(row=0, column=2, padx=4)
        self.add_button = self._localized(ttk.Button(review, command=self._begin_range_pick,
            style='Compact.TButton'), '구간 추가…', 'Add range…')
        self.add_button.grid(row=0, column=3, padx=(4, 0))
        self.remove_range_button = self._localized(ttk.Button(review, command=lambda: self.remove_range(self._position),
            style='Compact.TButton'), '추가 구간 해제', 'Remove added range')
        self.remove_range_button.grid(row=0, column=4, padx=(4, 0))
        self.override_button = self._localized(ttk.Button(review, command=self._open_output_editor,
            style='Compact.TButton'), '사용 프레임 변경…', 'Change frame used…')
        self.override_button.grid(row=0, column=5, padx=(4, 0))
        self.clear_override_button = self._localized(ttk.Button(review,
            command=lambda: self.remove_frame_override(self._position), style='Compact.TButton'),
            '변경 해제', 'Clear override')
        self.clear_override_button.grid(row=0, column=6, padx=(4, 0))

        self.range_bar = ttk.Frame(review_area, style='Surface.TFrame', padding=(6, 4))
        self.range_bar.grid(row=0, column=0, sticky='ew')
        self.range_bar.columnconfigure(0, weight=1)
        line_height = self.tk.getint(self.tk.call('font', 'metrics', 'TkDefaultFont', '-linespace'))
        self.range_bar.rowconfigure(0, minsize=2*line_height+4)
        self.range_hint = tk.StringVar(self)
        range_label = ttk.Label(self.range_bar, textvariable=self.range_hint, style='Section.TLabel',
                                justify='left', anchor='w', width=1)
        range_label.grid(row=0, column=0, sticky='ew')
        self.edit_numbers_button = self._localized(ttk.Button(self.range_bar, command=self._ask_range,
            style='Tertiary.TButton'), '편집본 번호로…', 'Enter edit numbers…')
        self.edit_numbers_button.grid(row=0, column=1, padx=(4, 0))
        self.cancel_range_button = self._localized(ttk.Button(self.range_bar, command=self._cancel_range_pick,
            style='Compact.TButton'), '취소', 'Cancel')
        self.cancel_range_button.grid(row=0, column=2, padx=4)
        self.apply_range_button = self._localized(ttk.Button(self.range_bar, command=self._apply_range_pick,
            style='Primary.TButton'), '구간 적용', 'Apply range')
        self.apply_range_button.grid(row=0, column=3)
        self.range_bar.grid_remove()

        lower = self.results_split = ttk.Panedwindow(review_area, orient='horizontal')
        lower.grid(row=1, column=0, sticky='nsew')
        self.confidence_graph = ConfidenceGraph(
            lower, on_seek=lambda n: self.seek(n, scrub=True),
            can_seek=lambda: not self._editing_pair or self._output_editor)
        lower.add(self.confidence_graph, weight=4)
        grid = self.table_pane = ttk.Frame(lower, style='Surface.TFrame')
        lower.add(grid, weight=6)
        lower.bind('<Map>', self._reset_table_width)
        grid.columnconfigure(0, weight=1)
        grid.rowconfigure(0, weight=1)
        self.table = ttk.Treeview(grid, columns=('source','status','plan','confidence'), show='headings',
                                  height=5, selectmode='browse')
        for name, sample in (('source', 'Source frame'), ('status', 'Image hold · Review'),
                             ('confidence', 'Confidence')):
            width = 18 + self.tk.getint(self.tk.call('font', 'measure', 'TkDefaultFont', sample))
            self.table.column(name, width=width, minwidth=width, anchor='center', stretch=False)
        self.table.column('plan', width=260, minwidth=155, anchor='w')
        self.table.heading('plan', anchor='w')
        self.table.tag_configure('missing', foreground='#b45309')
        self.table.tag_configure('held', foreground='#9d174d')
        self.table.tag_configure('review', foreground='#6d28d9')
        self.table.tag_configure('manual', foreground='#0e6682')
        self.table.tag_configure('added', foreground=PALETTE['primary'])
        self.table.tag_configure('override', foreground='#0e6682')
        self.table.tag_configure('range_start', background='#dbeafe')
        self.table.grid(row=0, column=0, sticky='nsew')
        self.table.bind('<<TreeviewSelect>>', self._select_row)
        for widget in (self.table, self.confidence_graph.canvas, self.scale):
            widget.bind('<Escape>', self._cancel_range_pick, add='+')
            widget.bind('<Return>', self._apply_range_pick, add='+')
        self.table.bind('<Left>', lambda _: self._step_key(-1) if self._editing_pair else None)
        self.table.bind('<Right>', lambda _: self._step_key(1) if self._editing_pair else None)
        scroll = ttk.Scrollbar(grid, orient='vertical', command=self.table.yview)
        scroll.grid(row=0, column=1, sticky='ns')
        self.table.configure(yscrollcommand=scroll.set)
        wide_scroll = ttk.Scrollbar(grid, orient='horizontal', command=self.table.xview)
        wide_scroll.grid(row=1, column=0, sticky='ew')
        wide_scroll.grid_remove()
        def horizontal_range(first, last):
            wide_scroll.set(first, last)
            if float(first) > 0 or float(last) < 1:
                wide_scroll.grid()
            else:
                wide_scroll.grid_remove()
        self.table.configure(xscrollcommand=horizontal_range)
        self.empty = ttk.Label(grid, style='Muted.TLabel')

        foot = ttk.Frame(self, style='Surface.TFrame', padding=(7, 4))
        foot.grid(row=2, column=0, sticky='ew')
        foot.columnconfigure(0, weight=1)
        ttk.Label(foot, textvariable=self.status, style='Muted.TLabel', anchor='w', width=12).grid(
            row=0, column=0, sticky='ew')
        quality_control, self.quality_box = make_render_quality_control(
            foot, lambda: self._render_quality, on_quality_change or self.set_render_quality)
        quality_control.grid(row=0, column=1, padx=(0, 8))
        self.preview_button = self._localized(ttk.Button(foot, style='Primary.TButton', command=self.render_preview),
                                              '미리보기 렌더', 'Render preview')
        self.preview_button.grid(row=0, column=2, padx=(0, 5))
        self.export = self._localized(ttk.Button(foot, style='Primary.TButton', command=self.render),
                                     '원본 타이밍으로 저장', 'Save on Source Timeline')
        self.export.grid(row=0, column=3)
        self.bar = ttk.Progressbar(self, maximum=100)
        self.bar.grid(row=3, column=0, sticky='ew')
        for variable in self.paths.values():
            variable.trace_add('write', self._invalidate)
        self.refresh_language()
        self._tick()

    def _localized(self, widget, ko, en):
        self._strings.append((widget, ko, en))
        widget.configure(text=_label(ko, en))
        return widget

    def _make_pair_editor(self, parent):
        bar = self.pair_editor = ttk.Frame(parent, style='Surface.TFrame', padding=(0, 5, 0, 1))
        bar.grid(row=2, column=0, columnspan=3, sticky='ew')
        bar.grid_remove()
        self.pair_source = tk.StringVar(self)
        self.pair_change = tk.StringVar(self)
        fixed = ttk.Frame(bar, style='Surface.TFrame')
        fixed.grid(row=0, column=0, columnspan=2, padx=(0, 8))
        self._localized(ttk.Label(fixed, style='Section.TLabel'),
                        '고정 원본', 'Locked source').pack(side='left', padx=4)
        ttk.Label(fixed, textvariable=self.pair_source, style='Section.TLabel').pack(side='left')
        self.reference_button = self._localized(ttk.Button(fixed, command=self._choose_reference,
            style='Tertiary.TButton'), '변경…', 'Change…')
        self.reference_button.pack(side='left', padx=3)
        self._localized(ttk.Label(bar, style='Surface.TLabel'), '편집본', 'Edit').grid(row=0, column=2, padx=(6, 4))
        self.change_spin = ttk.Spinbox(bar, from_=1, to=1, width=6, textvariable=self.pair_change)
        self.change_spin.grid(row=0, column=3)
        self.change_spin.bind('<Return>', self._confirm_candidate)
        self.change_spin.bind('<Escape>', lambda _: self._close_pair_editor())
        self.change_spin.bind('<Left>', lambda _: self._step_key(-1))
        self.change_spin.bind('<Right>', lambda _: self._step_key(1))
        self.change_spin.bind('<space>', lambda _: self._play_key())
        self.pair_change.trace_add('write', self._pair_input_changed)
        self.pin_button = self._localized(ttk.Button(bar, style='Compact.TButton', command=self._apply_pair),
                                          '이 쌍으로 지정', 'Pin this pair')
        self.pin_button.grid(row=0, column=4, padx=(10, 4))
        self.unpin_button = self._localized(ttk.Button(bar, style='Compact.TButton', command=self._remove_pair),
                                            '지정 해제', 'Unpin')
        self.unpin_button.grid(row=0, column=5)
        self.use_frame_button = self._localized(ttk.Button(bar, command=self._use_candidate,
            style='Primary.TButton'), '이 프레임 사용', 'Use this frame')
        self.use_frame_button.grid(row=0, column=4, columnspan=2, padx=(10, 4))
        self.use_frame_button.grid_remove()
        self.pair_hint = ttk.Label(bar, style='Muted.TLabel')
        self.pair_hint.grid(row=0, column=6, sticky='w', padx=8)
        bar.columnconfigure(6, weight=1)
        self._localized(ttk.Button(bar, command=self._close_pair_editor, style='Tertiary.TButton'),
                        '닫기', 'Close').grid(row=0, column=7)

    def _anchors(self):
        return {pair['source_frame']: pair['change_frame']
                for pair in (self.analysis or {}).get('manual_anchors', ())}

    def _toggle_pair_editor(self):
        if self.busy:
            return
        if self._editing_pair:
            self._close_pair_editor()
        elif self.analysis or self._prepared:
            self._open_pair_editor()
        else:
            paths = self._input_paths()
            if paths:
                from reference_match import prepare_candidate_costs
                self._launch('prepare', lambda progress, cancel: prepare_candidate_costs(
                    *paths, progress=progress, cancel=cancel))

    def _open_output_editor(self):
        if self.analysis is not None and not self.busy and not self._editing_pair:
            self._open_pair_editor(output_only=True)

    def _open_pair_editor(self, *, output_only=False):
        self._end_range_pick()
        media = self.analysis or self._prepared
        self._review_cursor = self._position
        if self.analysis:
            row = self._rows()[max(0, self._position - 1)]
            source, edit = row['source_frame'], row['change_frame']
            if output_only:
                edit = self._selected_edits.get(source, edit)
            pair = source, edit
        else:
            pair = self._input_pair() or (1, 1)
            for role in self.paths:
                self._streams[role] = _PreviewStream(media[role+'_path'],media[role+'_meta'])
        self.playing = False
        self._editing_pair = True
        self._output_editor = output_only
        if output_only:
            self.use_frame_button.grid()
            self.pin_button.grid_remove()
            self.unpin_button.grid_remove()
            self.reference_button.pack_forget()
        else:
            self.use_frame_button.grid_remove()
            self.pin_button.grid()
            self.unpin_button.grid()
            self.reference_button.pack(side='left', padx=3)
        self.table.configure(selectmode='none')
        self.change_spin.configure(to=media['change_frames'])
        self.pair_editor.grid()
        self._set_pair_fields(pair)
        self._position = pair[1]
        self._fill_table()
        self.seek(pair[1], force=True)
        if output_only:
            self._status(f'원본 {pair[0]}번 자리에 사용할 편집본 프레임을 선택하세요.',
                         f'Choose the edit frame to use at source frame {pair[0]}.')
        else:
            self._status('편집본에서 고정 원본과 같은 순간을 선택하세요.',
                         'Find the matching moment in the edit for this locked source frame.')
        self._headers()
        self._controls()
        self.change_spin.focus_set()
        self.change_spin.selection_range(0, 'end')

    def _choose_reference(self):
        self.playing = False
        self._controls()
        limit = (self.analysis or self._prepared)['source_frames']
        number = simpledialog.askinteger(_label('기준 원본 변경', 'Change reference frame'),
            _label(f'고정할 원본 프레임 (1–{limit})', f'Source frame to lock (1–{limit})'),
            initialvalue=int(self.pair_source.get()), minvalue=1, maxvalue=limit, parent=self)
        if number is not None:
            self.set_reference_frame(number)

    def set_reference_frame(self, number):
        """Explicitly select a new fixed source while preserving the current edit candidate."""
        if not self._editing_pair or self._output_editor or self.busy or type(number) is not int:
            return False
        if not 1 <= number <= (self.analysis or self._prepared)['source_frames']:
            return False
        self._set_pair_fields((number, self._position))
        self._sync_graph()
        self.seek(self._position, force=True)
        return True

    def _close_pair_editor(self, *, restore=True):
        if self._editing_pair:
            self._editing_pair = False
            self._output_editor = False
            self.playing = False
            self._position = self._review_cursor
            self.table.configure(selectmode='browse')
            self.pair_editor.grid_remove()
            if restore:
                self._fill_table()
            if restore and self.analysis:
                self.seek(self._position, force=True)
                self._status('분석 완료', 'Analysis complete')
            elif restore:
                self._reset_streams()
                self._headers()
                for role in self.canvases:
                    self._draw(role)
                self._status('원본과 편집본을 선택하고 프레임 매치를 실행하세요.', 'Select both inputs and run Match Frames.')
            self._headers()
            self._controls()
        return 'break'

    def _set_pair_fields(self, pair):
        self._updating_fields = True
        try:
            if self.pair_source.get() != str(pair[0]):
                self.pair_source.set(str(pair[0]))
            if self.pair_change.get() != str(pair[1]):
                self.pair_change.set(str(pair[1]))
        finally:
            self._updating_fields = False
        self._pair_controls()

    def _input_pair(self):
        try:
            s, c = int(self.pair_source.get()), int(self.pair_change.get())
        except ValueError:
            return None
        media = self.analysis or self._prepared
        if media and 1 <= s <= media['source_frames'] and 1 <= c <= media['change_frames']:
            return s, c
        return None

    def _pair_controls(self):
        pair = self._input_pair()
        if self._output_editor:
            ready = pair is not None and pair == self._displayed and not self._displayed_after and not self.busy
            self.use_frame_button.state(['!disabled' if ready else 'disabled'])
            self.pair_hint.configure(text=_label('선택한 원본 한 장에만 적용합니다.',
                                                 'Applies to this source frame only.'))
            return
        pins = self._anchors()
        can_pin = pair is not None and pins.get(pair[0]) != pair[1]
        can_unpin = pair is not None and pair[0] in pins
        if pair is None:
            hint = _label('두 영상의 범위 안에서 프레임을 입력하세요.', 'Enter a frame within each clip.')
        elif self._added_range_at(pair[0]):
            can_pin = False
            hint = _label('추가 구간에서 생성할 프레임입니다. 매치를 지정하려면 먼저 구간을 해제하세요.',
                          'This frame will be generated. Remove its added range to pin a match.')
        elif can_pin and any((pair[0]-s)*(pair[1]-c) <= 0 for s,c in pins.items() if s != pair[0]):
            can_pin = False
            hint = _label('다른 지정 쌍과 프레임 순서가 겹칩니다.', 'This pair crosses or shares another pinned frame.')
        elif self.analysis:
            hint = _label('그래프 커서는 고정한 원본 프레임을 표시합니다.',
                          'The graph cursor marks the locked source frame.')
        else:
            hint = _label('이 쌍을 기준으로 첫 분석을 시작합니다.', 'Pin the first pair to start matching.')
        self.pair_hint.configure(text=hint)
        self.pin_button.state(['!disabled' if can_pin and pair == self._displayed else 'disabled'])
        self.unpin_button.state(['!disabled' if can_unpin else 'disabled'])

    def _pair_input_changed(self, *_):
        if self._updating_fields or not self._editing_pair or self.busy:
            return
        self.playing = False
        self._pair_controls()
        pair = self._input_pair()
        if pair:
            self.seek(pair[1], scrub=True)
        else:
            self._controls()

    def _apply_pair(self, _event=None):
        pair = self._input_pair()
        if pair and not self._output_editor and not self.busy and not self.pin_button.instate(['disabled']):
            pins = self._anchors()
            pins[pair[0]] = pair[1]
            self._rematch(sorted(pins.items()))
        return 'break'

    def _confirm_candidate(self, _event=None):
        return self._use_candidate() if self._output_editor else self._apply_pair()

    def _use_candidate(self):
        pair = self._input_pair()
        if self._output_editor and pair and pair == self._displayed and not self._displayed_after:
            self.set_frame_override(*pair)
        return 'break'

    def _remove_pair(self):
        pair = self._input_pair()
        pins = self._anchors()
        if pair and not self.busy and pair[0] in pins:
            del pins[pair[0]]
            self._rematch(sorted(pins.items()))

    def _can_edit_ranges(self):
        return (not self.closed and not self.busy and not self._editing_pair
                and self.analysis is not None and self.analysis['source_frames'] >= 3
                and self.analysis['change_frames'] >= 2)

    def _added_range_at(self, source):
        if self.analysis is not None:
            for span in self.analysis.get('manual_ranges', ()):
                if span['left'] < source < span['right']:
                    return span
        return None

    def _begin_range_pick(self):
        if not self._can_edit_ranges() or self._range_start is not None:
            return
        self.playing = False
        # Temporarily show all source rows, including the trusted endpoints.
        self._previous_issue_filter = self.only_issues.get()
        self.only_issues.set(False)
        self.set_table_expanded(True)
        self._range_start = self._position if self._position in self._range_refs else 0
        self.review_tools.grid_remove()
        self.range_bar.grid()
        self._fill_table()
        self.seek(self._position)
        self.table.focus_set()

    def _update_range_pick(self):
        first, other = self._range_start, self._position
        if first is None:
            return
        self._range_span = None
        if not first and other in self._range_refs:
            first = self._range_start = other
            if self.table.exists(str(first)):
                tags = self.table.item(str(first), 'tags')
                self.table.item(str(first), tags=(*tags, 'range_start'))
        if not first:
            hint = _label('표에서 기준으로 쓸 원본 프레임을 선택하세요.',
                          'Select the first trusted source frame in the table.')
        else:
            edit = self._range_refs[first]
            heading = _label(f'첫 기준  원본 {first} (편집 {edit})', f'First endpoint  Source {first} (edit {edit})')
            if other == first:
                hint = heading + _label('\n표에서 다른 기준 행을 선택하세요.',
                                        '\nSelect the other endpoint in the table.')
            else:
                try:
                    span = resolve_source_row_range(self.analysis, first, other)
                except ManualRangeError as exc:
                    hint = heading + '\n' + range_error_message(exc)
                else:
                    self._range_span = span
                    left, right = span['left'], span['right']
                    a, b = span['left_change'], span['right_change']
                    hint = _label(f'원본 {left}·{right} 기준   ·   편집 {a}·{b} 참조',
                                  f'Source endpoints {left} · {right}   |   Edit references {a} · {b}')
                    hint += '\n' + range_fill_summary(self.analysis, left, right, language() == 'en')
        self.range_hint.set(hint)
        self.apply_range_button.state(['!disabled' if self._range_span is not None else 'disabled'])

    def _end_range_pick(self):
        if self._range_start is None:
            return
        self._range_start = self._range_span = None
        self.only_issues.set(self._previous_issue_filter)
        self.range_bar.grid_remove()
        self.review_tools.grid()

    def _cancel_range_pick(self, _event=None):
        if self._range_start is not None:
            self._end_range_pick()
            self._fill_table()
            self._controls()
            return 'break'

    def _apply_range_pick(self, _event=None):
        if self._range_start is None:
            return
        if self._can_edit_ranges() and self._range_span is not None:
            self._commit_range(self._range_span)
        return 'break'

    def _ask_range(self):
        if not self._can_edit_ranges():
            return
        self.playing = False
        self._controls()
        analysis = self.analysis
        span = ask_reference_range(self, analysis, self._position)
        if span is not None and self.analysis is analysis:
            self.add_edit_range(span['left_change'], span['right_change'],
                                left_source=span['left'], right_source=span['right'])

    def add_edit_range(self, left_change, right_change, *, left_source=None, right_source=None):
        if not self._can_edit_ranges():
            return False
        try:
            span = resolve_edit_range(self.analysis, left_change, right_change,
                                      left_source=left_source, right_source=right_source)
        except ManualRangeError as exc:
            self._status(range_error_message(exc, english=False), range_error_message(exc, english=True))
            return False
        return self._commit_range(span)

    def add_range(self, left, right):
        """Add a source-indexed span for callers using the original range format."""
        if not self._can_edit_ranges():
            return False
        try:
            validate_reference_range(self.analysis, left, right)
        except ManualRangeError as exc:
            self._status(range_error_message(exc, english=False), range_error_message(exc, english=True))
            return False
        return self._commit_range({'left': left, 'right': right})

    def _edit_span_label(self, span):
        left_edit = span.get('left_change', self._rows()[span['left']-1]['change_frame'])
        right_edit = span.get('right_change', self._rows()[span['right']-1]['change_frame'])
        return f'{left_edit}–{right_edit}'

    def _commit_range(self, span):
        left, right = span['left'], span['right']
        ranges = [*self.analysis.get('manual_ranges', ()), span]
        target = self._position if left < self._position < right else left + 1
        self._set_plan_option('manual_ranges', sorted(ranges, key=lambda span: span['left']), target)
        edits = self._edit_span_label(span)
        slots = str(left+1) if right == left+2 else f'{left+1}–{right-1}'
        self._status(f'편집본 {edits} 구간 추가 · 원본 {slots} 위치',
                     f'Added edit range {edits} · source positions {slots}')
        return True

    def remove_range(self, source_frame):
        if not self._can_edit_ranges():
            return False
        span = self._added_range_at(source_frame)
        if span is None:
            return False
        ranges = [r for r in self.analysis['manual_ranges'] if r != span]
        self._set_plan_option('manual_ranges', ranges, source_frame)
        edits = self._edit_span_label(span)
        self._status(f'편집본 {edits} 추가 구간을 해제했습니다.',
                     f'Removed the added edit range {edits}.')
        return True

    def set_frame_override(self, source, edit):
        if self.closed or self.busy or self.analysis is None or (self._editing_pair and not self._output_editor):
            return False
        try:
            validate_frame_override(self.analysis, source, edit)
        except FrameOverrideError:
            self._status('영상 범위 안에서 원본과 편집본 프레임을 선택하세요.',
                         'Choose a source frame and an edit frame within the clips.')
            return False
        if self._output_editor:
            self._close_pair_editor(restore=False)
        if self._override_edits.get(source) == edit:
            self._fill_table()
            self.seek(source, force=True)
        else:
            overrides = dict(self._override_edits)
            overrides[source] = edit
            self._store_overrides(overrides, source)
        self._status(f'원본 {source}번에 편집본 {edit}번을 사용합니다.',
                     f'Source frame {source} will use edit frame {edit}.')
        return True

    def remove_frame_override(self, source):
        if (self.closed or self.busy or self._editing_pair or self.analysis is None
                or type(source) is not int or source not in self._override_edits):
            return False
        overrides = dict(self._override_edits)
        del overrides[source]
        self._store_overrides(overrides, source)
        self._status(f'원본 {source}번을 기존 저장 방식으로 되돌렸습니다.',
                     f'Restored the previous export plan for source frame {source}.')
        return True

    def _store_overrides(self, overrides, source):
        entries = [{'source_frame': s, 'change_frame': e} for s, e in sorted(overrides.items())]
        self._set_plan_option('frame_overrides', entries, source)

    def _set_plan_option(self, key, entries, target):
        self._end_range_pick()
        if entries:
            self.analysis[key] = entries
        else:
            self.analysis.pop(key, None)
        self.playing = False
        self._discard_preview()
        self._position = target
        self._fill_table()
        self.seek(target, force=True)

    def refresh_language(self):
        for widget, ko, en in self._strings:
            widget.configure(text=_label(ko, en))
        self._table_toggle_label()
        view_index = max(0, self.view_box.current())
        self.view_box.configure(values=tuple(_label(ko, en) for _, ko, en in _VIEW_LAYOUTS))
        self.view_box.current(view_index)
        self.tools_button.configure(text=tr('더보기'))
        refresh_widgets(self.tools_menu)
        self._refresh_tools()
        self.status.set(_label(*self._message))
        self._update_analyze_label()
        self._fill_table()
        self._controls()
        self._headers()
        self.confidence_graph.refresh_language()
        if self._editing_pair:
            self._pair_controls()
        for role in self.paths:
            self._draw(role)

    def _update_analyze_label(self):
        ready = self.analysis is not None
        self.analyze_button.configure(text=tr('재분석') if ready else _label('프레임 매치', 'Match Frames'))

    def _controls(self):
        ready = self.analysis is not None
        picking = self._range_start is not None
        for role in self.paths:
            for widget in (self.entries[role], self.open_buttons[role]):
                widget.state(['disabled' if self.busy else '!disabled'])
        can_scan = not self.busy and all(v.get().strip() for v in self.paths.values())
        self.analyze_button.state(['!disabled' if can_scan and not picking else 'disabled'])
        self.anchor_toggle.state(['!disabled' if can_scan and not picking else 'disabled'])
        show_task_cancel(self.analyze_button, self.cancel_button, self.busy, self.stop.is_set())
        self.tools_button.state(['disabled' if self.busy else '!disabled'])
        self.quality_box.state(['disabled' if self.busy else '!disabled'])
        for widget in (self.export, self.override_button):
            widget.state(['!disabled' if ready and not self.busy and not self._editing_pair and not picking else 'disabled'])
        can_clear = ready and not self.busy and not self._editing_pair and self._position in self._override_edits
        self.clear_override_button.state(['!disabled' if can_clear else 'disabled'])
        editable = self._can_edit_ranges()
        self.add_button.state(['!disabled' if editable else 'disabled'])
        can_remove = editable and self._added_range_at(self._position) is not None
        self.remove_range_button.state(['!disabled' if can_remove else 'disabled'])
        can_preview = ready and not self.busy and not self._editing_pair and self._preview is None and not picking
        self.preview_button.state(['!disabled' if can_preview else 'disabled'])
        if self._preview is not None and not self._editing_pair:
            self.preview_switch.grid()
        else:
            self.preview_switch.grid_remove()
        for widget in (self.prev,self.next,self.scale):
            widget.state(['!disabled' if ready or self._editing_pair else 'disabled'])
        self.play.state(['!disabled' if self._playback_fps() > 0 and not picking else 'disabled'])
        play_text = _label('▶ 편집본 재생', '▶ Play edit') if self._editing_pair else _label('▶ 재생', '▶ Play')
        self.play.configure(text=_label('❚❚ 정지','❚❚ Pause') if self.playing else play_text)

    def _refresh_tools(self):
        output_ready = not self.busy and self.last_output and Path(self.last_output).is_file()
        states = {'result': output_ready, 'folder': output_ready,
                  'report': self.analysis is not None and not self.busy and not self._editing_pair and self._range_start is None}
        for role, path in self.paths.items():
            states[role+'_path'] = bool(path.get().strip()) and not self.busy
        for key, available in states.items():
            self.tools_menu.entryconfigure(self.tool_items[key], state='normal' if available else 'disabled')

    def _reset_player_height(self, event=None):
        split = self.review_split
        if event is not None:
            split.unbind('<Map>')
        if not split.winfo_ismapped():
            return
        player, review = (split.nametowidget(str(pane)) for pane in split.panes())
        if event is not None:
            split.paneconfigure(review, minsize=review.winfo_reqheight())
        split.update_idletasks()
        sash = split.winfo_pixels(split['sashwidth'])
        spare = split.winfo_height() - sash - player.winfo_reqheight() - review.winfo_reqheight()
        split.sash_place(0, 0, player.winfo_reqheight() + round(spare * .6))

    def reset_view_settings(self):
        if self.busy:
            return
        self.view_box.current(0)
        self.difference.set(False)
        self.difference_gain.set('1×')
        self.preview_after.set(False)
        if self._range_start is not None:
            self._previous_issue_filter = False
        if self.only_issues.get():
            self.only_issues.set(False)
            self._fill_table()
        self._table_split_fraction = None
        self.set_table_expanded(True)
        self._reset_table_width()
        self.confidence_graph.navigation.fit()
        self._view_changed()
        self._preview_changed()
        self._headers()
        self._reset_player_height()
        self._status('원본 비교 설정을 초기화했습니다.', 'Video Edit settings reset.')

    def show_help(self):
        if self.busy:
            return
        if self.help_window is None or not self.help_window.winfo_exists():
            self.help_window = ReferenceHelp(self)
        else:
            self.help_window.deiconify()
            self.help_window.lift()
            self.help_window.notebook.focus_set()

    def _copy_path(self, role):
        path = self.paths[role].get().strip().strip('"')
        if not self.busy and path:
            self.clipboard_clear()
            self.clipboard_append(path)
            self._status('파일 경로를 복사했습니다.', 'File path copied.')

    def _open_result(self, folder=False):
        if self.busy or not self.last_output:
            return
        path = Path(self.last_output)
        if not path.is_file():
            self._status('저장한 결과 파일을 찾을 수 없습니다.', 'The saved result file could not be found.')
            return
        try:
            os.startfile(str(path.parent if folder else path))
        except OSError as error:
            self._status(f'열기 실패 · {error}', f'Could not open · {error}')

    def _status(self, ko, en):
        self._message = ko, en
        self.status.set(_label(ko,en))

    def _drop_target(self, widget, role):
        try:
            widget.drop_target_register(DND_FILES)
            widget.dnd_bind('<<Drop>>', lambda e: self._drop(e, role))
        except (AttributeError, tk.TclError):
            pass  # Plain Tk hosts still provide Browse and editable paths.

    def _drop(self, event, role):
        if self.busy:
            return REFUSE_DROP
        try:
            paths = self.tk.splitlist(event.data)
            if len(paths) == 1 and Path(paths[0]).is_file():
                self.set_inputs(**{role: paths[0]})
                return COPY
        except (OSError, tk.TclError):
            pass
        self._status('영상 파일을 하나씩 놓으세요.', 'Drop one video file at a time.')
        return REFUSE_DROP

    @staticmethod
    def _select_text(widget):
        widget.selection_range(0, 'end')
        return 'break'

    def choose(self, role=None):
        if self.busy:
            return
        if role is None:
            choose_change = self.focus_get() == self.entries['change'] or (
                self.paths['source'].get() and not self.paths['change'].get())
            role = 'change' if choose_change else 'source'
        value = filedialog.askopenfilename(parent=self,
            title=_label('기준 원본 선택','Choose original video') if role == 'source' else _label('편집본 선택','Choose edited video'),
            filetypes=[(_label('영상','Videos'), '*.mov *.mp4 *.mxf *.mkv *.avi *.webm *.m4v'),
                       (_label('모든 파일','All files'), '*.*')])
        if value:
            self.set_inputs(**{role: value})

    def set_inputs(self, source=None, change=None):
        if not self.busy:
            for role, path in (('source',source), ('change',change)):
                if path is not None:
                    self.paths[role].set(str(path))

    def _invalidate(self, *_):
        self._end_range_pick()
        if self.busy:
            self.stop.set()
        self._close_pair_editor(restore=False)
        self._reset_streams()
        self.analysis = None
        self._prepared = None
        self._set_pair_fields(('', ''))
        self._position = 0
        self.bar['value'] = 0
        self._status('원본과 편집본을 선택하고 프레임 매치를 실행하세요.', 'Select both inputs and run Match Frames.')
        self.refresh_language()

    def _reset_streams(self):
        self.playing = False
        self._discard_preview()
        for stream in self._streams.values():
            stream.stop()
        self._streams.clear()
        self._requested = self._displayed = None
        self._images.clear()
        self._photos.clear()

    def _discard_preview(self):
        stream = self._streams.pop('preview', None)
        if stream is not None:
            stream.stop(wait=True)
        if self._preview is not None:
            self._preview.close()
            self._preview = None
        self.preview_after.set(False)
        if self._displayed_after:
            self._displayed_after = False
            self._displayed = None
            self._images.pop('change', None)
            self._headers()
            self._draw('change')

    def set_render_quality(self, quality):
        self.quality_box.set(quality)
        if quality == self._render_quality:
            return
        self._render_quality = quality
        if self.busy and self._job == 'preview':
            self.cancel()
        if self._preview is not None:
            self._discard_preview()
            self.seek(self._position, force=True, playing=self.playing)
            if not self.busy:
                self._status('품질이 변경되었습니다. 미리보기를 다시 렌더하세요.',
                             'Quality changed. Render a new preview.')

    def start(self):
        if self._range_start is None:
            self._rematch(sorted(self._anchors().items()))

    def _input_paths(self):
        paths = [v.get().strip().strip('"') for v in self.paths.values()]
        if not all(Path(p).is_file() for p in paths):
            self._status('두 영상의 파일 경로를 확인하세요.', 'Check both video paths.')
            return None
        return paths

    def _rematch(self, pins):
        if self.busy:
            return
        paths = self._input_paths()
        if not paths:
            return
        tentative = self._input_pair() if self._editing_pair else None
        self._resume_frame = tentative[0] if tentative else max(1, self._position)
        self._close_pair_editor()
        prior = self.analysis or {}
        options = {key: list(prior[key]) for key in ('manual_ranges', 'frame_overrides') if prior.get(key)}
        def analyze(progress, cancel):
            from reference_match import analyze_reference
            result = analyze_reference(*paths, anchors=pins, progress=progress, cancel=cancel, review_cache=True)
            if options:
                try:
                    plan_reference({**result, **options})
                except ManualRangeError as exc:
                    raise ValueError(_label('추가 구간과 새 매치가 충돌해 기존 결과를 유지했습니다. ',
                                            'Kept the previous match because an added range conflicts. ')
                                     + range_error_message(exc)) from exc
                except FrameOverrideError as exc:
                    raise ValueError(_label('변경한 프레임이 새 영상 범위를 벗어나 기존 결과를 유지했습니다.',
                                            'Kept the previous plan because an override falls outside the new clips.')) from exc
                result.update(options)
            return result
        self._launch('analyze', analyze)

    def _launch(self, job, run):
        self.stop = threading.Event()
        self.busy = True
        self.playing = False
        self._job = job
        if job == 'prepare':
            self._status('프레임 후보 비용 계산 중…', 'Comparing candidate frames…')
        elif job == 'analyze':
            self._status('프레임 대응 분석 중…', 'Matching frames…')
        elif job == 'preview':
            self._status('보정 미리보기 렌더 중…', 'Rendering corrected preview…')
        else:
            self._status('영상 저장 중…', 'Rendering video…')
        self.bar['value'] = 0
        self._controls()
        def worker():
            try:
                result = run(lambda fraction: self.events.put(('progress',fraction)), self.stop)
                self.events.put(('done',result))
            except Exception as exc:
                self.events.put(('cancelled' if self.stop.is_set() or isinstance(exc,hold_core.Cancelled) else 'error', str(exc)))
            if job == 'preview' and self.closed:
                self._discard_pending_preview()
        threading.Thread(target=worker, daemon=True).start()

    def cancel(self):
        if self.busy:
            self.stop.set()
            self._status('취소 중…','Canceling…')
            self._controls()

    def _job_event(self, kind, value):
        if kind == 'progress':
            self.bar['value'] = min(100, max(0, value*100))
            return
        self.busy = False
        if kind == 'error' and not self.stop.is_set():
            self._status(value,value)
            messagebox.showerror(_label('작업 실패','Operation failed'), value, parent=self)
        elif kind == 'cancelled' or (self.stop.is_set() and (self._job != 'render' or kind == 'error')):
            if self._job == 'preview' and kind == 'done':
                value.close()
            self._status('취소됐습니다.','Canceled.')
        elif self._job == 'prepare':
            self._prepared = value
            self.bar['value'] = 0
            self._open_pair_editor()
        elif self._job == 'analyze':
            self._reset_streams()
            self._prepared = None
            self.analysis = value
            self._update_analyze_label()
            self.bar['value'] = 100
            for role in self.paths:
                self._streams[role] = _PreviewStream(value[role+'_path'], value[role+'_meta'])
            self._status('분석 완료','Analysis complete')
            self._fill_table()
            self.seek(self._resume_frame, force=True)
        elif self._job == 'preview':
            self._preview = value
            self._streams['preview'] = _PreviewStream(str(value.path), self.analysis['change_meta'])
            self.bar['value'] = 100
            self.preview_after.set(True)
            self._status('미리보기 준비 완료', 'Preview ready')
            self.seek(self._position, force=True, playing=self.playing)
        else:
            self.bar['value'] = 100
            self.last_output = str(value.get('output') or self._output)
            count = len(value.get('unresolved_frames', []))
            ko = f' · 양끝 참조 부족 {count}프레임은 대응 영상 유지' if count else ''
            en = f' · {count} unbounded frames kept from the edit' if count else ''
            self._status('저장 완료' + ko + ' · ' + str(self._output), 'Saved' + en + ' · ' + str(self._output))
        self._controls()

    def _rows(self):
        return self.analysis['rows'] if self.analysis else ()

    def _current_source(self):
        return int(self.pair_source.get()) if self._editing_pair else self._position

    def _timeline_length(self):
        if self._editing_pair:
            return (self.analysis or self._prepared)['change_frames']
        return len(self._rows())

    def _playback_fps(self):
        media = self.analysis or (self._prepared if self._editing_pair else None)
        return media['change_meta' if self._editing_pair else 'source_meta'].get('fps', 0) if media else 0

    def _sync_graph(self):
        """Replace graph data only when the analysis, mode or locked source changes."""
        if self._output_editor:
            media = self.analysis
            source = int(self.pair_source.get())
            binding = ('candidates', id(media), source)
            if self._graph_binding != binding:
                matrix = getattr(media, 'candidate_costs', None)
                automatic = getattr(media, 'automatic_change', None)
                costs = () if matrix is None else matrix[:, source-1]
                marker = None if automatic is None else automatic[source-1]
                self.confidence_graph.set_candidates(source, costs, self._position, automatic_frame=marker)
        else:
            binding = ('overview', id(self.analysis))
            if self._graph_binding != binding:
                self.confidence_graph.set_rows(self._rows())
        self._graph_binding = binding
        self.confidence_graph.set_position(self._position if self._output_editor else self._current_source())

    def _reset_table_width(self, event=None):
        split = self.results_split
        if not split.winfo_ismapped():
            split.bind('<Map>', self._reset_table_width)
            return
        split.unbind('<Map>')
        if self.table_expanded:
            split.update_idletasks()
            split.sashpos(0, round(.4 * split.winfo_width()))

    def set_table_expanded(self, expanded):
        if self.table_expanded == expanded:
            return
        self.table_expanded = expanded
        split = self.results_split
        if expanded:
            split.add(self.table_pane, weight=6)
            if self._table_split_fraction is not None:
                split.update_idletasks()
                split.sashpos(0, round(self._table_split_fraction * split.winfo_width()))
        else:
            if split.winfo_width() > 1:
                self._table_split_fraction = split.sashpos(0) / split.winfo_width()
            split.forget(self.table_pane)
        self._table_toggle_label()

    def _table_toggle_label(self):
        arrow = '▾' if self.table_expanded else '▸'
        self.table_toggle.configure(text=arrow + _label(' 대응표', ' Frame table'))

    def _fill_table(self):
        rows = self._rows()
        for column, labels in zip(self.table['columns'], (
                ('원본 프레임', 'Source frame'), ('대응 상태', 'Status'),
                ('저장 방식', 'On export'), ('신뢰도', 'Confidence'))):
            self.table.heading(column, text=_label(*labels))
        actions = describe_reference_frames(self.analysis) if self.analysis else ()
        self._range_refs = {a['source_frame']: a['change_frame'] for a in actions if a['kind'] in ('use', 'override')}
        self._added_frames = {a['source_frame'] for a in actions if a.get('manual')}
        self._override_edits = {a['source_frame']: a['change_frame'] for a in actions if a['kind'] == 'override'}
        self._selected_edits = {a['source_frame']: a['change_frame'] for a in actions
                                if a.get('manual_anchor') or a['kind'] == 'override'}
        if self._override_edits:
            self.clear_override_button.grid()
        else:
            self.clear_override_button.grid_remove()
        if (self.analysis or {}).get('manual_ranges'):
            self.remove_range_button.grid()
        else:
            self.remove_range_button.grid_remove()
        plan_labels = [_plan_text(action) for action in actions]
        longest = max(plan_labels, key=len, default='')
        plan_width = 18 + self.tk.getint(self.tk.call('font', 'measure', 'TkDefaultFont', longest))
        self.table.column('plan', minwidth=max(155, plan_width))
        self._sync_graph()
        listed = rows
        if self.only_issues.get():
            listed = [r for r in rows if r['status'] != 'matched' or r['review']
                      or r['source_frame'] in self._added_frames or r['source_frame'] in self._override_edits]
        self._show_rows(listed, plan_labels)
        self._update_range_pick()
        if self.analysis:
            data = self.analysis
            missing = sum(r['status']=='missing' for r in data['rows'])
            held = sum(r['status']=='held' for r in data['rows'])
            generated = sum(a['kind'] == 'generate' for a in actions)
            self.summary.set(_label(f"원본 {data['source_frames']} · 편집본 {data['change_frames']}   |   누락 {missing} · 이미지 홀드 {held} · 생성 예정 {generated}장",
                                    f"Source {data['source_frames']} · Edit {data['change_frames']}   |   Missing {missing} · Image holds {held} · To generate {generated}"))
        else:
            self.summary.set('')
        length = self._timeline_length()
        self.frame_number.set(f'{self._position} / {length}' if length else '—')

    def _show_rows(self, rows, plan_labels):
        previous = self.table.get_children()
        if previous:
            self.table.delete(*previous)
        for row in rows:
            source = row['source_frame']
            code = 'manual' if row.get('manual') else row['status']
            if source in self._added_frames:
                code = 'added'
            if source in self._override_edits:
                code = 'override'
            state = _label(*_STATUS[code])
            if row['review'] and code not in ('review', 'added', 'override'):
                state += _label(' · 확인', ' · Review')
            score = None if code == 'override' else row.get('confidence')
            tags = (code, 'range_start') if source == self._range_start else (code,)
            self.table.insert('', 'end', iid=str(source), values=(source, state, plan_labels[source-1],
                              '—' if score is None else f'{score:.2f}'), tags=tags)
        if rows:
            self.empty.place_forget()
            current = str(self._current_source())
            if self.table.exists(current):
                self.table.selection_set(current)
                self.table.focus(current)
                self.table.see(current)
        else:
            self.empty.configure(text=_label('검토 대상이 없습니다.','No issues to review.') if self.analysis
                                 else _label('분석 후 대응 프레임이 표시됩니다.','Run Match Frames to review correspondences.'))
            self.empty.place(relx=.5, rely=.5, anchor='center', in_=self.table)

    def _select_row(self, _event):
        selected = self.table.selection()
        if selected and not self._editing_pair and int(selected[0]) != self._position:
            self.seek(int(selected[0]))

    def seek(self, number, *, force=False, playing=False, scrub=False):
        length = self._timeline_length()
        if not length:
            return
        number = max(1,min(length, int(number)))
        if self._editing_pair:
            pair = int(self.pair_source.get()), number
            self._set_pair_fields(pair)
        else:
            row = self._rows()[number-1]
            edit = row['change_frame'] if self._preview is not None else self._selected_edits.get(number, row['change_frame'])
            pair = row['source_frame'], edit
        self._seek_pair(number, pair, force=force, playing=playing, scrub=scrub)

    def _seek_pair(self, number, pair, *, force=False, playing=False, scrub=False):
        length = self._timeline_length()
        self.playing = playing
        self._position = number
        self._requested = pair
        self.confidence_graph.set_position(number if self._output_editor else pair[0])
        # Coalesce drag events before starting costly frame-accurate seeks.
        self._preview_due = time.monotonic() + .06 if scrub else 0
        self.scale.configure(to=max(2,length))
        self.timeline.set(number)
        self.frame_number.set(f'{number} / {length}')
        source = pair[0]
        if self.table.exists(str(source)):
            if self.table.selection() != (str(source),):
                self.table.selection_set(str(source))
                self.table.focus(str(source))
            self.table.see(str(source))
        elif self.table.selection():
            self.table.selection_remove(*self.table.selection())
        self._update_range_pick()
        if force or self._requested != self._displayed:
            self._present_pair()
        self._controls()

    def _difference_changed(self, _event=None):
        self._headers()
        self._draw('change')

    def _view_changed(self, _event=None):
        layout = _VIEW_LAYOUTS[self.view_box.current()][0]
        for column, (role, canvas) in enumerate(self.canvases.items()):
            visible = layout in ('both', role)
            canvas.master.columnconfigure(column, weight=int(visible), uniform='preview' if visible else '')
            if visible:
                self.preview_headers[role].grid()
                canvas.grid()
                self._draw(role)
            else:
                self.preview_headers[role].grid_remove()
                canvas.grid_remove()

    def _preview_changed(self):
        self._preview_due = 0
        self._present_pair()

    def _headers(self):
        if self._editing_pair:
            source = _label('고정 원본', 'Locked source')
            change = _label('사용할 편집본', 'Edit to use') if self._output_editor else _label('편집 후보', 'Edit candidate')
        elif self._displayed_after:
            source = _label('기준 원본', 'Original / Source')
            change = _label('보정 후', 'After')
        else:
            source = _label('기준 원본', 'Original / Source')
            change = _label('편집본', 'Video Edit / Change')
        if self._displayed:
            s, c = self._displayed
            source += f'  ·  {s}'
            if self._displayed_after:
                change = (_label(f'원본 {s} ↔ 보정 후 {s}', f'Source {s} ↔ After {s}') if self.difference.get()
                          else _label(f'보정 후 · {s}', f'After · {s}'))
            elif self.difference.get():
                change = _label(f'원본 {s} ↔ 편집 {c}', f'Source {s} ↔ Edit {c}')
            elif not self._editing_pair and self._preview is None and s in self._selected_edits:
                change = _label(f'지정한 편집본 · {c}', f'Selected edit · {c}')
            else:
                change += f'  ·  {c}'
            if self.analysis and not self._editing_pair and not self._displayed_after:
                row = self.analysis['rows'][s-1]
                if (row['status'] == 'missing' and c == row['change_frame']
                        and (self._preview is not None or s not in self._selected_edits)):
                    change += _label('  (최근접)', '  (nearest)')
        self.headers['source'].set(source)
        self.headers['change'].set(change)

    def _draw(self, role):
        canvas = self.canvases[role]
        if not canvas.winfo_manager():
            return
        image = self._images.get(role)
        difference = role == 'change' and self.difference.get()
        if difference:
            source = self._images.get('source')
            if self._displayed and source is not None and image is not None:
                image = _rgb_difference(source, image, int(self.difference_gain.get().removesuffix('×')))
            else:
                image = None
        width, height = canvas.winfo_width(),canvas.winfo_height()
        canvas.delete('all')
        if image is None:
            self._photos.pop(role, None)
            placeholder = _label('원본 미리보기','Original preview') if role=='source' else _label('편집본 미리보기','Edited preview')
            if difference:
                placeholder = _label('프레임 차이 미리보기', 'Frame difference preview')
            canvas.create_text(width/2,height/2,fill='#b2b5bb',text=placeholder)
            return
        ratio = min(width/image.width, height/image.height)
        resized = image.resize((max(1,round(image.width*ratio)),max(1,round(image.height*ratio))), Image.Resampling.BILINEAR)
        self._photos[role] = ImageTk.PhotoImage(resized, master=canvas)
        canvas.create_image(width/2,height/2,image=self._photos[role],anchor='center')

    def _present_pair(self):
        after = self._preview is not None and not self._editing_pair and self.preview_after.get()
        mode_changed = after != self._displayed_after
        if (not self.visible or not self._requested or (self._displayed == self._requested and not mode_changed)
                or time.monotonic() < self._preview_due):
            return
        try:
            source, edit = self._requested
            images = {'source': self._streams['source'].frame(source),
                      'change': self._streams['preview'].frame(source) if after else self._streams['change'].frame(edit)}
        except (ValueError,RuntimeError,OSError) as exc:
            if after:
                self._discard_preview()
                self._requested = (source, self._selected_edits.get(source, edit))
            else:
                self._requested = None
            self._displayed = None
            self._images.clear()
            for role in self.canvases:
                self._draw(role)
            self._headers()
            self.playing = False
            self._status(str(exc),str(exc))
            self._controls()
            return
        if all(im is not None for im in images.values()):
            source_changed = images['source'] is not self._images.get('source')
            self._images = images
            self._displayed = self._requested
            self._displayed_after = after
            if source_changed:
                self._draw('source')
            self._draw('change')
            self._headers()
            if self._editing_pair:
                self._pair_controls()
            fps = self._playback_fps()
            if fps > 0:
                self._deadline = time.monotonic() + 1/fps
        elif mode_changed and self._displayed is not None:
            # The new stream can take a tick to decode. Don't label the cached edit as corrected.
            self._displayed = None
            self._displayed_after = False
            self._images.pop('change', None)
            self._headers()
            self._draw('change')
            if self._editing_pair:
                self._pair_controls()

    def toggle_playback(self):
        if self._range_start is not None or not self._timeline_length() or not self._playback_fps():
            return
        if self.playing:
            self.playing = False
            self._controls()
        else:
            self.seek(1 if self._position >= self._timeline_length() else self._position, playing=True)
            self._deadline = time.monotonic()

    def _play_key(self):
        self.toggle_playback()
        return 'break'

    def _step_key(self, step):
        self.seek(self._position + step)
        return 'break'

    def _tick(self):
        if self.closed:
            return
        try:
            while True:
                self._job_event(*self.events.get_nowait())
        except queue.Empty:
            pass
        if self.visible:
            self._present_pair()
            if self.playing and self._displayed == self._requested and time.monotonic()>=self._deadline:
                if self._position >= self._timeline_length():
                    self.playing = False
                    self._controls()
                else:
                    self.seek(self._position+1, playing=True)
        pending = self.visible and self._requested is not None and self._requested != self._displayed
        self._tick_id = self.after(15 if self.playing or pending else 80, self._tick)

    def render_preview(self):
        if self.busy or self._editing_pair or not self.analysis or self._preview is not None:
            return
        from reference_render import render_reference_preview
        analysis = self.analysis
        quality = self._render_quality
        self._launch('preview', lambda progress, cancel: render_reference_preview(
            analysis, progress=progress, cancel=cancel, quality=quality))

    def _discard_pending_preview(self):
        # Whichever side of a window close receives the finished temp file owns its cleanup.
        while True:
            try:
                kind, value = self.events.get_nowait()
            except queue.Empty:
                return
            if kind == 'done':
                value.close()

    def render(self, output=None):
        if self.busy or self._editing_pair or not self.analysis:
            return
        path = Path(self.analysis['change_path'])
        extension = path.suffix.lower() or '.mov'
        output = output or filedialog.asksaveasfilename(parent=self,
            title=_label('원본 타이밍·오디오로 저장','Save with source timing and audio'),
            defaultextension=extension, initialfile=path.stem+'_framematch'+extension,
            filetypes=[(_label('편집본 형식','Edit format'),'*'+extension)])
        if output:
            analysis = self.analysis
            quality = self._render_quality
            self._output = output
            def export(progress, cancel):
                from reference_render import render_reference
                return render_reference(analysis, output, progress=progress, cancel=cancel, quality=quality)
            self._launch('render', export)

    def save_report(self):
        if self.busy or not self.analysis or self._editing_pair or self._range_start is not None:
            return
        path = filedialog.asksaveasfilename(parent=self, defaultextension='.json', filetypes=[('JSON','*.json')],
            initialfile=Path(self.analysis['change_path']).stem+'_framematch.json')
        if path:
            try:
                write_report(path, self.analysis, protected_paths=[self.analysis['source_path'],self.analysis['change_path'],
                                                                  getattr(self,'_output',None), self.last_output])
                self._status('저장 완료 · '+path, 'Saved · '+path)
            except (OSError, ValueError, TypeError) as exc:
                messagebox.showerror(_label('저장 실패','Export failed'), str(exc), parent=self)

    def on_hide(self):
        self.visible = self.playing = False
        self._end_range_pick()
        self._close_pair_editor()
        for stream in self._streams.values():
            stream.stop()
        self._controls()

    def on_show(self):
        self.visible = True
        self.set_render_quality(sdk_bridge.configured_quality())
        self.refresh_language()

    def close(self):
        self.closed = True
        self.stop.set()
        if self.busy and self._job == 'preview':
            self._discard_pending_preview()
        self._reset_streams()
        if self._tick_id is not None:
            self.after_cancel(self._tick_id)
            self._tick_id = None
