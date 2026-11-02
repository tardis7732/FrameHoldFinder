"""Shared spatial alignment for the Video Edit matcher and Difference viewer."""
from fractions import Fraction

from source_format import _sar


def sample_aspect_ratio(value):
    """Assume square pixels when the stream has no usable sample aspect ratio."""
    return _sar(value) or Fraction(1)


def width_overlap_boxes(source_size, change_size, source_sar=None, change_sar=None):
    """Return pixel boxes covering the shared area at equal display width."""
    source_width = source_size[0] * sample_aspect_ratio(source_sar)
    change_width = change_size[0] * sample_aspect_ratio(change_sar)
    height_per_width = min(source_size[1] / source_width, change_size[1] / change_width)

    def centered_box(size, display_width):
        width, height = size
        shared_height = max(1, round(display_width * height_per_width))
        top = (height - shared_height) // 2
        return 0, top, width, top + shared_height

    return centered_box(source_size, source_width), centered_box(change_size, change_width)
