"""Offline self-tests for the parsing and alarm-decision logic.

Run with:  uv run python selftest.py

These cover the false-positive guards (confirmations, confidence gate,
hysteresis, Testmode suppression) without needing a screen or Tesseract.
"""
import queue

import ocr
import sound


def test_parser() -> None:
    cases = {
        "45.22%": 45.22, " 1,234.5 ": 1234.5, "12,5": 12.5, "-3.0": -3.0,
        "abc": None, "": None, "99": 99.0, "  -0,75% ": -0.75, "1.234,5": 1234.5,
    }
    for k, want in cases.items():
        got = ocr._parse_float(k)
        assert got == want, f"parse({k!r}) -> {got!r}, want {want!r}"
    assert ocr.keyword_present("status: test mode on", "Testmode")
    assert ocr.keyword_present("TESTMODE", "testmode")
    assert not ocr.keyword_present("running normally", "Testmode")
    print("parser: OK")


def test_presets() -> None:
    m = sound.ensure_presets("presets")
    assert set(m) == set(sound.PRESET_NAMES)
    print("presets: OK")


def test_monitor_logic() -> None:
    import capture
    import monitor as mon
    from monitor import Monitor, Settings

    capture.grab = lambda region: object()
    script = {"it": iter([])}
    tm = {"on": False}

    ocr.read_number = lambda img, **kw: (lambda v, c: ocr.NumberRead(
        value=v, confidence=c, raw="" if v is None else str(v)))(*next(script["it"]))
    ocr.read_text = lambda img, **kw: "testmode" if tm["on"] else "ok"
    ocr.keyword_present = lambda text, kw: "testmode" in text

    class FakePlayer:
        def play_loop(self, w): pass
        def stop(self): pass

    s = Settings(number_region=(0, 0, 10, 10), testmode_region=(0, 0, 10, 10),
                 testmode_enabled=True, keyword="Testmode", threshold=50.0,
                 hysteresis_margin=0.5, confirmations=3, conf_threshold=60.0)

    def run(reads, testmode=False):
        script["it"] = iter(reads)
        tm["on"] = testmode
        m = Monitor(lambda: s, FakePlayer(), queue.Queue(maxsize=99))
        m._reset_state()
        return [int(m._tick(s).alarm) for _ in reads]

    assert run([(40, 90), (99, 90), (40, 90), (41, 90)]) == [0, 0, 0, 0]   # spike
    assert run([(60, 90)] * 4) == [0, 0, 1, 1]                              # sustained
    assert run([(99, 10), (99, 20), (99, 30), (99, 40)]) == [0, 0, 0, 0]   # low conf
    assert run([(99, 90)] * 4, testmode=True) == [0, 0, 0, 0]              # testmode
    assert run([(60, 90)] * 3 + [(49.8, 90)] * 2 + [(40, 90)] * 3) == \
        [0, 0, 1, 1, 1, 1, 1, 0]                                            # hysteresis
    print("monitor logic: OK")


def test_mute_toggle_click() -> None:
    import capture
    import clicker
    from monitor import Monitor, Settings

    capture.grab = lambda region: object()
    ocr.read_number = lambda img, **kw: ocr.NumberRead(value=99.0, confidence=90.0, raw="99")
    ocr.read_text = lambda img, **kw: "ok"
    ocr.keyword_present = lambda text, kw: False

    clicks: list = []
    clicker.click = lambda x, y: clicks.append((x, y))

    class FakePlayer:
        def play_loop(self, w): pass
        def stop(self): pass

    base = dict(number_region=(0, 0, 10, 10), testmode_enabled=False,
                threshold=50.0, confirmations=2, conf_threshold=60.0,
                mute_toggle_point=(123, 456))

    # Enabled: one click on the rising edge of the alarm, not repeated per frame.
    s = Settings(mute_toggle_enabled=True, **base)
    m = Monitor(lambda: s, FakePlayer(), queue.Queue(maxsize=99))
    m._reset_state()
    for _ in range(4):
        m._tick(s)
    assert clicks == [(123, 456)], f"expected one click, got {clicks}"

    # Disabled: never clicks.
    clicks.clear()
    s = Settings(mute_toggle_enabled=False, **base)
    m = Monitor(lambda: s, FakePlayer(), queue.Queue(maxsize=99))
    m._reset_state()
    for _ in range(4):
        m._tick(s)
    assert clicks == [], f"expected no clicks, got {clicks}"
    print("mute toggle click: OK")


if __name__ == "__main__":
    test_parser()
    test_presets()
    test_monitor_logic()
    test_mute_toggle_click()
    print("ALL TESTS PASSED")
