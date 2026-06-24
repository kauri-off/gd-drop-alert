"""GD Drop Alert - main application.

For watching a Geometry Dash stream on a second monitor: reads the player's
percent in a user-selected screen region and plays a looping alert when it
rises above a threshold (i.e. the player reaches the drop). An optional second
region is OCR'd for a keyword (e.g. "Testmode"); while present, the alert is
suppressed so practice/test runs don't trigger it.

Run with:  uv run python alarm_app.py
"""

from __future__ import annotations

import json
import os
import queue
import threading
import tkinter as tk
import webbrowser
from tkinter import filedialog, messagebox, ttk

import capture
import ocr
import region_select
import sound
from logger import EventLogger
from monitor import Monitor, Settings, Status

APP_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(APP_DIR, "config.json")
PRESETS_DIR = os.path.join(APP_DIR, "presets")
LOGS_DIR = os.path.join(APP_DIR, "logs")

TESSERACT_URL = "https://github.com/UB-Mannheim/tesseract/wiki"

DEFAULTS = {
    "number_region": None,
    "testmode_region": None,
    "testmode_enabled": True,
    "keyword": "Testmode",
    "threshold": 50.0,
    "hysteresis_margin": 0.5,
    "confirmations": 3,
    "conf_threshold": 60.0,
    "poll_interval_ms": 500,
    "scale": 4,
    "bw_threshold": 140,
    "sound_choice": "Beep",      # preset name or "Custom"
    "custom_wav": "",
    "logging_enabled": True,
    "mute_toggle_enabled": False,
    "mute_toggle_point": None,   # (x, y) physical pixels of the stream's mute toggle button
}


