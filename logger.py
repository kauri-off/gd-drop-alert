"""Logging for after-the-fact false-positive analysis.

Two streams, both under logs/:
  * readings-<session>.csv  - one row per polling cycle (the full OCR trail).
  * events-<session>.log    - alarm fired/cleared/silenced + monitor start/stop.

Reviewing the CSV around an alarm timestamp shows exactly what the OCR read on
each frame, its confidence, and the confirmation counters - which is what you
need to tell a real trigger from a misread.
"""

from __future__ import annotations

import csv
import datetime as _dt
import os
import threading

READING_FIELDS = [
    "time", "value", "confidence", "valid", "testmode",
    "above", "below", "silenced", "alarm", "raw", "error",
]


class EventLogger:
    def __init__(self, logs_dir: str, enabled: bool = True):
        self.logs_dir = logs_dir
        self.enabled = enabled
        self._lock = threading.Lock()
        self._csv_file = None
        self._csv_writer = None
        self._events_path = None
        self._session = None

    def _now(self) -> str:
        return _dt.datetime.now().isoformat(timespec="milliseconds")

    def open_session(self) -> None:
        """Start a fresh pair of log files for a monitoring run."""
        if not self.enabled:
            return
        with self._lock:
            self.close()
            os.makedirs(self.logs_dir, exist_ok=True)
            self._session = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
            csv_path = os.path.join(self.logs_dir, f"readings-{self._session}.csv")
            self._events_path = os.path.join(self.logs_dir, f"events-{self._session}.log")
            try:
                self._csv_file = open(csv_path, "w", newline="", encoding="utf-8")
                self._csv_writer = csv.DictWriter(self._csv_file, fieldnames=READING_FIELDS)
                self._csv_writer.writeheader()
                self._csv_file.flush()
            except Exception:
                self._csv_file = None
                self._csv_writer = None
            self._write_event("monitor started")

    def log_reading(self, status) -> None:
        if not self.enabled:
            return
        with self._lock:
            if self._csv_writer is None:
                return
            extra = getattr(status, "extra", {}) or {}
            row = {
                "time": self._now(),
                "value": "" if status.value is None else f"{status.value:g}",
                "confidence": f"{status.confidence:.1f}",
                "valid": int(bool(status.valid)),
                "testmode": int(bool(status.testmode)),
                "above": extra.get("above", ""),
                "below": extra.get("below", ""),
                "silenced": int(bool(extra.get("silenced", False))),
                "alarm": int(bool(status.alarm)),
                "raw": status.raw,
                "error": status.error,
            }
            try:
                self._csv_writer.writerow(row)
                self._csv_file.flush()
            except Exception:
                pass

    def log_event(self, message: str) -> None:
        if not self.enabled:
            return
        with self._lock:
            self._write_event(message)

    def _write_event(self, message: str) -> None:
        if not self._events_path:
            return
        try:
            with open(self._events_path, "a", encoding="utf-8") as f:
                f.write(f"{self._now()}  {message}\n")
        except Exception:
            pass

    def close(self) -> None:
        if self._csv_file is not None:
            try:
                self._write_event("monitor stopped")
                self._csv_file.flush()
                self._csv_file.close()
            except Exception:
                pass
        self._csv_file = None
        self._csv_writer = None
