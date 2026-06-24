# GD Drop Alert

A Windows desktop app for watching a **Geometry Dash** stream on a second
monitor: it reads the player's percent on screen and plays a looping alert when
it rises above a threshold — so you get pinged the moment a player reaches the
drop. You select the screen region holding the percent; an optional second
region is watched for a keyword (e.g. **Testmode**) that suppresses the alert
while present, so practice/test runs don't trigger it.

Built for **stability and no false positives** — a single misread can never
trigger the alert.

## Features

- **Region selection** — drag a rectangle over the percent to watch, and an
  optional second rectangle over a "Testmode" indicator. DPI-aware, so the
  capture lines up on scaled displays.
- **Reliable reads** — captures with `mss`, preprocesses with Pillow
  (grayscale, upscale, autocontrast, auto-polarity binarize), and OCRs with
  Tesseract using a digit whitelist. Parses `45.22%`, `1,234.5`, `12,5`, etc.
- **No false positives** — layered guards:
  1. OCR confidence floor.
  2. Must parse to a real number.
  3. Must exceed the threshold for **N consecutive frames** before firing.
  4. **Hysteresis** so a percent hovering at the threshold doesn't stutter.
  5. **Testmode suppression** — keyword in region 2 mutes everything.
  6. **Stop Alert** silences until the percent drops back below the band.
- **Alert sounds** — built-in presets (Beep, Siren, Pulse, Chime) generated on
  first run, plus your own custom `.wav`. Test button to preview.
- **Stream mute toggle** — record the screen position of a stream's mute button,
  and the app will physically move the mouse and click it. Optionally click it
  automatically when the alert fires (e.g. to unmute the stream so you hear it),
  or click it on demand with **Toggle mute now**.
- **Logging** — every polling cycle is written to `logs/readings-<session>.csv`
  with the raw OCR text, confidence, counters and alert state, and alert
  events to `logs/events-<session>.log`. Review these to audit any trigger.

## Requirements

- Windows 10/11
- Python 3.11+ (tested on 3.14) and [`uv`](https://docs.astral.sh/uv/)
- **Tesseract OCR** (one-time install) —
  <https://github.com/UB-Mannheim/tesseract/wiki>
  The app auto-detects it on `PATH` or at
  `C:\Program Files\Tesseract-OCR\tesseract.exe`. If installed elsewhere, set
  the `TESSERACT_CMD` environment variable to the full path of `tesseract.exe`.

## Setup & run

```sh
uv sync
uv run python alarm_app.py
```

Run the offline logic tests with:

```sh
uv run python selftest.py
```

## Usage

1. **Select Percent Region** — drag over the percent (e.g. `45.22%`). Press *Esc*
   to cancel.
2. (Optional) **Select Testmode Region** and set the keyword. Tick *Enable
   Testmode suppression*.
3. Set **Alert when percent >** to your threshold. Tune:
   - *Hysteresis margin* — how far below the threshold before clearing.
   - *Confirmations* — consecutive frames required (higher = fewer false
     positives, slower to fire).
   - *Min OCR confidence* — raise it if you see misreads in the log.
   - *Poll interval (ms)* — how often to read the screen.
4. Pick an alert **Sound** (or browse a custom WAV) and press **Test sound**.
5. (Optional) Under **Stream Mute Toggle**, press **Set Button Position** and
   click your stream's mute button. Tick *Click it automatically when the alert
   fires* to have the app unmute the stream on trigger, or use **Toggle mute
   now** to click it manually (a short delay lets you focus the stream first).
6. Press **Start Monitoring**. The status panel shows the live percent,
   confidence, counters, and alert/Testmode state.
7. **Stop Alert** silences a sounding alert without stopping monitoring.

## Tuning for zero false positives

If a misread ever fires the alert, open `logs/` and look at the CSV rows around
the alert timestamp:

- Misreads usually show as **low `confidence`** or odd `raw` text → raise *Min
  OCR confidence*.
- A brief real spike that you don't care about → raise *Confirmations*.
- Reframe the Percent Region tighter around just the digits (less background =
  cleaner OCR).

## Files

| File | Purpose |
|------|---------|
| `alarm_app.py` | GUI, wiring, config persistence |
| `capture.py` | `mss` capture + DPI awareness |
| `ocr.py` | Pillow preprocessing, Tesseract, float/keyword parsing |
| `monitor.py` | Background loop + alert state machine |
| `sound.py` | Preset WAV generation + `winsound` playback |
| `region_select.py` | Fullscreen drag-to-select / click-to-pick overlay |
| `clicker.py` | Synthetic mouse click at a screen point (mute toggle) |
| `logger.py` | CSV/event logging |
| `selftest.py` | Offline logic tests |
| `config.json` | Saved settings (created at runtime) |
| `presets/` | Generated alarm WAVs |
| `logs/` | Per-session reading/event logs |

## Optional: build a standalone .exe

```sh
uv run pyinstaller --onefile --noconsole --add-data "presets;presets" alarm_app.py
```

Tesseract still needs to be installed separately on the target machine.