def load_config() -> dict:
    cfg = dict(DEFAULTS)
    if os.path.isfile(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            cfg.update({k: v for k, v in data.items() if k in DEFAULTS})
        except Exception:
            pass
    # Regions persist as lists in JSON; normalize to tuples.
    for key in ("number_region", "testmode_region", "mute_toggle_point"):
        if isinstance(cfg[key], list):
            cfg[key] = tuple(cfg[key])
    return cfg


def save_config(cfg: dict) -> None:
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
    except Exception:
        pass


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.cfg = load_config()
        self.preset_paths = sound.ensure_presets(PRESETS_DIR)
        self.player = sound.AlarmPlayer()
        self.status_queue: "queue.Queue[Status]" = queue.Queue(maxsize=8)
        self.logger = EventLogger(LOGS_DIR, enabled=self.cfg["logging_enabled"])
        self.monitor = Monitor(self._snapshot_settings, self.player,
                               self.status_queue, logger=self.logger)

        root.title("GD Drop Alert")
        root.resizable(False, False)
        root.protocol("WM_DELETE_WINDOW", self._on_close)

        self._build_ui()
        self._refresh_region_labels()
        self._refresh_mute_label()
        self._check_tesseract()
        # Lock the window to its initial size so status updates never reflow it.
        root.update_idletasks()
        root.geometry(f"{root.winfo_reqwidth()}x{root.winfo_reqheight()}")
        self.root.after(120, self._drain_queue)

    # --- UI construction ---------------------------------------------------
    def _build_ui(self) -> None:
        pad = {"padx": 8, "pady": 4}
        main = ttk.Frame(self.root, padding=12)
        main.grid(row=0, column=0, sticky="nsew")

        # Regions ----------------------------------------------------------
        reg = ttk.LabelFrame(main, text="Regions", padding=8)
        reg.grid(row=0, column=0, columnspan=2, sticky="ew", **pad)

        ttk.Button(reg, text="Select Percent Region",
                   command=self._select_number).grid(row=0, column=0, sticky="w", padx=4, pady=4)
        self.num_label = ttk.Label(reg, text="(not set)", foreground="#a00")
        self.num_label.grid(row=0, column=1, sticky="w", padx=8)

        ttk.Button(reg, text="Select Testmode Region",
                   command=self._select_testmode).grid(row=1, column=0, sticky="w", padx=4, pady=4)
        self.test_label = ttk.Label(reg, text="(not set)", foreground="#888")
        self.test_label.grid(row=1, column=1, sticky="w", padx=8)

        self.testmode_var = tk.BooleanVar(value=self.cfg["testmode_enabled"])
        ttk.Checkbutton(reg, text="Enable Testmode suppression",
                        variable=self.testmode_var,
                        command=self._persist).grid(row=2, column=0, sticky="w", padx=4)
        ttk.Label(reg, text="Keyword:").grid(row=2, column=1, sticky="e")
        self.keyword_var = tk.StringVar(value=self.cfg["keyword"])
        e = ttk.Entry(reg, textvariable=self.keyword_var, width=14)
        e.grid(row=2, column=2, sticky="w", padx=4)
        e.bind("<FocusOut>", lambda _e: self._persist())

        # Trigger ----------------------------------------------------------
        trig = ttk.LabelFrame(main, text="Trigger", padding=8)
        trig.grid(row=1, column=0, sticky="nsew", **pad)

        self.threshold_var = tk.StringVar(value=str(self.cfg["threshold"]))
        self.margin_var = tk.StringVar(value=str(self.cfg["hysteresis_margin"]))
        self.confirm_var = tk.StringVar(value=str(self.cfg["confirmations"]))
        self.conf_var = tk.StringVar(value=str(self.cfg["conf_threshold"]))
        self.interval_var = tk.StringVar(value=str(self.cfg["poll_interval_ms"]))

        self._labeled_entry(trig, "Alert when percent >", self.threshold_var, 0)
        self._labeled_entry(trig, "Hysteresis margin", self.margin_var, 1)
        self._labeled_entry(trig, "Confirmations (frames)", self.confirm_var, 2)
        self._labeled_entry(trig, "Min OCR confidence", self.conf_var, 3)
        self._labeled_entry(trig, "Poll interval (ms)", self.interval_var, 4)

        # Sound ------------------------------------------------------------
        snd = ttk.LabelFrame(main, text="Alert Sound", padding=8)
        snd.grid(row=1, column=1, sticky="nsew", **pad)

        self.sound_var = tk.StringVar(value=self.cfg["sound_choice"])
        choices = sound.PRESET_NAMES + ["Custom"]
        ttk.Label(snd, text="Sound:").grid(row=0, column=0, sticky="w")
        self.sound_combo = ttk.Combobox(snd, values=choices, textvariable=self.sound_var,
                                         state="readonly", width=12)
        self.sound_combo.grid(row=0, column=1, sticky="w", padx=4, pady=4)
        self.sound_combo.bind("<<ComboboxSelected>>", lambda _e: self._on_sound_change())

        ttk.Button(snd, text="Browse custom WAV…",
                   command=self._browse_custom).grid(row=1, column=0, columnspan=2, sticky="w", pady=2)
        self.custom_label = ttk.Label(snd, text=self._custom_label_text(), width=28)
        self.custom_label.grid(row=2, column=0, columnspan=2, sticky="w")
        ttk.Button(snd, text="Test sound", command=self._test_sound).grid(
            row=3, column=0, sticky="w", pady=6)
        ttk.Button(snd, text="Stop", command=self._stop_test).grid(
            row=3, column=1, sticky="w", pady=6)

        # Mute toggle ------------------------------------------------------
        # Clicks an on-screen mute button (e.g. a stream's) with the mouse.
        mute = ttk.LabelFrame(main, text="Stream Mute Toggle", padding=8)
        mute.grid(row=2, column=0, columnspan=2, sticky="ew", **pad)

        ttk.Button(mute, text="Set Button Position",
                   command=self._select_mute_point).grid(row=0, column=0, sticky="w", padx=4, pady=4)
        self.mute_label = ttk.Label(mute, text="(not set)", foreground="#888", width=22)
        self.mute_label.grid(row=0, column=1, sticky="w", padx=8)

        self.mute_toggle_var = tk.BooleanVar(value=self.cfg["mute_toggle_enabled"])
        ttk.Checkbutton(mute, text="Click it automatically when the alarm fires",
                        variable=self.mute_toggle_var,
                        command=self._persist).grid(row=0, column=2, sticky="w", padx=8)
        ttk.Button(mute, text="Toggle mute now",
                   command=self._toggle_mute_now).grid(row=0, column=3, sticky="e", padx=4)

        # Controls ---------------------------------------------------------
        ctrl = ttk.Frame(main, padding=(0, 6))
        ctrl.grid(row=3, column=0, columnspan=2, sticky="ew")
        self.start_btn = ttk.Button(ctrl, text="Start Monitoring", command=self._toggle_monitor)
        self.start_btn.grid(row=0, column=0, padx=4)
        ttk.Button(ctrl, text="Stop Alert", command=self._stop_alarm).grid(row=0, column=1, padx=4)
        self.logging_var = tk.BooleanVar(value=self.cfg["logging_enabled"])
        ttk.Checkbutton(ctrl, text="Log readings", variable=self.logging_var,
                        command=self._on_logging_toggle).grid(row=0, column=2, padx=12)
        ttk.Button(ctrl, text="Open logs", command=self._open_logs).grid(row=0, column=3, padx=4)

        # Status -----------------------------------------------------------
        # Fixed widths + a wraplength on the detail line keep the footprint
        # constant so the (non-resizable) window doesn't jump horizontally as
        # the OCR text/counters change length each cycle.
        st = ttk.LabelFrame(main, text="Status", padding=8)
        st.grid(row=4, column=0, columnspan=2, sticky="ew", **pad)
        st.columnconfigure(0, weight=1)
        self.status_value = ttk.Label(st, text="Percent: —", font=("Segoe UI", 14, "bold"),
                                      anchor="w", width=24)
        self.status_value.grid(row=0, column=0, sticky="w")
        self.status_detail = ttk.Label(st, text="Idle.", foreground="#555",
                                       anchor="w", justify="left", width=78)
        self.status_detail.grid(row=1, column=0, sticky="w", pady=2)
        self.status_alarm = ttk.Label(st, text="", font=("Segoe UI", 12, "bold"),
                                      anchor="w", width=30)
        self.status_alarm.grid(row=2, column=0, sticky="w")

        # OCR engine -------------------------------------------------------
        ocr_fr = ttk.LabelFrame(main, text="OCR Engine (Tesseract)", padding=8)
        ocr_fr.grid(row=5, column=0, columnspan=2, sticky="ew", **pad)
        ocr_fr.columnconfigure(0, weight=1)
        self.tess_label = ttk.Label(ocr_fr, text="Checking…", anchor="w", width=46)
        self.tess_label.grid(row=0, column=0, sticky="w", padx=4)
        self.tess_install_btn = ttk.Button(ocr_fr, text="Install Tesseract",
                                            command=self._install_tesseract)
        self.tess_install_btn.grid(row=0, column=1, sticky="e", padx=4)
        ttk.Button(ocr_fr, text="Re-check",
                   command=self._check_tesseract).grid(row=0, column=2, sticky="e", padx=4)

    def _labeled_entry(self, parent, text, var, row):
        ttk.Label(parent, text=text).grid(row=row, column=0, sticky="w", pady=2)
        e = ttk.Entry(parent, textvariable=var, width=10)
        e.grid(row=row, column=1, sticky="w", padx=6)
        e.bind("<FocusOut>", lambda _e: self._persist())
        return e

    # --- helpers -----------------------------------------------------------
    def _custom_label_text(self) -> str:
        p = self.cfg.get("custom_wav", "")
        return os.path.basename(p) if p else "(no custom file)"

    def _current_wav(self) -> str:
        choice = self.sound_var.get()
        if choice == "Custom":
            return self.cfg.get("custom_wav", "")
        return self.preset_paths.get(choice, "")

    def _float(self, var, fallback):
        try:
            return float(var.get())
        except (ValueError, AttributeError):
            return fallback

    def _int(self, var, fallback):
        try:
            return int(float(var.get()))
        except (ValueError, AttributeError):
            return fallback

    def _snapshot_settings(self) -> Settings:
        return Settings(
            number_region=self.cfg["number_region"],
            testmode_region=self.cfg["testmode_region"],
            testmode_enabled=self.testmode_var.get(),
            keyword=self.keyword_var.get(),
            threshold=self._float(self.threshold_var, 0.0),
            hysteresis_margin=self._float(self.margin_var, 0.5),
            confirmations=max(1, self._int(self.confirm_var, 3)),
            conf_threshold=self._float(self.conf_var, 60.0),
            poll_interval_ms=max(100, self._int(self.interval_var, 500)),
            scale=self.cfg["scale"],
            bw_threshold=self.cfg["bw_threshold"],
            alarm_wav=self._current_wav(),
            mute_toggle_enabled=self.mute_toggle_var.get(),
            mute_toggle_point=self.cfg["mute_toggle_point"],
        )

    def _persist(self) -> None:
        self.cfg.update({
            "testmode_enabled": self.testmode_var.get(),
            "keyword": self.keyword_var.get(),
            "threshold": self._float(self.threshold_var, DEFAULTS["threshold"]),
            "hysteresis_margin": self._float(self.margin_var, DEFAULTS["hysteresis_margin"]),
            "confirmations": max(1, self._int(self.confirm_var, DEFAULTS["confirmations"])),
            "conf_threshold": self._float(self.conf_var, DEFAULTS["conf_threshold"]),
            "poll_interval_ms": max(100, self._int(self.interval_var, DEFAULTS["poll_interval_ms"])),
            "sound_choice": self.sound_var.get(),
            "mute_toggle_enabled": self.mute_toggle_var.get(),
        })
        save_config(self.cfg)

    def _refresh_region_labels(self) -> None:
        nr = self.cfg["number_region"]
        if nr:
            self.num_label.config(text=f"{nr[2]}×{nr[3]} @ ({nr[0]},{nr[1]})", foreground="#070")
        else:
            self.num_label.config(text="(not set)", foreground="#a00")
        tr = self.cfg["testmode_region"]
        if tr:
            self.test_label.config(text=f"{tr[2]}×{tr[3]} @ ({tr[0]},{tr[1]})", foreground="#070")
        else:
            self.test_label.config(text="(not set)", foreground="#888")

    def _refresh_mute_label(self) -> None:
        mp = self.cfg["mute_toggle_point"]
        if mp:
            self.mute_label.config(text=f"({mp[0]}, {mp[1]})", foreground="#070")
        else:
            self.mute_label.config(text="(not set)", foreground="#888")

    # --- actions -----------------------------------------------------------
    def _select_number(self) -> None:
        self.root.withdraw()
        self.root.after(200, lambda: self._do_select("number_region",
                                                     "Drag over the PERCENT to watch"))

    def _select_testmode(self) -> None:
        self.root.withdraw()
        self.root.after(200, lambda: self._do_select("testmode_region",
                                                     "Drag over the TESTMODE indicator"))

    def _do_select(self, key: str, prompt: str) -> None:
        try:
            region = region_select.select_region(self.root, prompt)
        finally:
            self.root.deiconify()
            self.root.lift()
        if region:
            self.cfg[key] = region
            save_config(self.cfg)
            self._refresh_region_labels()

    def _select_mute_point(self) -> None:
        self.root.withdraw()
        self.root.after(200, self._do_select_mute_point)

    def _do_select_mute_point(self) -> None:
        try:
            point = region_select.select_point(
                self.root, "Click the stream's MUTE button to remember its position")
        finally:
            self.root.deiconify()
            self.root.lift()
        if point:
            self.cfg["mute_toggle_point"] = point
            save_config(self.cfg)
            self._refresh_mute_label()

    def _toggle_mute_now(self) -> None:
        point = self.cfg["mute_toggle_point"]
        if not point:
            messagebox.showwarning("No position", "Set the mute button position first.")
            return
        # Give the user a moment to bring the stream to the foreground, then click.
        self.status_detail.config(text="Toggling mute…")
        self.root.after(600, lambda: self.monitor.click_mute_toggle(point))

    def _on_sound_change(self) -> None:
        self._persist()

    def _browse_custom(self) -> None:
        path = filedialog.askopenfilename(
            title="Choose a WAV file",
            filetypes=[("WAV audio", "*.wav"), ("All files", "*.*")],
        )
        if path:
            self.cfg["custom_wav"] = path
            self.sound_var.set("Custom")
            self.custom_label.config(text=self._custom_label_text())
            self._persist()

    def _test_sound(self) -> None:
        wav = self._current_wav()
        if not wav or not os.path.isfile(wav):
            messagebox.showwarning("No sound", "Selected sound file was not found.")
            return
        try:
            self.player.play_once(wav)
        except Exception as exc:
            messagebox.showerror("Playback error", str(exc))

    def _stop_test(self) -> None:
        self.player.stop()

    def _toggle_monitor(self) -> None:
        if self.monitor.running:
            self.monitor.stop()
            self.start_btn.config(text="Start Monitoring")
            self.status_detail.config(text="Stopped.")
            return
        if not self.cfg["number_region"]:
            messagebox.showwarning("No region", "Select the percent region first.")
            return
        if not ocr.tesseract_available():
            self._tesseract_warning()
            return
        self._persist()
        self.monitor.start()
        self.start_btn.config(text="Stop Monitoring")
        self.status_detail.config(text="Monitoring…")

    def _stop_alarm(self) -> None:
        self.monitor.silence_alarm()
        self.player.stop()
        self.status_alarm.config(text="Alert silenced.", foreground="#555")

    def _on_logging_toggle(self) -> None:
        self.logger.enabled = self.logging_var.get()
        self.cfg["logging_enabled"] = self.logging_var.get()
        save_config(self.cfg)
        # If turning on mid-run, open a session now so rows start flowing.
        if self.logger.enabled and self.monitor.running:
            self.logger.open_session()

    def _open_logs(self) -> None:
        os.makedirs(LOGS_DIR, exist_ok=True)
        try:
            os.startfile(LOGS_DIR)  # noqa: S606 - Windows shell open
        except Exception as exc:
            messagebox.showinfo("Logs", f"Logs folder:\n{LOGS_DIR}\n\n({exc})")

    # --- status pump -------------------------------------------------------
    def _drain_queue(self) -> None:
        last: Status | None = None
        try:
            while True:
                last = self.status_queue.get_nowait()
        except queue.Empty:
            pass
        if last is not None:
            self._render_status(last)
        self.root.after(120, self._drain_queue)

    @staticmethod
    def _clip(text: str, limit: int = 76) -> str:
        return text if len(text) <= limit else text[: limit - 1] + "…"

    def _render_status(self, s: Status) -> None:
        if s.error:
            self.status_value.config(text="Percent: —")
            self.status_detail.config(text=self._clip(s.error), foreground="#a00")
        else:
            vtxt = f"{s.value:g}" if s.value is not None else "unreadable"
            self.status_value.config(text=f"Percent: {vtxt}")
            conf = f"{s.confidence:.0f}" if s.confidence >= 0 else "—"
            quality = "valid" if s.valid else "ignored (low conf)"
            tm = "  • TESTMODE" if s.testmode else ""
            extra = s.extra or {}
            raw = self._clip(s.raw, 16)
            self.status_detail.config(
                text=self._clip(
                    f"raw='{raw}'  conf={conf}  [{quality}]  "
                    f"above={extra.get('above', 0)} below={extra.get('below', 0)}{tm}"),
                foreground="#555",
            )
        if s.alarm:
            self.status_alarm.config(text="🔔 ON DROP — ALERT ACTIVE", foreground="#c00")
        elif s.testmode:
            self.status_alarm.config(text="Suppressed by Testmode", foreground="#06c")
        else:
            self.status_alarm.config(text="", foreground="#555")

    # --- tesseract ---------------------------------------------------------
    def _check_tesseract(self) -> None:
        version = ocr.tesseract_version()
        if version:
            self.tess_label.config(text=f"Installed ✓  (v{version})", foreground="#070")
            self.tess_install_btn.config(state="disabled")
        else:
            self.tess_label.config(
                text="Not found — required for monitoring.", foreground="#a00")
            self.tess_install_btn.config(state="normal")

    def _install_tesseract(self) -> None:
        methods = ocr.available_installers()
        if not methods:
            if messagebox.askyesno(
                "No package manager",
                "No supported package manager (winget, Chocolatey, or Scoop) "
                "was found.\n\n"
                "Open the Tesseract download page in your browser instead?",
            ):
                webbrowser.open(TESSERACT_URL)
            return
        if not messagebox.askyesno(
            "Install Tesseract",
            f"Install Tesseract using {methods[0]}?\n\n"
            "A Windows User Account Control prompt may appear, and this can "
            "take a few minutes.",
        ):
            return
        self.tess_install_btn.config(state="disabled")
        self.tess_label.config(text=f"Installing via {methods[0]}…", foreground="#a60")
        threading.Thread(target=self._install_worker, daemon=True).start()

    def _install_worker(self) -> None:
        ok, method, detail = ocr.install_tesseract()
        # Hop back onto the Tk thread for any UI work.
        self.root.after(0, lambda: self._install_done(ok, method, detail))

    def _install_done(self, ok: bool, method: str, detail: str) -> None:
        self._check_tesseract()
        if ok:
            messagebox.showinfo("Tesseract", detail)
            return
        if messagebox.askyesno(
            "Install failed",
            f"Automatic install did not complete.\n\n{detail}\n\n"
            "Open the Tesseract download page to install it manually?",
        ):
            webbrowser.open(TESSERACT_URL)

    def _tesseract_warning(self) -> None:
        messagebox.showerror(
            "Tesseract not found",
            "This app needs the Tesseract OCR engine.\n\n"
            "Use the “Install Tesseract” button in the OCR Engine "
            "section, then press “Re-check”.\n\n"
            f"Manual download: {TESSERACT_URL}\n\n"
            "If installed in a custom location, set the TESSERACT_CMD "
            "environment variable to the full path of tesseract.exe.",
        )

    # --- lifecycle ---------------------------------------------------------
    def _on_close(self) -> None:
        try:
            self.monitor.stop()
            self.player.stop()
            self._persist()
        finally:
            self.root.destroy()


def main() -> None:
    capture.enable_dpi_awareness()
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
