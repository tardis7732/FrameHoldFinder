"""Small, local UI helpers shared by the review workspace and player."""
import math
import tkinter as tk
from i18n import tr, DisplayVar
from tkinter import ttk


PALETTE = {
    'app': '#f5f5f5', 'surface': '#ffffff', 'text': '#18181b', 'muted': '#52525b',
    'primary': '#1d4ed8', 'primary_hover': '#1e40af', 'primary_pressed': '#1e3a8a',
    'primary_fg': '#ffffff', 'secondary': '#27272a', 'secondary_hover': '#3f3f46',
    'secondary_fg': '#ffffff', 'border': '#8b8b93', 'focus': '#1d4ed8',
    'track': '#e4e4e7', 'subtle': '#f4f4f5', 'hover': '#e4e4e7',
    'disabled_bg': '#f4f4f5', 'disabled_fg': '#a1a1aa', 'disabled_border': '#c7c7ce',
    'button_secondary': '#334155', 'button_secondary_fg': '#ffffff',
    'button_secondary_hover': '#475569', 'button_secondary_pressed': '#1e293b',
    'button_neutral': '#e4e4e7', 'button_neutral_hover': '#d4d4d8', 'button_neutral_pressed': '#c4c4cc',
    'button_border': '#71717a', 'button_hover_border': '#62626b', 'excluded': '#71717a',
    'scroll_thumb': '#d7d7d7', 'scroll_hover': '#bcbcbc', 'tooltip_bg': '#363636', 'tooltip_fg': '#ffffff',
}


def motion_graph_limit(metrics):
    """Fit visible original/corrected scores with ten percent headroom."""
    values=(float(metric.get(key,0)) for metric in metrics for key in ('score','raw_score'))
    maximum=max((value for value in values if math.isfinite(value) and value>0),default=0.)
    return maximum*1.1 if maximum else 1.


def remove_dotted_focus(style, name, padding, focus_padding=1):
    """Remove only focus drawing, replacing its reserved space with padding."""
    def strip(nodes):
        result = []
        for element, options in nodes:
            options = dict(options)
            children = strip(options.get('children', []))
            if element.rsplit('.', 1)[-1] == 'focus':
                for child, child_options in children:
                    child_options = dict(child_options)
                    for key in ('side', 'sticky', 'expand'):
                        if key in options:child_options.setdefault(key, options[key])
                    result.append((child, child_options))
            else:
                if 'children' in options:options['children'] = children
                result.append((element, options))
        return result
    style.layout(name, strip(style.layout(name)))
    values = (padding,) if isinstance(padding, int) else tuple(padding)
    style.configure(name, padding=tuple(value + focus_padding for value in values), focusthickness=0)


