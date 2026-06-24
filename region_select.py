"""Fullscreen drag-to-select overlay returning a physical-pixel region.

Because the process is DPI aware (see capture.enable_dpi_awareness), tkinter
coordinates map 1:1 to physical pixels, so the returned rectangle lines up with
mss captures.
"""

from __future__ import annotations

import tkinter as tk

import capture


def select_region(root: tk.Tk, prompt: str = "Drag to select a region") -> tuple[int, int, int, int] | None:
    """Show a fullscreen overlay; return (left, top, width, height) or None.

    None means the user cancelled (Esc / right-click) or made a zero-size box.
    """
    vleft, vtop, vwidth, vheight = capture.virtual_screen_bounds()

    overlay = tk.Toplevel(root)
    overlay.overrideredirect(True)
    overlay.geometry(f"{vwidth}x{vheight}+{vleft}+{vtop}")
    overlay.attributes("-topmost", True)
    try:
        overlay.attributes("-alpha", 0.30)
    except tk.TclError:
        pass
    overlay.configure(cursor="crosshair", bg="black")
    overlay.lift()
    overlay.focus_force()

    canvas = tk.Canvas(overlay, bg="gray20", highlightthickness=0, cursor="crosshair")
    canvas.pack(fill="both", expand=True)
    canvas.create_text(
        vwidth // 2, 40, text=f"{prompt}   (Esc to cancel)",
        fill="white", font=("Segoe UI", 16, "bold"),
    )

    state: dict[str, object] = {"start": None, "rect": None, "result": None}

    def on_press(event):
        state["start"] = (event.x, event.y)
        if state["rect"]:
            canvas.delete(state["rect"])
        state["rect"] = canvas.create_rectangle(
            event.x, event.y, event.x, event.y,
            outline="#00ff7f", width=2,
        )

    def on_drag(event):
        if not state["start"]:
            return
        x0, y0 = state["start"]
        canvas.coords(state["rect"], x0, y0, event.x, event.y)

    def on_release(event):
        if not state["start"]:
            _cancel()
            return
        x0, y0 = state["start"]
        x1, y1 = event.x, event.y
        left, top = min(x0, x1), min(y0, y1)
        width, height = abs(x1 - x0), abs(y1 - y0)
        if width < 3 or height < 3:
            state["result"] = None
        else:
            state["result"] = (vleft + left, vtop + top, width, height)
        overlay.destroy()

    def _cancel(_event=None):
        state["result"] = None
        overlay.destroy()

    canvas.bind("<ButtonPress-1>", on_press)
    canvas.bind("<B1-Motion>", on_drag)
    canvas.bind("<ButtonRelease-1>", on_release)
    overlay.bind("<Escape>", _cancel)
    overlay.bind("<ButtonPress-3>", _cancel)

    overlay.grab_set()
    root.wait_window(overlay)
    return state["result"]  # type: ignore[return-value]


def select_point(root: tk.Tk, prompt: str = "Click the button to remember") -> tuple[int, int] | None:
    """Show a fullscreen overlay; return (x, y) physical pixels or None.

    None means the user cancelled (Esc / right-click). Used to capture the
    on-screen location of a stream's mute/unmute button.
    """
    vleft, vtop, vwidth, vheight = capture.virtual_screen_bounds()

    overlay = tk.Toplevel(root)
    overlay.overrideredirect(True)
    overlay.geometry(f"{vwidth}x{vheight}+{vleft}+{vtop}")
    overlay.attributes("-topmost", True)
    try:
        overlay.attributes("-alpha", 0.30)
    except tk.TclError:
        pass
    overlay.configure(cursor="crosshair", bg="black")
    overlay.lift()
    overlay.focus_force()

    canvas = tk.Canvas(overlay, bg="gray20", highlightthickness=0, cursor="crosshair")
    canvas.pack(fill="both", expand=True)
    canvas.create_text(
        vwidth // 2, 40, text=f"{prompt}   (Esc to cancel)",
        fill="white", font=("Segoe UI", 16, "bold"),
    )

    state: dict[str, object] = {"result": None}

    def on_click(event):
        state["result"] = (vleft + event.x, vtop + event.y)
        overlay.destroy()

    def _cancel(_event=None):
        state["result"] = None
        overlay.destroy()

    canvas.bind("<ButtonPress-1>", on_click)
    overlay.bind("<Escape>", _cancel)
    overlay.bind("<ButtonPress-3>", _cancel)

    overlay.grab_set()
    root.wait_window(overlay)
    return state["result"]  # type: ignore[return-value]
