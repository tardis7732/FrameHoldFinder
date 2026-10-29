"""Suggested starting value and validation for the existing pixel input."""
import math


DEFAULT_MIN_MOVE_RATIO = 0.005


def _positive_number(value, message):
    try:
        if isinstance(value, bool):
            raise ValueError()
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError(message) from None
    if not math.isfinite(number) or number <= 0:
        raise ValueError(message)
    return number


def default_min_move(analysis_width):
    """Seed the pixel field with actual analysis width times 0.005."""
    width = _positive_number(analysis_width, '분석 가로 크기는 0보다 큰 유한한 숫자여야 합니다.')
    return width * DEFAULT_MIN_MOVE_RATIO


def valid_min_move(value):
    """Validate a user-entered pixel value without changing its units."""
    return _positive_number(value, '최소 이동량은 0보다 큰 유한한 숫자여야 합니다.')
