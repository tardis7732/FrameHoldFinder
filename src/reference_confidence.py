"""Source timeline confidence and local costs for the edited-frame chooser."""
from dataclasses import dataclass, field
from itertools import groupby
import math
import tkinter as tk
from tkinter import ttk

from graph_marks import draw_playhead
from graph_navigation import GraphNavigation
from graph_viewport import FrameViewport
from i18n import language
from ui_support import PALETTE


_NORMAL = '#4c86a8'
_REVIEW = '#d97b24'
_MISSING = '#a65a09'
_MIN_AUTO_PEAK = .25
_CANDIDATE_RADIUS = 12
_FONT = ('맑은 고딕', 8)


@dataclass
class _ScoreColumn:
    issue: float | None = None
    clipped: bool = False
    unscored: bool = False
    manual: bool = False
    held: bool = False
    missing: list[int] = field(default_factory=list)

    def add(self, number, row, ymax):
        score = row.get('confidence')
        if row.get('manual'):
            self.manual = True
        elif row.get('status') == 'missing':
            self.missing.append(number)
        elif score is None:
            self.unscored = True
        else:
            issue = (row.get('review') or row.get('status') in ('review', 'held')
                     or row.get('event') in ('hold_candidate', 'drop_candidate'))
            self.held |= row.get('status') == 'held'
            self.clipped |= score > ymax
            if issue:
                self.issue = score if self.issue is None else min(self.issue, score)


def _draw_trace(canvas, samples, tag):
    """Connect measured frames in order. An unmeasured frame breaks the line."""
    for measured, run in groupby(samples, key=lambda p: p[1] is not None):
        if not measured:
            continue
        points = []
        for _, column in groupby(run, key=lambda p: round(p[0])):
            bucket = list(column)
            if len(bucket) > 4:
                # Keep both ends and extrema in time order at subpixel zoom.
                bucket = sorted({bucket[0], bucket[-1], min(bucket, key=lambda p: p[1]),
                                 max(bucket, key=lambda p: p[1])})
            points.extend(bucket)
        if len(points) == 1:
            x, y = points[0]
            points = [(x - 1, y), (x + 1, y)]
        canvas.create_line(*points, fill=_NORMAL, width=1, tags=tag)


