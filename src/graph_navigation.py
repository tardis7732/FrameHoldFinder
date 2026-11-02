"""The same zoom, pan, range and scale controls for both review graphs."""
import math
import tkinter as tk
from tkinter import ttk

from graph_viewport import FrameViewport
from i18n import tr


class GraphControls(ttk.Frame):
    def __init__(self, parent, *, frame_range, y_value, zoom, fit, scroll, apply_y, auto_y,
                 unit, min_y, max_y, increment):
        super().__init__(parent, style='Surface.TFrame')
        nav = ttk.Frame(self, style='Surface.TFrame')
        nav.pack(fill='x', pady=(0, 2))
        ttk.Label(nav, textvariable=frame_range, style='Muted.TLabel', width=13).pack(side='left')
        self.fit_button = ttk.Button(nav, width=4, style='Compact.TButton', command=fit)
        self.zoom_in = ttk.Button(nav, text='+', width=2, style='Compact.TButton', command=lambda: zoom(1.5))
        self.zoom_out = ttk.Button(nav, text='−', width=2, style='Compact.TButton', command=lambda: zoom(1/1.5))
        for button in (self.fit_button, self.zoom_in, self.zoom_out):
            button.pack(side='right', padx=(3, 0))
        self.scrollbar = ttk.Scrollbar(nav, orient='horizontal', command=scroll, takefocus=True,
                                       style='Graph.Horizontal.TScrollbar')
        self.scrollbar.pack(side='left', fill='x', expand=True, padx=(4, 0))

        scale = ttk.Frame(self, style='Surface.TFrame')
        scale.pack(fill='x', pady=(2, 4))
        self.y_label = ttk.Label(scale, style='Muted.TLabel')
        self.y_label.pack(side='left', padx=(0, 6))
        self.y_entry = ttk.Spinbox(scale, textvariable=y_value, from_=min_y, to=max_y,
                                   increment=increment, width=7, command=apply_y)
        self.y_entry.pack(side='left')
        self.y_entry.bind('<Return>', apply_y)
        self.y_entry.bind('<FocusOut>', apply_y)
        if unit:
            ttk.Label(scale, text=unit, style='Muted.TLabel').pack(side='left', padx=(4, 2))
        self.auto_button = ttk.Button(scale, width=4, style='Compact.TButton', command=auto_y)
        self.auto_button.pack(side='left', padx=(6, 0))
        self.refresh_language()

    def refresh_language(self):
        self.fit_button.configure(text=tr('전체'))
        self.y_label.configure(text=tr('세로 최대'))
        self.auto_button.configure(text=tr('자동'))


class GraphNavigation(GraphControls):
    def __init__(self, parent, *, on_view_change, unit='', min_y=.001):
        self.view = FrameViewport()
        self._on_view_change = on_view_change
        self._auto_y = 1.
        self._manual_y = None
        self._min_y = min_y
        self._frame_range = tk.StringVar(parent)
        self._y_value = tk.StringVar(parent, '1')
        self._position = lambda: 1
        self._pan_x = None
        super().__init__(parent, frame_range=self._frame_range, y_value=self._y_value,
                         zoom=self.zoom, fit=self.fit, scroll=self._scroll, apply_y=self._apply_y,
                         auto_y=self.auto_y, unit=unit, min_y=min_y, max_y=1, increment=.05)
        self._sync()

    @property
    def y_limit(self):
        return self._auto_y if self._manual_y is None else self._manual_y

    def set_data(self, frame_count, auto_y, reset=False):
        if reset or frame_count != self.view.frames:
            self.view.reset(frame_count)
            self._manual_y = None
        self._auto_y = min(1., max(self._min_y, auto_y))
        self._sync()

    def _sync(self):
        v = self.view
        visible = v.visible_frames
        label = '—' if not v.frames else str(visible.start)
        if v.frames and len(visible) > 1:
            label += f'–{visible.stop-1}'
        self._frame_range.set(label)
        self.scrollbar.set(*v.fractions())
        for widget, enabled in ((self.zoom_in, v.span > min(v.min_span, v.full_span) + 1e-8),
                                (self.zoom_out, v.zoomed), (self.scrollbar, v.zoomed),
                                (self.fit_button, v.zoomed or self._manual_y is not None),
                                (self.y_entry, v.frames > 0), (self.auto_button, self._manual_y is not None)):
            widget.state(['!disabled' if enabled else 'disabled'])
        if self.focus_get() != self.y_entry:
            self._y_value.set(f'{self.y_limit:.4g}')

    def _changed(self):
        self._sync()
        self._on_view_change()

    def zoom(self, factor, anchor=None):
        if anchor is None:
            pos = self._position()
            anchor = pos if self.view.left <= pos <= self.view.right else (self.view.left + self.view.right)/2
        if self.view.zoom(factor, anchor):
            self._changed()

    def reveal(self, number):
        changed = self.view.reveal(number)
        if changed:
            self._changed()
        return changed

    def fit(self):
        self.view.reset(self.view.frames)
        self.auto_y()

    def auto_y(self):
        self._manual_y = None
        self._y_value.set(f'{self.y_limit:.4g}')
        self._changed()

    def _apply_y(self, event=None):
        if event is not None and event.type == tk.EventType.KeyPress and event.state & 4:
            return
        if not self.view.frames:
            return 'break'
        try:
            new_y = float(self._y_value.get())
            if math.isfinite(new_y) and self._min_y <= new_y <= 1 and new_y != float(f'{self.y_limit:.4g}'):
                self._manual_y = new_y
                self._changed()
        except ValueError:
            pass
        self._y_value.set(f'{self.y_limit:.4g}')
        return 'break'

    def _scroll(self, action, value, units=None):
        if action == 'moveto':
            changed = self.view.moveto(float(value))
        else:
            stride = self.view.span*.8 if units == 'pages' else max(1, self.view.span*.1)
            changed = self.view.pan(int(value)*stride)
        if changed:
            self._changed()

    def bind_canvas(self, canvas, bounds, position, *, enabled=lambda: True):
        self._position = position

        def wheel(event):
            if not enabled():
                return 'break'
            left, _, right, _ = bounds()
            if not self.view.frames or not left <= event.x <= right:
                return 'break'
            steps = {4: 1, 5: -1}.get(event.num, max(-3, min(3, event.delta / 120)))
            if event.state & 4:
                self._manual_y = min(1., max(self._min_y, self.y_limit / 1.5**steps))
                self._y_value.set(f'{self.y_limit:.4g}')
                self._changed()
            elif event.state & 1:
                if self.view.pan(-steps*max(1, self.view.span*.15)):
                    self._changed()
            else:
                anchor = self.view.left + (event.x-left)/max(1, right-left)*self.view.span
                self.zoom(1.5**steps, anchor)
            return 'break'

        def pan_start(event):
            if enabled() and self.view.zoomed:
                self._pan_x = event.x
                canvas.configure(cursor='fleur')

        def pan_drag(event):
            if enabled() and self._pan_x is not None:
                left, _, right, _ = bounds()
                delta = (self._pan_x-event.x)*self.view.span/max(1, right-left)
                self._pan_x = event.x
                if self.view.pan(delta):
                    self._changed()

        def pan_end(_event):
            self._pan_x = None
            canvas.configure(cursor='')

        for sequence in ('<MouseWheel>', '<Button-4>', '<Button-5>'):
            canvas.bind(sequence, wheel)
        canvas.bind('<Button-2>', pan_start)
        canvas.bind('<B2-Motion>', pan_drag)
        canvas.bind('<ButtonRelease-2>', pan_end)
