"""Canvas marks shared by the motion and reference confidence graphs."""

def draw_stem(canvas, x, bottom, y, color, *, hollow=False, dashed=False,
              stem_tags=(), dot_tags=(), radius=3):
    canvas.create_line(x, bottom, x, y, fill=color, width=2, dash=(3, 3) if dashed else (), tags=stem_tags)
    canvas.create_oval(x-radius, y-radius, x+radius, y+radius, outline=color,
                       fill='white' if hollow else color, width=1.5, tags=dot_tags)


def draw_playhead(canvas, x, top, bottom):
    canvas.delete('playhead')
    if x is not None:
        canvas.create_line(x, top, x, bottom, fill='#24354a', dash=(3, 3), tags='playhead')
        canvas.create_text(x, top+1, text='▼', fill='#24354a', anchor='n', tags='playhead')