class ConfidenceGraph(ttk.Frame):
    """Rows are the complete, ordered timeline, including missing frames."""
    def __init__(self, parent, on_seek, *, can_seek=None):
        super().__init__(parent, style='Surface.TFrame', padding=(8, 4))
        self._on_seek = on_seek
        self._can_seek = can_seek
        self._rows = ()
        self._missing_marks = {}
        self._position = 0
        self._redraw_id = None
        self._candidate_costs = None
        self._candidate_source = None
        self._automatic_frame = None
        self._candidate_view = FrameViewport()
        self._first_candidate = 1
        self._overview_position = 0

        head = ttk.Frame(self, style='Surface.TFrame')
        head.pack(fill='x')
        self._title = ttk.Label(head, style='Section.TLabel')
        self._title.pack(side='left')
        self._value = ttk.Label(head, style='Muted.TLabel', anchor='e')
        self._value.pack(side='right')
        self.canvas = tk.Canvas(self, width=160, height=88, background=PALETTE['surface'],
                                bd=0, highlightthickness=0, takefocus=True)
        self.canvas.pack(fill='both', expand=True)
        self.navigation = GraphNavigation(self, on_view_change=self._request_draw, unit='', min_y=.001)
        self.navigation.pack(fill='x')
        self._view = self.navigation.view
        self.navigation.bind_canvas(self.canvas, self._bounds, lambda: self._position,
                                    enabled=lambda: self._candidate_costs is None)
        self._legend = ttk.Label(self, style='Muted.TLabel', font=_FONT, justify='left')
        self._legend.pack(fill='x')
        self.canvas.bind('<Configure>', self._resize)
        self.canvas.bind('<Button-1>', self._press)
        self.canvas.bind('<B1-Motion>', self._drag)
        self.canvas.bind('<Left>', lambda _: self._seek(self._position - 1))
        self.canvas.bind('<Right>', lambda _: self._seek(self._position + 1))
        self.canvas.bind('<Home>', lambda _: self._seek(1))
        self.canvas.bind('<End>', lambda _: self._seek(self._frame_count()))
        self.refresh_language()

    def set_rows(self, rows):
        if self._candidate_costs is not None:
            self._candidate_costs = None
            self._position = self._overview_position
            self.navigation.pack(fill='x', before=self._legend)
            self._labels()
        self._rows = rows
        peak = max((r['confidence'] for r in rows if r.get('confidence') is not None and not r.get('manual')),
                   default=None)
        auto_y = 1. if peak is None else min(1., 1.1 * max(_MIN_AUTO_PEAK, peak))
        self.navigation.set_data(len(rows), auto_y)
        self._position = min(max(self._position, 1), len(rows))
        self._request_draw()

    def set_candidates(self, source_frame, costs, change_frame, automatic_frame=None):
        """Bind indexed Edit costs for one fixed Source; keep the overview zoom."""
        entering = self._candidate_costs is None
        if entering:
            self._overview_position = self._position
            self.navigation.pack_forget()
        recenter = (entering or self._candidate_source != source_frame
                    or len(self._candidate_costs) != len(costs))
        changed = costs is not self._candidate_costs or automatic_frame != self._automatic_frame
        self._candidate_costs = costs
        self._candidate_source = source_frame
        self._automatic_frame = automatic_frame
        position = min(max(1, change_frame), len(costs))
        if not recenter and not changed:
            self.set_position(position)
            return
        self._position = position
        if recenter or not self._candidate_visible(position):
            self._center_candidates(position)
        self._labels()
        self._request_draw()

    def _center_candidates(self, position):
        self._first_candidate = max(1, position - _CANDIDATE_RADIUS)
        end = min(len(self._candidate_costs), position + _CANDIDATE_RADIUS)
        self._candidate_view.reset(end - self._first_candidate + 1)

    def _candidate_visible(self, number):
        return self._first_candidate <= number < self._first_candidate + self._candidate_view.frames

    def _frame_count(self):
        return len(self._candidate_costs) if self._candidate_costs is not None else len(self._rows)

    def _cost(self, number):
        raw = self._candidate_costs[number - 1]
        if raw is not None:
            cost = float(raw)
            if math.isfinite(cost) and 0 <= cost < 10:
                return cost
        return None

    def set_position(self, number):
        position = min(max(int(number), 1), self._frame_count())
        if self._position != position:
            self._position = position
            if self._candidate_costs is not None:
                if not self._candidate_visible(position):
                    self._center_candidates(position)
                    self._labels()
                    self._request_draw()
            elif position:
                self.navigation.reveal(position)
            if self._redraw_id is None:
                self._cursor()

    def refresh_language(self):
        self._labels()
        self.navigation.refresh_language()
        self._request_draw()

    def _labels(self):
        english = language() == 'en'
        if self._candidate_costs is not None:
            number = self._candidate_source
            self._title.configure(text=f'Source {number} · Match cost' if english else f'원본 {number} · 매칭 비용')
            auto = self._automatic_frame
            if auto is None:
                auto_label = 'No auto match' if english else '자동 대응 없음'
            elif self._candidate_visible(auto):
                auto_label = f'○ Auto {auto}' if english else f'○ 자동 {auto}'
            else:
                auto_label = f'Auto {auto} (off screen)' if english else f'자동 {auto} (범위 밖)'
            self._legend.configure(text=f'Lower is closer  ·  {auto_label}  ·  ▼ Selected  ·  □ No score'
                                   if english else f'낮을수록 유사 · {auto_label} · ▼ 선택 · □ 점수 없음')
            return
        self._title.configure(text='Match confidence' if english else '프레임 매치 신뢰도')
        self._legend.configure(text='▲ Missing  ·  Orange: review  ·  Pink: image hold  ·  □ No score  ·  ◆ Manual'
                               if english else '▲ 누락 후보 · 주황 검토 · 분홍 홀드 · □ 점수 없음 · ◆ 수동 지정')

    def _bounds(self):
        left = 47 if self._candidate_costs is not None else 32
        return (left, 11, max(left + 1, self.canvas.winfo_width() - 19),
                max(12, self.canvas.winfo_height() - 35))

    def _resize(self, event):
        self._legend.configure(wraplength=max(100, event.width))
        self._request_draw()

    def _request_draw(self):
        if self._redraw_id is None:
            self._redraw_id = self.after_idle(self._draw)

    def destroy(self):
        if self._redraw_id is not None:
            self.after_cancel(self._redraw_id)
        super().destroy()

    def _press(self, event):
        self.canvas.focus_set()
        return self._drag(event)

    def _drag(self, event):
        left, _, right, _ = self._bounds()
        fraction = (event.x - left) / (right - left)
        if self._candidate_costs is not None:
            return self._seek(self._first_candidate - 1 + self._candidate_view.frame_at(fraction))
        number = self._view.frame_at(fraction)
        if self._missing_marks:
            hits = self.canvas.find_overlapping(event.x-.5, event.y-.5, event.x+.5, event.y+.5)
            frames = (n for item in hits for n in self._missing_marks.get(item, ()))
            # A single triangle may represent several frames on a zoomed-out timeline.
            target = self._view.left + fraction * self._view.span
            number = min(frames, key=lambda n: abs(n - target), default=number)
        return self._seek(number)

    def _seek(self, number):
        if self._frame_count() and (self._can_seek is None or self._can_seek()):
            number = max(1, min(number, self._frame_count()))
            self.set_position(number)
            self._on_seek(number)
        return 'break'

    def _draw(self):
        self._redraw_id = None
        c = self.canvas
        self._missing_marks.clear()
        c.delete('all')
        left, top, right, bottom = self._bounds()
        if self._candidate_costs is not None:
            self._draw_candidates(left, top, right, bottom)
            self._cursor()
            return
        if not self._rows:
            c.create_text((left + right) / 2, (top + bottom) / 2, fill=PALETTE['muted'],
                          text='Run Match Frames to see confidence.' if language() == 'en'
                               else '프레임 매치 후 표시됩니다.')
            self._value.configure(text='')
            return
        ymax = self.navigation.y_limit
        height = bottom - top
        columns = {}
        samples = []
        for number in self._view.visible_frames:
            x = left + self._view.position(number) * (right - left)
            pixel = round(x)
            row = self._rows[number - 1]
            if pixel not in columns:
                columns[pixel] = _ScoreColumn()
            columns[pixel].add(number, row, ymax)
            score = row.get('confidence')
            y = None if row.get('manual') or score is None else bottom - min(ymax, score) / ymax * height
            samples.append((x, y))
        cell = (right - left) / max(self._view.span, 1)
        for x, col in columns.items():
            if col.held or col.missing:
                half = max(.5, cell * .44)
                band, tag = ('#fbe7ee', 'hold_frame') if col.held else ('#fff0d6', 'missing_band')
                c.create_rectangle(max(left, x - half), top, min(right, x + half), bottom,
                                   fill=band, outline='', tags=tag)
        for score in (ymax, ymax / 2, 0.):
            y = bottom - (score / ymax) * height
            c.create_line(left, y, right, y, fill='#eeeeee', tags='grid')
            c.create_text(left - 4, y, text=f'{score:.3g}', anchor='e',
                          font=_FONT, fill=PALETTE['muted'])
        for number in self._view.ticks(max(2, (right - left) // 60)):
            x = left + self._view.position(number) * (right - left)
            c.create_text(x, bottom + 21, text=str(number), font=_FONT, fill=PALETTE['muted'])
        _draw_trace(c, samples, 'confidence_trace')
        for x, col in columns.items():
            if col.issue is not None:
                y = bottom - min(ymax, col.issue) / ymax * height
                c.create_oval(x-2, y-2, x+2, y+2, fill=_REVIEW, outline='', tags='issue')
            if col.clipped:
                c.create_line(x-2, top-5, x, top-8, x+2, top-5, fill=PALETTE['muted'], tags='clipped')
            rail = bottom + 8
            if col.unscored:
                c.create_rectangle(x-2, rail-2, x+2, rail+2, outline=PALETTE['muted'], tags='no_score')
            if col.missing:
                half = min(4, max(1, cell * .4))
                marker = c.create_polygon(x, rail-4, x+half, rail+3, x-half, rail+3,
                                          fill=_MISSING, outline='', tags='missing_frame')
                self._missing_marks[marker] = col.missing
            if col.manual:
                c.create_polygon(x, rail-4, x+4, rail, x, rail+4, x-4, rail,
                                 fill=PALETTE['primary'], outline='', tags='manual')
        c.tag_raise('issue')
        self._cursor()

    def _draw_candidates(self, left, top, right, bottom):
        c = self.canvas
        english = language() == 'en'
        view = self._candidate_view
        if not view.frames:
            c.create_text((left+right)/2, (top+bottom)/2, fill=PALETTE['muted'],
                          text='Waiting for match costs' if english else '매칭 비용 대기 중')
            return
        costs = [self._cost(self._first_candidate + i) for i in range(view.frames)]
        valid = [cost for cost in costs if cost is not None]
        if valid:
            low, high = min(valid), max(valid)
            margin = max((high - low) * .12, high * .001, .000001)
            floor, ceiling = max(0., low - margin), high + margin
            scale = (bottom - top) / (ceiling - floor)
            for value in (floor, (floor+ceiling)/2, ceiling):
                y = bottom - (value-floor)*scale
                c.create_line(left, y, right, y, fill='#eeeeee', tags='cost_grid')
                c.create_text(left-4, y, text=f'{value:.4g}', anchor='e', font=_FONT, fill=PALETTE['muted'])
        else:
            c.create_text((left+right)/2, (top+bottom)/2, fill=PALETTE['muted'],
                          text='No valid cost in this range' if english else '이 범위에 유효한 매칭 비용이 없습니다.')

        for tick in view.ticks(max(2, (right-left)//55)):
            x = left + view.position(tick)*(right-left)
            c.create_text(x, bottom+21, text=str(self._first_candidate+tick-1), font=_FONT, fill=PALETTE['muted'])
        samples = []
        for index, cost in enumerate(costs):
            number = self._first_candidate + index
            x = left + view.position(index+1)*(right-left)
            frame_tag = f'cost_frame:{number}'
            automatic = number == self._automatic_frame
            auto_tags = ('auto_match', f'auto_frame:{number}') if automatic else ()
            y = None if cost is None else bottom - (cost-floor)*scale
            samples.append((x, y))
            if cost is None:
                y = bottom + 8
                c.create_rectangle(x-2, y-2, x+2, y+2, outline=PALETTE['muted'], tags=('no_score', frame_tag))
            if automatic:
                c.create_oval(x-4, y-4, x+4, y+4, outline=PALETTE['primary'],
                              fill=PALETTE['surface'] if cost is not None else '', width=1.5, tags=auto_tags)
        _draw_trace(c, samples, 'cost_trace')
        c.tag_raise('auto_match')

    def _cursor(self):
        c = self.canvas
        left, top, right, bottom = self._bounds()
        english = language() == 'en'
        if self._candidate_costs is not None:
            if self._position:
                x = left + self._candidate_view.position(self._position-self._first_candidate+1)*(right-left)
                draw_playhead(c, x, top, bottom)
                c.tag_raise('auto_match')
                cost = self._cost(self._position)
                value = '—' if cost is None else f'{cost:.5g}'
                label = 'Edit' if english else '편집본'
                self._value.configure(text=f'{label} {self._position}  ·  {value}')
            else:
                self._value.configure(text='')
            return
        x = None
        if self._position and self._view.left <= self._position <= self._view.right:
            x = left + self._view.position(self._position) * (right - left)
        draw_playhead(c, x, top, bottom)
        if not self._position:
            self._value.configure(text='')
            return
        timeline = 'Source' if english else '원본'
        row = self._rows[self._position-1]
        score = row.get('confidence')
        if row.get('manual'):
            label = 'Manual' if english else '수동 지정'
        elif row.get('status') == 'missing':
            label = 'Missing' if english else '누락 후보'
        else:
            label = '—' if score is None else f'{score:.2f}'
        self._value.configure(text=f'{timeline} {self._position}  ·  {label}')
