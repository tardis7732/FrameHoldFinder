"""Own a temporary movie until its preview decoder has been stopped."""
from pathlib import Path
import tempfile
import time

from hold_core import Cancelled


class RenderedPreview:
    def __init__(self, suffix):
        self._folder = tempfile.TemporaryDirectory(prefix='FrameHoldFinder-preview-')
        self.path = Path(self._folder.name) / ('preview' + suffix)

    def close(self):
        # A just-stopped Windows decoder can briefly retain its file handle.
        for delay in (.03, .06, .12, .24):
            try:
                self._folder.cleanup()
                return
            except PermissionError:
                time.sleep(delay)
        self._folder.cleanup()


def render_preview_file(input_path, render_to, cancel=None):
    """Transfer ownership to the caller only after a successful, uncancelled render."""
    if cancel is not None and cancel.is_set():
        raise Cancelled()
    preview = RenderedPreview(Path(input_path).suffix.lower())
    try:
        render_to(preview.path)
        if cancel is not None and cancel.is_set():
            raise Cancelled()
    except BaseException:
        preview.close()
        raise
    return preview