def apply_design_style(style):
    """Semantic roles adapted from UI/UX Pro Max and shadcn/Radix guidance."""
    p = PALETTE
    style.configure('.', font=('맑은 고딕', 10), background=p['app'], foreground=p['text'])
    style.configure('Surface.TFrame', background=p['surface'])
    style.configure('Surface.TLabel', background=p['surface'], foreground=p['text'])
    style.configure('Muted.TLabel', background=p['surface'], foreground=p['muted'])
    style.configure('Section.TLabel', background=p['surface'], foreground=p['text'])
    style.configure('Surface.TCheckbutton', background=p['surface'], foreground=p['text'],
                    focuscolor=p['focus'], focusthickness=2, padding=0)
    style.map('Surface.TCheckbutton', background=[('active', p['surface'])],
              foreground=[('disabled', p['disabled_fg'])])
    # Player.TCheckbutton inherits this layout and indicator focus state.
    # A zero thickness alone does not suppress Tk's dotted focus renderer.
    remove_dotted_focus(style, 'TCheckbutton', padding=2, focus_padding=1)
    remove_dotted_focus(style, 'Surface.TCheckbutton', padding=0, focus_padding=2)
    remove_dotted_focus(style, 'TMenubutton', padding=5, focus_padding=1)
    style.map('TCheckbutton',
              upperbordercolor=[('disabled', p['disabled_border']), ('focus', p['focus'])],
              lowerbordercolor=[('disabled', p['disabled_border']), ('focus', p['focus'])])
    for name, background, foreground, padding in (
            ('TButton', p['button_secondary'], p['button_secondary_fg'], (12, 6)),
            ('Compact.TButton', p['button_neutral'], p['text'], (9, 4)),
            ('Tertiary.TButton', p['surface'], p['primary'], (8, 5)),
            ('Accent.TButton', p['secondary'], p['secondary_fg'], (12, 6)),
            ('Primary.TButton', p['primary'], p['primary_fg'], (14, 7)),
            ('Tools.TMenubutton', p['button_neutral'], p['text'], (7, 5))):
        primary = name == 'Primary.TButton'
        accent = name == 'Accent.TButton'
        secondary = name == 'TButton'
        tertiary = name == 'Tertiary.TButton'
        filled = primary or accent or secondary
        border = (p['primary_hover'] if primary else p['text'] if accent else
                  p['button_secondary_pressed'] if secondary else p['primary'] if tertiary else p['button_border'])
        hover = (p['primary_hover'] if primary else p['secondary_hover'] if accent else
                 p['button_secondary_hover'] if secondary else p['subtle'] if tertiary else p['button_neutral_hover'])
        pressed = (p['primary_pressed'] if primary else p['text'] if accent else
                   p['button_secondary_pressed'] if secondary else p['hover'] if tertiary else p['button_neutral_pressed'])
        hover_border = border if filled else p['primary_hover'] if tertiary else p['button_hover_border']
        pressed_border = border if filled else p['primary_pressed'] if tertiary else p['muted']
        focus_border = p['text'] if primary else p['primary_pressed'] if tertiary else p['focus']
        # Clam suppresses its entire border element for relief="flat" and
        # reserves two pixels even for borderwidth=1. Raised relief draws the
        # outer outline; matching both inner edges to the fill leaves exactly
        # one visible pixel without a bevel or a state-dependent size change.
        style.configure(name, background=background, foreground=foreground, padding=padding,
                        borderwidth=1, relief='raised', lightcolor=background, darkcolor=background,
                        bordercolor=border, focuscolor=p['primary_fg'] if filled else p['focus'],
                        focusthickness=2, width=0,
                        font=('맑은 고딕', 10, 'bold' if primary else 'normal'))
        state_colors = [('disabled', p['disabled_bg']), ('pressed', pressed), ('active', hover), ('!disabled', background)]
        style.map(name, background=state_colors,
                  foreground=[('disabled', p['disabled_fg']), ('!disabled', foreground)],
                  relief=[('disabled', 'raised'), ('pressed', 'sunken'), ('!disabled', 'raised')],
                  bordercolor=[('disabled', p['disabled_border']), ('focus', focus_border),
                               ('pressed', pressed_border), ('active', hover_border),
                               ('!disabled', border)],
                  lightcolor=state_colors, darkcolor=state_colors)
        if name == 'Tools.TMenubutton':
            # Offset the newly reserved border space so this toolbar control
            # keeps its previous footprint as closely as possible.
            style.configure(name, arrowcolor=foreground)
            style.map(name, arrowcolor=[('disabled', p['disabled_fg']), ('!disabled', foreground)])
        remove_dotted_focus(style, name, padding=padding, focus_padding=2)
    for name in ('TEntry', 'TSpinbox', 'TCombobox'):
        style.configure(name, fieldbackground=p['surface'], background=p['surface'], padding=(6, 4),
                        foreground=p['text'], bordercolor=p['border'], lightcolor=p['surface'],
                        darkcolor=p['surface'], arrowcolor=p['muted'], arrowsize=11, borderwidth=1)
        style.map(name, fieldbackground=[('disabled', p['disabled_bg']), ('readonly', p['surface'])],
                  foreground=[('disabled', p['disabled_fg'])],
                  bordercolor=[('focus', p['focus']), ('!focus', p['border'])],
                  lightcolor=[('focus', p['focus']), ('!focus', p['surface'])],
                  darkcolor=[('focus', p['focus']), ('!focus', p['surface'])])
    style.configure('Treeview', rowheight=30, background=p['surface'], fieldbackground=p['surface'],
                    foreground=p['text'], borderwidth=0, relief='flat', lightcolor=p['surface'],
                    darkcolor=p['surface'], bordercolor=p['surface'])
    style.configure('Treeview.Heading', background=p['subtle'], foreground=p['muted'],
                    padding=(7, 8), relief='flat', borderwidth=0, lightcolor=p['subtle'],
                    darkcolor=p['subtle'], bordercolor=p['subtle'], font=('맑은 고딕', 9))
    style.map('Treeview', background=[('selected', p['primary'])], foreground=[('selected', p['primary_fg'])])
    style.map('Treeview.Heading', background=[('active', p['hover'])], relief=[('active', 'flat')])
    for orientation in ('Vertical', 'Horizontal'):
        prefix = orientation + '.Scrollbar'
        style.layout(orientation + '.TScrollbar', [(prefix + '.trough', {
            'sticky': 'nswe', 'children': [(prefix + '.thumb', {'sticky': 'nswe', 'expand': '1'})]})])
        style.configure(orientation + '.TScrollbar', background=p['scroll_thumb'], troughcolor=p['surface'],
                        bordercolor=p['surface'], lightcolor=p['scroll_thumb'], darkcolor=p['scroll_thumb'],
                        borderwidth=0, width=8, arrowsize=8, gripcount=0)
        style.map(orientation + '.TScrollbar', background=[('active', p['scroll_hover'])])
    style.configure('Horizontal.TProgressbar', background=p['primary'], troughcolor=p['subtle'],
                    borderwidth=0, lightcolor=p['primary'], darkcolor=p['primary'], bordercolor=p['subtle'], thickness=3)
    style.configure('TPanedwindow', background=p['app'])


