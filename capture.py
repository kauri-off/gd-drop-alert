"""Screen capture and Windows DPI handling.

All coordinates used throughout the app are *physical* pixels. We make the
process per-monitor DPI aware so that tkinter coordinates and mss captures
agree, which prevents the capture rectangle from drifting on scaled displays
(a common cause of OCR misreads).
"""

from __future__ import annotations

import ctypes
import threading

from PIL import Image

try:
    import mss
except Exception as exc:  # pragma: no cover - import guard
    mss = None
    _MSS_IMPORT_ERROR = exc
else:
    _MSS_IMPORT_ERROR = None


def enable_dpi_awareness() -> None:
    """Make the current process per-monitor DPI aware.

    Must be called once, as early as possible, before any Tk window exists.
    Falls back gracefully on older Windows where the API is unavailable.
    """
    try:
        # PROCESS_PER_MONITOR_DPI_AWARE = 2
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return
    except Exception:
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


# mss instances are not thread-safe; give each thread its own.
_local = threading.local()


def _get_sct():
    if mss is None:
        raise RuntimeError(f"mss failed to import: {_MSS_IMPORT_ERROR}")
    sct = getattr(_local, "sct", None)
    if sct is None:
        sct = mss.mss()
        _local.sct = sct
    return sct


def virtual_screen_bounds() -> tuple[int, int, int, int]:
    """Return (left, top, width, height) of the full virtual desktop."""
    sct = _get_sct()
    m = sct.monitors[0]  # index 0 is the union of all monitors
    return m["left"], m["top"], m["width"], m["height"]


def grab(region: tuple[int, int, int, int]) -> Image.Image:
    """Capture a physical-pixel region (left, top, width, height) as an RGB image."""
    left, top, width, height = region
    if width <= 0 or height <= 0:
        raise ValueError(f"invalid region size: {width}x{height}")
    monitor = {"left": int(left), "top": int(top),
               "width": int(width), "height": int(height)}
    sct = _get_sct()
    shot = sct.grab(monitor)
    return Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
