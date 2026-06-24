"""Alarm sound generation and playback.

Presets are synthesized into WAV files with the stdlib `wave` module on first
run (no audio assets to ship). Playback uses `winsound` so we depend on nothing
outside the standard library: a looping async PlaySound for the alarm, which we
can stop instantly with SND_PURGE.
"""

from __future__ import annotations

import math
import os
import struct
import wave
import winsound

SAMPLE_RATE = 44100
AMPLITUDE = 22000  # out of 32767

# Preset name -> generator description. Generators return a list of float
# samples in [-1, 1].
PRESET_NAMES = ["Beep", "Siren", "Pulse", "Chime"]


def _silence(duration: float) -> list[float]:
    return [0.0] * int(SAMPLE_RATE * duration)


def _tone(freq: float, duration: float, *, fade: float = 0.005) -> list[float]:
    n = int(SAMPLE_RATE * duration)
    fade_n = max(1, int(SAMPLE_RATE * fade))
    out = []
    for i in range(n):
        s = math.sin(2 * math.pi * freq * (i / SAMPLE_RATE))
        # Short linear fade in/out to avoid clicks.
        if i < fade_n:
            s *= i / fade_n
        elif i > n - fade_n:
            s *= (n - i) / fade_n
        out.append(s)
    return out


def _sweep(f0: float, f1: float, duration: float) -> list[float]:
    n = int(SAMPLE_RATE * duration)
    out = []
    phase = 0.0
    for i in range(n):
        t = i / n
        freq = f0 + (f1 - f0) * t
        phase += 2 * math.pi * freq / SAMPLE_RATE
        out.append(math.sin(phase))
    return out


def _gen_beep() -> list[float]:
    return _tone(880, 0.18) + _silence(0.12)


def _gen_siren() -> list[float]:
    return _sweep(600, 1200, 0.5) + _sweep(1200, 600, 0.5)


def _gen_pulse() -> list[float]:
    return (_tone(1000, 0.08) + _silence(0.06)) * 3 + _silence(0.25)


def _gen_chime() -> list[float]:
    out = []
    for f in (660, 880, 1320):
        out += _tone(f, 0.16)
    out += _silence(0.3)
    return out


_GENERATORS = {
    "Beep": _gen_beep,
    "Siren": _gen_siren,
    "Pulse": _gen_pulse,
    "Chime": _gen_chime,
}


def _write_wav(path: str, samples: list[float]) -> None:
    with wave.open(path, "w") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        frames = bytearray()
        for s in samples:
            v = int(max(-1.0, min(1.0, s)) * AMPLITUDE)
            frames += struct.pack("<h", v)
        w.writeframes(bytes(frames))


def ensure_presets(presets_dir: str) -> dict[str, str]:
    """Generate any missing preset WAVs. Returns {name: path}."""
    os.makedirs(presets_dir, exist_ok=True)
    mapping: dict[str, str] = {}
    for name, gen in _GENERATORS.items():
        path = os.path.join(presets_dir, f"{name.lower()}.wav")
        if not os.path.isfile(path):
            try:
                _write_wav(path, gen())
            except Exception:
                continue
        mapping[name] = path
    return mapping


class AlarmPlayer:
    """Loops a WAV file asynchronously; stop() silences it immediately."""

    def __init__(self) -> None:
        self._playing = False

    @property
    def is_playing(self) -> bool:
        return self._playing

    def play_loop(self, wav_path: str) -> None:
        if not wav_path or not os.path.isfile(wav_path):
            raise FileNotFoundError(wav_path)
        winsound.PlaySound(
            wav_path,
            winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_LOOP,
        )
        self._playing = True

    def play_once(self, wav_path: str) -> None:
        if not wav_path or not os.path.isfile(wav_path):
            raise FileNotFoundError(wav_path)
        winsound.PlaySound(
            wav_path, winsound.SND_FILENAME | winsound.SND_ASYNC
        )

    def stop(self) -> None:
        winsound.PlaySound(None, winsound.SND_PURGE)
        self._playing = False
