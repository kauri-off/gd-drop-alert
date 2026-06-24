"""Background monitoring loop and alarm state machine.

The Monitor runs in its own thread. Each cycle it captures the number region,
OCRs it, applies a stack of guards designed to eliminate false positives, and
drives an AlarmPlayer. Human-readable status is pushed to a queue the GUI drains
on the Tk main thread.

Guards (in order):
  1. Confidence gate     - OCR mean confidence must clear a floor.
  2. Valid-parse gate    - the text must parse to a real float.
  3. Confirmation count  - value must exceed threshold for N consecutive valid
                           reads before the alarm fires.
  4. Hysteresis          - once firing, only clears after the value stays below
                           (threshold - margin) for N consecutive reads.
  5. Testmode suppression- keyword in region 2 suppresses + resets everything.
  6. Manual silence      - 'Stop alarm' silences until the value drops below the
                           threshold band again, so it can't immediately retrigger.
"""

from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass, field

import capture
import clicker
import ocr


@dataclass
class Settings:
    number_region: tuple[int, int, int, int] | None = None
    testmode_region: tuple[int, int, int, int] | None = None
    testmode_enabled: bool = True
    keyword: str = "Testmode"
    threshold: float = 0.0
    hysteresis_margin: float = 0.5
    confirmations: int = 3
    conf_threshold: float = 60.0
    poll_interval_ms: int = 500
    scale: int = 4
    bw_threshold: int = 140
    alarm_wav: str = ""
    mute_toggle_enabled: bool = False
    mute_toggle_point: tuple[int, int] | None = None


@dataclass
class Status:
    value: float | None = None
    confidence: float = -1.0
    raw: str = ""
    testmode: bool = False
    alarm: bool = False
    valid: bool = False
    error: str = ""
    extra: dict = field(default_factory=dict)


class Monitor:
    def __init__(self, get_settings, player, status_queue: "queue.Queue[Status]",
                 logger=None):
        self._get_settings = get_settings
        self._player = player
        self._queue = status_queue
        self._logger = logger
        self._thread: threading.Thread | None = None
        self._stop_evt = threading.Event()

        # State machine fields (only touched by the worker thread).
        self._above = 0
        self._below = 0
        self._alarm_on = False
        self._silenced = False

    # --- lifecycle ---------------------------------------------------------
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_evt.clear()
        self._reset_state()
        if self._logger:
            self._logger.open_session()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_evt.set()
        t = self._thread
        if t and t.is_alive():
            t.join(timeout=2.0)
        self._set_alarm(False)
        if self._logger:
            self._logger.close()

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def silence_alarm(self) -> None:
        """User pressed 'Stop alarm': silence and arm re-trigger lockout."""
        self._silenced = True
        self._set_alarm(False)
        if self._logger:
            self._logger.log_event("alarm silenced by user")

    def click_mute_toggle(self, point: tuple[int, int]) -> bool:
        """Move the mouse to ``point`` and click it (a stream's mute toggle button).

        Safe to call from any thread. Returns True on success.
        """
        try:
            clicker.click(point[0], point[1])
            if self._logger:
                self._logger.log_event(f"mute toggle click at {point[0]},{point[1]}")
            return True
        except Exception as exc:
            if self._logger:
                self._logger.log_event(f"mute toggle click failed: {exc}")
            return False

    def _reset_state(self) -> None:
        self._above = 0
        self._below = 0
        self._alarm_on = False
        self._silenced = False

    # --- alarm control -----------------------------------------------------
    def _set_alarm(self, on: bool) -> None:
        if on and not self._alarm_on:
            self._alarm_on = True
            s = self._get_settings()
            try:
                if s.alarm_wav:
                    self._player.play_loop(s.alarm_wav)
            except Exception:
                pass
            if self._logger:
                self._logger.log_event(
                    f"ALARM FIRED (above={self._above}, threshold={s.threshold})")
            if s.mute_toggle_enabled and s.mute_toggle_point:
                self.click_mute_toggle(s.mute_toggle_point)
        elif not on and self._alarm_on:
            self._alarm_on = False
            try:
                self._player.stop()
            except Exception:
                pass
            if self._logger:
                self._logger.log_event(f"alarm cleared (below={self._below})")

    # --- main loop ---------------------------------------------------------
    def _run(self) -> None:
        while not self._stop_evt.is_set():
            s = self._get_settings()
            status = self._tick(s)
            if self._logger:
                self._logger.log_reading(status)
            try:
                self._queue.put_nowait(status)
            except queue.Full:
                pass
            self._stop_evt.wait(max(0.05, s.poll_interval_ms / 1000.0))

    def _tick(self, s: Settings) -> Status:
        if not s.number_region:
            return Status(error="No number region selected", alarm=self._alarm_on)

        try:
            num_img = capture.grab(s.number_region)
        except Exception as exc:
            return Status(error=f"capture failed: {exc}", alarm=self._alarm_on)

        read = ocr.read_number(num_img, scale=s.scale, threshold=s.bw_threshold)

        # Testmode detection.
        testmode = False
        if s.testmode_enabled and s.testmode_region:
            try:
                t_img = capture.grab(s.testmode_region)
                t_text = ocr.read_text(t_img, scale=max(2, s.scale - 1),
                                       threshold=s.bw_threshold)
                testmode = ocr.keyword_present(t_text, s.keyword)
            except Exception:
                testmode = False

        valid = (read.value is not None) and (read.confidence >= s.conf_threshold)

        if testmode:
            # Suppress completely and reset counters so nothing carries over.
            self._above = 0
            self._below = 0
            self._set_alarm(False)
        elif valid:
            v = read.value
            if v > s.threshold:
                self._above += 1
                self._below = 0
            elif v < (s.threshold - s.hysteresis_margin):
                self._below += 1
                self._above = 0
                self._silenced = False  # value left the danger zone -> re-arm
            else:
                # Inside hysteresis band: hold current counters steady.
                pass

            if (not self._alarm_on and not self._silenced
                    and self._above >= s.confirmations):
                self._set_alarm(True)
            elif self._alarm_on and self._below >= s.confirmations:
                self._set_alarm(False)
        # If not valid: ignore this frame entirely (no counter change).

        return Status(
            value=read.value,
            confidence=read.confidence,
            raw=read.raw,
            testmode=testmode,
            alarm=self._alarm_on,
            valid=valid,
            error="",
            extra={"above": self._above, "below": self._below,
                   "silenced": self._silenced},
        )