class ToolTip:
    def __init__(self, widget, text, delay=450):
        self.widget = widget
        self.text = text
        self.delay = delay
        self.pending = None
        self.window = None
        widget.bind('<Enter>', self.schedule, add='+')
        widget.bind('<Leave>', self.hide, add='+')
        widget.bind('<ButtonPress>', self.hide, add='+')
        widget.bind('<FocusOut>', self.hide, add='+')
        widget.bind('<Destroy>', self.destroyed, add='+')

    def schedule(self, _=None):
        self.hide()
        self.pending = self.widget.after(self.delay, self.show)

    def show(self):
        self.pending = None
        try:
            if not self.widget.winfo_ismapped():
                return
            text = self.text() if callable(self.text) else self.text
            if not text:
                return
            window = self.window = tk.Toplevel(self.widget)
            window.withdraw()
            window.overrideredirect(True)
            window.transient(self.widget.winfo_toplevel())
            tk.Label(window, text=tr(text), justify='left', wraplength=330,
                     background=PALETTE['tooltip_bg'], foreground=PALETTE['tooltip_fg'], padx=10, pady=7,
                     font=('맑은 고딕', 9), borderwidth=0).pack()
            window.update_idletasks()
            x = self.widget.winfo_rootx() + 8
            y = self.widget.winfo_rooty() + self.widget.winfo_height() + 5
            x = min(x, self.widget.winfo_screenwidth() - window.winfo_reqwidth() - 8)
            if y + window.winfo_reqheight() > self.widget.winfo_screenheight() - 8:
                y = self.widget.winfo_rooty() - window.winfo_reqheight() - 5
            window.geometry(f'+{max(0,x)}+{max(0,y)}')
            window.deiconify()
        except tk.TclError:
            self.hide()

    def hide(self, _=None):
        if self.pending is not None:
            try:
                self.widget.after_cancel(self.pending)
            except tk.TclError:
                pass
            self.pending = None
        if self.window is not None:
            try:
                self.window.destroy()
            except tk.TclError:
                pass
            self.window = None

    def destroyed(self, event):
        if event.widget is self.widget:
            self.hide()


def attach_tooltip(widget, text):
    existing = getattr(widget, '_motion_tooltip', None)
    if existing is not None:
        existing.text = text
        return existing
    tooltip = ToolTip(widget, text)
    widget._motion_tooltip = tooltip
    return tooltip
