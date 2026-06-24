"""OCR: image preprocessing, Tesseract reading, and parsing.

Two read paths:
  * read_number()  -> parsed float + confidence, tuned for digits.
  * read_text()    -> raw text, used for Testmode keyword detection.

We keep preprocessing in Pillow only (no NumPy/OpenCV) for a small, stable
dependency footprint.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass

from PIL import Image, ImageOps

import pytesseract

# --- Tesseract discovery ----------------------------------------------------

_DEFAULT_PATHS = [
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
]


def locate_tesseract() -> str | None:
    """Return a usable tesseract.exe path, or None if not found.

    Also configures pytesseract to use it. Honours the TESSERACT_CMD env var.
    """
    candidates = []
    env = os.environ.get("TESSERACT_CMD")
    if env:
        candidates.append(env)
    on_path = shutil.which("tesseract")
    if on_path:
        candidates.append(on_path)
    candidates.extend(_DEFAULT_PATHS)

    for path in candidates:
        if path and os.path.isfile(path):
            pytesseract.pytesseract.tesseract_cmd = path
            return path
    return None


def tesseract_available() -> bool:
    if locate_tesseract() is None:
        return False
    try:
        pytesseract.get_tesseract_version()
        return True
    except Exception:
        return False


def tesseract_version() -> str | None:
    """Human-readable Tesseract version, or None if unavailable."""
    if locate_tesseract() is None:
        return None
    try:
        return str(pytesseract.get_tesseract_version())
    except Exception:
        return None


# --- Auto-install -----------------------------------------------------------

# Ordered preference: try winget first, then Chocolatey, then Scoop. Each entry
# is (display name, detection command, install command). The first whose CLI is
# found on PATH is attempted; on failure we fall through to the next.
_INSTALLERS: list[tuple[str, str, list[str]]] = [
    ("winget", "winget", [
        "winget", "install", "--id", "UB-Mannheim.TesseractOCR", "-e",
        "--accept-source-agreements", "--accept-package-agreements",
    ]),
    ("Chocolatey", "choco", ["choco", "install", "tesseract", "-y"]),
    ("Scoop", "scoop", ["scoop", "install", "tesseract"]),
]

# CREATE_NO_WINDOW: keep a console from flashing up under the windowed exe.
_NO_WINDOW = 0x08000000


def available_installers() -> list[str]:
    """Display names of package managers found on this machine, in order."""
    return [name for name, cli, _ in _INSTALLERS if shutil.which(cli)]


def install_tesseract() -> tuple[bool, str, str]:
    """Try to install Tesseract via the first available package manager.

    Returns (ok, method, detail). ``ok`` is True only if a manager reported
    success *and* Tesseract is subsequently importable. ``method`` names the
    manager used (or "none"/"failed"); ``detail`` is a human-readable message.
    Blocking and slow (may trigger a UAC prompt) — run off the UI thread.
    """
    found = [(name, cmd) for name, cli, cmd in _INSTALLERS if shutil.which(cli)]
    if not found:
        return (False, "none", "No supported package manager (winget, "
                               "Chocolatey, or Scoop) was found.")

    errors: list[str] = []
    for name, cmd in found:
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, creationflags=_NO_WINDOW
            )
        except Exception as exc:
            errors.append(f"{name}: {exc}")
            continue
        if proc.returncode == 0 and tesseract_available():
            return (True, name, f"Installed via {name}.")
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()
        msg = tail[-1].strip() if tail else f"exit code {proc.returncode}"
        errors.append(f"{name}: {msg}")

    return (False, "failed", " | ".join(errors) or "Installation failed.")


# --- Preprocessing ----------------------------------------------------------

def _preprocess(img: Image.Image, scale: int = 4, threshold: int = 140) -> Image.Image:
    """Grayscale, upscale, autocontrast and binarize to dark-on-light.

    Auto-detects light text on a dark background and inverts so Tesseract
    always sees dark glyphs on a light field.
    """
    gray = ImageOps.grayscale(img)
    if scale > 1:
        gray = gray.resize(
            (max(1, gray.width * scale), max(1, gray.height * scale)),
            Image.LANCZOS,
        )
    gray = ImageOps.autocontrast(gray)

    # Decide polarity from the mean brightness: a dark image is light-on-dark.
    mean = sum(gray.getdata()) / (gray.width * gray.height)
    if mean < 128:
        gray = ImageOps.invert(gray)

    # Binarize. Pixels brighter than the threshold become white, else black.
    bw = gray.point(lambda p: 255 if p > threshold else 0)
    return bw


# --- Number reading ---------------------------------------------------------

_NUMBER_RE = re.compile(r"[-+]?\d[\d.,]*\d|[-+]?\d")


@dataclass
class NumberRead:
    value: float | None       # parsed float, or None if unreadable
    confidence: float          # mean Tesseract word confidence (0-100), -1 if none
    raw: str                   # raw OCR text (stripped)


def _parse_float(text: str) -> float | None:
    cleaned = text.strip().replace(" ", "")
    m = _NUMBER_RE.search(cleaned)
    if not m:
        return None
    token = m.group(0)

    sign = ""
    if token and token[0] in "+-":
        sign = "-" if token[0] == "-" else ""
        token = token[1:]

    has_comma = "," in token
    has_dot = "." in token
    if has_comma and has_dot:
        # Mixed separators: the right-most one is the decimal point; the rest
        # are thousands separators (handles "1,234.5" and "1.234,5").
        last = max(token.rfind(","), token.rfind("."))
        int_part = token[:last].replace(",", "").replace(".", "")
        frac_part = token[last + 1:].replace(",", "").replace(".", "")
        token = f"{int_part}.{frac_part}"
    elif has_comma:
        # A single comma is treated as a decimal point ("12,5" -> 12.5);
        # multiple commas are thousands separators ("1,234" -> 1234).
        token = token.replace(",", ".") if token.count(",") == 1 else token.replace(",", "")
    elif has_dot and token.count(".") > 1:
        token = token.replace(".", "")  # multiple dots = thousands separators

    try:
        return float(sign + token)
    except ValueError:
        return None


def read_number(img: Image.Image, *, scale: int = 4, threshold: int = 140) -> NumberRead:
    """OCR a region expected to contain a number; return value + confidence."""
    proc = _preprocess(img, scale=scale, threshold=threshold)
    config = "--oem 3 --psm 7 -c tessedit_char_whitelist=0123456789.,%+-"

    try:
        data = pytesseract.image_to_data(
            proc, config=config, output_type=pytesseract.Output.DICT
        )
    except Exception as exc:
        return NumberRead(value=None, confidence=-1.0, raw=f"<ocr error: {exc}>")

    words, confs = [], []
    for txt, conf in zip(data.get("text", []), data.get("conf", [])):
        txt = (txt or "").strip()
        if not txt:
            continue
        try:
            c = float(conf)
        except (TypeError, ValueError):
            c = -1.0
        if c < 0:
            continue
        words.append(txt)
        confs.append(c)

    raw = "".join(words)
    value = _parse_float(raw)
    confidence = (sum(confs) / len(confs)) if confs else -1.0
    return NumberRead(value=value, confidence=confidence, raw=raw)


# --- Text reading (Testmode) ------------------------------------------------

def read_text(img: Image.Image, *, scale: int = 3, threshold: int = 140) -> str:
    """OCR a region as free text (lowercased) for keyword detection."""
    proc = _preprocess(img, scale=scale, threshold=threshold)
    config = "--oem 3 --psm 6"
    try:
        text = pytesseract.image_to_string(proc, config=config)
    except Exception:
        return ""
    return text.strip().lower()


def _normalize(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def keyword_present(text: str, keyword: str) -> bool:
    """True if keyword appears in text, tolerant of OCR spacing/case slips."""
    keyword = keyword.strip()
    if not keyword:
        return False
    if keyword.lower() in text.lower():
        return True
    # Fallback: compare alphanumeric-only forms (handles stray spaces/punct).
    return _normalize(keyword) in _normalize(text)
