"""Synthetic mouse clicks at a physical-pixel screen point (Windows).

Used to toggle a stream's mute state by clicking its on-screen mute button. Because
the process is per-monitor DPI aware (see capture.enable_dpi_awareness), the
coordinates handled throughout the app are physical pixels, which is exactly
what SetCursorPos expects — so a stored point lines up with what the user saw.
"""

from __future__ import annotations

import ctypes
import time

MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004

# How long to wait after the click before restoring focus. mouse_event only
# queues the click; the window under the cursor activates once its own thread
# processes that input, which happens after click() would otherwise return. We
# let that settle first so the restore doesn't race ahead and get clobbered.
_ACTIVATION_SETTLE_S = 0.05


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


def _get_cursor_pos() -> tuple[int, int] | None:
    pt = _POINT()
    if ctypes.windll.user32.GetCursorPos(ctypes.byref(pt)):
        return pt.x, pt.y
    return None


def _restore_foreground(hwnd: int) -> None:
    """Bring ``hwnd`` back to the foreground after a click stole focus.

    Windows only lets a process call SetForegroundWindow when it owns the
    foreground or received the most recent input; otherwise the call is ignored
    and the taskbar button just flashes. We borrow that right by attaching our
    thread's input state to the thread that currently owns the foreground (the
    window the click activated), which lets the SetForegroundWindow call through.
    """
    user32 = ctypes.windll.user32
    if not hwnd:
        return
    current = user32.GetForegroundWindow()
    if current == hwnd:
        return  # the click didn't move focus after all

    our_thread = ctypes.windll.kernel32.GetCurrentThreadId()
    fg_thread = user32.GetWindowThreadProcessId(current, None)
    attached = bool(fg_thread) and fg_thread != our_thread \
        and bool(user32.AttachThreadInput(our_thread, fg_thread, True))
    try:
        user32.SetForegroundWindow(hwnd)
    finally:
        if attached:
            user32.AttachThreadInput(our_thread, fg_thread, False)


def click(x: int, y: int) -> None:
    """Move the cursor to (x, y) physical pixels, left-click, then restore it.

    The cursor is returned to wherever it was before the click, and the window
    that had focus before the click is brought back to the foreground, so
    neither the user's pointer nor their active window jumps. Raises on failure
    so callers can report/log it.
    """
    user32 = ctypes.windll.user32
    origin = _get_cursor_pos()
    foreground = user32.GetForegroundWindow()
    if not user32.SetCursorPos(int(x), int(y)):
        raise OSError("SetCursorPos failed")
    user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
    user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
    if origin is not None:
        user32.SetCursorPos(origin[0], origin[1])
    # Let the click's window activation land before we undo it.
    time.sleep(_ACTIVATION_SETTLE_S)
    _restore_foreground(foreground)
