"""Alarm sound generation and playback.

Presets are synthesized into WAV files with the stdlib `wave` module on first
run (no audio assets to ship). Playback uses the pygame-ce mixer, which opens
the audio device once and keeps it warm: a single reserved channel plays a
`Sound`, loops it gaplessly (``loops=-1``), and stops instantly via
``Channel.stop()`` from any thread. This replaces the old `winsound` backend,
whose per-call device open/close clipped onsets and whose process-global purge
made stopping racy.
"""

from __future__ import annotations

import math
import os
import struct
import threading
import wave

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
import pygame

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


# Mixer init is process-wide and lazy: importing this module (e.g. for
# ensure_presets in tests or on a headless box) must not require an audio
# device. _ensure_mixer() is idempotent and only runs when a player is created.
_mixer_lock = threading.Lock()
_mixer_ready = False


def _ensure_mixer() -> bool:
    """Initialise the pygame mixer once. Returns True if audio is available."""
    global _mixer_ready
    with _mixer_lock:
        if _mixer_ready:
            return True
        try:
            # Stereo, 16-bit, small buffer: mono presets and stereo custom WAVs
            # both play, and the short buffer keeps latency low.
            pygame.mixer.pre_init(SAMPLE_RATE, -16, 2, 512)
            pygame.mixer.init()
            _mixer_ready = True
        except Exception:
            _mixer_ready = False
        return _mixer_ready


class AlarmPlayer:
    """Plays a WAV through a reserved pygame mixer channel.

    The mixer holds the audio device open for the player's lifetime, so onsets
    aren't clipped by a cold start. Playback uses one dedicated channel:
    ``play_once`` plays a single pass, ``play_loop`` loops gaplessly, and
    ``stop`` silences instantly. Channel operations are safe to call from any
    thread (the GUI thread and the monitor worker thread both do). If no audio
    device is available the player degrades to a no-op rather than raising.
    """

    def __init__(self) -> None:
        self._cache: dict[str, "pygame.mixer.Sound"] = {}
        self._lock = threading.Lock()
        self._channel = None
        if _ensure_mixer():
            self._channel = pygame.mixer.Channel(0)

    @property
    def is_playing(self) -> bool:
        ch = self._channel
        return bool(ch and ch.get_busy())

    def _sound(self, wav_path: str) -> "pygame.mixer.Sound":
        if not wav_path or not os.path.isfile(wav_path):
            raise FileNotFoundError(wav_path)
        snd = self._cache.get(wav_path)
        if snd is None:
            snd = pygame.mixer.Sound(wav_path)
            self._cache[wav_path] = snd
        return snd

    def _play(self, wav_path: str, *, loops: int) -> None:
        if self._channel is None:
            return
        snd = self._sound(wav_path)
        with self._lock:
            self._channel.play(snd, loops=loops)

    def play_loop(self, wav_path: str) -> None:
        self._play(wav_path, loops=-1)

    def play_once(self, wav_path: str) -> None:
        self._play(wav_path, loops=0)

    def stop(self) -> None:
        ch = self._channel
        if ch is None:
            return
        with self._lock:
            ch.stop()
