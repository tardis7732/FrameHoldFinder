"""Render a single-video repair plan for playback on the original timeline."""
import threading

from preview_file import render_preview_file
import sdk_bridge


def render_motion_preview(analysis, runs, *, quality='HIGH', progress=None, cancel=None):
    cancel = cancel or threading.Event()

    def render_to(path):
        sdk_bridge.repair(analysis, runs, path, quality, progress or (lambda frame, total: None), cancel)

    return render_preview_file(analysis['path'], render_to, cancel)
