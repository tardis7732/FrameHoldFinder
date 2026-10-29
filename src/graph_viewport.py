"""Horizontal graph navigation, independent of motion analysis and Tk."""
import math


def _finite(value, name):
    try:
        if isinstance(value, bool):
            raise ValueError()
        value = float(value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError(f'{name} must be a finite number') from None
    if not math.isfinite(value):
        raise ValueError(f'{name} must be a finite number')
    return value


def _integer(value, name, minimum):
    value = _finite(value, name)
    if not value.is_integer() or value < minimum:
        raise ValueError(f'{name} must be an integer >= {minimum}')
    return int(value)


class FrameViewport:
    """A continuous display view over source frames 1 through N.

    Motion measurements still belong to arrival frames 2 through N. Including
    frame 1 here makes the whole source timeline seekable without inventing a
    measurement before the first frame.

    ``anchor`` is a frame coordinate. ``moveto`` takes a fraction of the full
    domain, as a Tk scrollbar does. ``visible_frames`` includes only integers
    inside the current floating boundaries. Invalid numeric inputs raise
    ValueError; finite pan/moveto/reveal positions are clamped to the domain.
    """
    def __init__(self, min_span=4):
        self.min_span = _finite(min_span, 'min_span')
        if self.min_span < 1:
            raise ValueError('min_span must be >= 1')
        self.reset()

    @staticmethod
    def _domain(frames):
        frames = _integer(frames, 'frames', 0)
        return frames, 1, max(1, frames)

    def reset(self, frames=0):
        frames, first, last = self._domain(frames)
        changed = (getattr(self, 'first', None), getattr(self, 'last', None),
                   getattr(self, 'left', None), getattr(self, 'right', None)) != (first, last, first, last)
        self.frames, self.first, self.last = frames, first, last
        self.left, self.right = float(first), float(last)
        return changed

    def set_frames(self, frames):
        frames, first, last = self._domain(frames)
        if (first, last) == (self.first, self.last):
            self.frames = frames
            return False
        return self.reset(frames)

    @property
    def span(self):
        return self.right - self.left

    @property
    def full_span(self):
        return self.last - self.first

    @property
    def zoomed(self):
        return self.span < self.full_span and not math.isclose(self.span, self.full_span, rel_tol=0, abs_tol=1e-9)

    @property
    def visible_frames(self):
        return range(max(self.first, math.ceil(self.left)), min(self.last, math.floor(self.right)) + 1)

    def _set_view(self, left, span):
        span = min(self.full_span, max(0., span))
        left = min(max(float(self.first), left), self.last - span)
        right = left + span
        if math.isclose(left, self.left, rel_tol=0, abs_tol=1e-9) and math.isclose(right, self.right, rel_tol=0, abs_tol=1e-9):
            return False
        self.left, self.right = left, right
        return True

    def zoom(self, factor, anchor=None):
        factor = _finite(factor, 'factor')
        if factor <= 0:
            raise ValueError('factor must be > 0')
        anchor = (self.left + self.right) / 2 if anchor is None else _finite(anchor, 'anchor')
        if self.full_span == 0:
            return False
        anchor = min(self.right, max(self.left, anchor))
        position = (anchor - self.left) / self.span
        new_span = min(self.full_span, max(min(self.min_span, self.full_span), self.span / factor))
        return self._set_view(anchor - position * new_span, new_span)

    def pan(self, delta):
        delta = _finite(delta, 'delta')
        return self._set_view(self.left + delta, self.span)

    def moveto(self, fraction):
        fraction = _finite(fraction, 'fraction')
        return self._set_view(self.first + min(1., max(0., fraction)) * self.full_span, self.span)

    def reveal(self, frame):
        frame = min(self.last, max(self.first, _finite(frame, 'frame')))
        if frame < self.left:
            return self._set_view(frame - .8 * self.span, self.span)
        if frame > self.right:
            return self._set_view(frame - .2 * self.span, self.span)
        return False

    def fractions(self):
        if not self.full_span:
            return 0., 1.
        return ((self.left - self.first) / self.full_span,
                (self.right - self.first) / self.full_span)

    def frame_at(self, fraction):
        fraction = min(1., max(0., _finite(fraction, 'fraction')))
        visible = self.visible_frames
        return min(visible.stop - 1, max(visible.start, int(round(self.left + fraction * self.span))))

    def position(self, frame):
        frame = _finite(frame, 'frame')
        return (frame - self.left) / self.span if self.span else .5

    def ticks(self, max_ticks):
        """Reserve both visible endpoints, then fit spaced 1/2/5-step ticks.

        Interior labels must leave enough room for the endpoint labels rather
        than displacing them. With a budget of one, only the midpoint fits.
        """
        maximum = _integer(max_ticks, 'max_ticks', 1)
        visible = self.visible_frames
        low, high = visible.start, visible.stop - 1
        if low == high:
            return [low]
        if maximum == 1:
            return [self.frame_at(.5)]
        if maximum == 2:
            return [low, high]
        spacing = max(1., (high - low) / (maximum - 1))
        power = 10 ** math.floor(math.log10(spacing))
        step = next(multiplier * power for multiplier in (1, 2, 5, 10) if multiplier * power >= spacing)
        step = max(1, int(step))
        first_tick = math.ceil((low + spacing) / step) * step
        last_tick = math.floor((high - spacing) / step) * step
        ticks = list(range(first_tick, last_tick + 1, step))
        return [low, *ticks, high]
