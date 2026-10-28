import json
import threading

import pytest
from PIL import Image

from app.drivers.printer_serial import PrinterDriver, PrinterTransportError


class _FakeSerial:
    def __init__(self, *, max_write=None, fail_after=None, zero_progress=False):
        self.is_open = True
        self.max_write = max_write
        self.fail_after = fail_after
        self.zero_progress = zero_progress
        self.written = bytearray()
        self.write_calls = 0
        self.flush_calls = 0

    def write(self, data):
        self.write_calls += 1
        if self.zero_progress:
            return 0
        if self.fail_after is not None and len(self.written) >= self.fail_after:
            raise OSError("injected serial failure")

        raw = bytes(data)
        limit = len(raw)
        if self.max_write is not None:
            limit = min(limit, self.max_write)
        if self.fail_after is not None:
            limit = min(limit, self.fail_after - len(self.written))
        self.written.extend(raw[:limit])
        return limit

    def flush(self):
        self.flush_calls += 1


class _FakeBusyHandle:
    def __init__(self, values):
        self.values = list(values)
        self.index = 0

    def get_values(self):
        value = self.values[min(self.index, len(self.values) - 1)]
        self.index += 1
        return [value]


def _make_driver(fake_serial):
    driver = PrinterDriver.__new__(PrinterDriver)
    driver.ser = fake_serial
    driver.port = "/dev/fake-printer"
    driver.baudrate = 9600
    driver._io_lock = threading.RLock()
    driver._incident_lock = threading.Lock()
    driver._cancel_event = threading.Event()
    driver._busy_chip = None
    driver._busy_handle = None
    driver.last_transport_stats = {}
    return driver


def test_send_bitmap_cancels_only_between_complete_raster_strips():
    fake_serial = _FakeSerial()
    driver = _make_driver(fake_serial)
    original_flush = fake_serial.flush

    def cancel_after_first_strip():
        original_flush()
        if fake_serial.flush_calls == 1:
            driver.request_cancel()

    fake_serial.flush = cancel_after_first_strip

    stats = driver._send_bitmap(Image.new("1", (384, 72), 0))

    first_strip_size = 8 + (48 * 24) + driver.RASTER_GUARD_BYTES
    assert len(fake_serial.written) == first_strip_size
    assert stats["cancelled"] is True
    assert stats["strips"] == 1
    assert stats["cancelled_after_strip"] == 1
    assert driver.last_transport_stats == stats


def test_send_bitmap_uses_guarded_24_row_strips():
    fake_serial = _FakeSerial()
    driver = _make_driver(fake_serial)
    image = Image.new("1", (384, 50), 1)

    stats = driver._send_bitmap(image)

    expected_heights = [24, 24, 2]
    cursor = 0
    for height in expected_heights:
        payload_size = 48 * height
        assert fake_serial.written[cursor : cursor + 8] == (
            b"\x1d\x76\x30\x00" + bytes((48, 0, height, 0))
        )
        cursor += 8
        assert fake_serial.written[cursor : cursor + payload_size] == b"\x00" * payload_size
        cursor += payload_size
        assert fake_serial.written[cursor : cursor + 4] == b"\x00" * 4
        cursor += 4

    assert cursor == len(fake_serial.written)
    assert fake_serial.flush_calls == 3
    assert stats["strips"] == 3
    assert stats["bytes_sent"] == len(fake_serial.written)
    assert stats["busy_wait_events"] == 0


def test_write_retries_partial_writes_until_every_byte_is_sent():
    fake_serial = _FakeSerial(max_write=7)
    driver = _make_driver(fake_serial)
    payload = bytes(range(100))

    assert driver._write(payload) == len(payload)
    assert bytes(fake_serial.written) == payload
    assert fake_serial.write_calls > 1


def test_write_raises_when_serial_makes_no_progress():
    driver = _make_driver(_FakeSerial(zero_progress=True))

    with pytest.raises(PrinterTransportError, match="no progress"):
        driver._write(b"receipt")


def test_busy_pin_timeout_is_bounded_and_persisted(monkeypatch, tmp_path):
    incident_path = tmp_path / "printer-incidents.log"
    monkeypatch.setenv("PC1_PRINTER_INCIDENT_LOG", str(incident_path))
    monkeypatch.setattr(PrinterDriver, "BUSY_PIN_DEBOUNCE_SECONDS", 0)
    monkeypatch.setattr(PrinterDriver, "BUSY_PIN_POLL_SECONDS", 0)
    monkeypatch.setattr(PrinterDriver, "BUSY_PIN_WAIT_TIMEOUT", 0)

    driver = _make_driver(_FakeSerial())
    driver._busy_handle = _FakeBusyHandle([1])

    stats = driver._send_bitmap(Image.new("1", (384, 1), 1))

    assert stats["busy_pin_timed_out"] is True
    assert stats["busy_wait_events"] == 1
    records = [json.loads(line) for line in incident_path.read_text().splitlines()]
    assert records[0]["event"] == "busy_pin_timeout"


def test_bitmap_failure_is_raised_and_persisted(monkeypatch, tmp_path):
    incident_path = tmp_path / "printer-incidents.log"
    monkeypatch.setenv("PC1_PRINTER_INCIDENT_LOG", str(incident_path))
    driver = _make_driver(_FakeSerial(fail_after=100))

    with pytest.raises(PrinterTransportError, match="Serial write failed"):
        driver._send_bitmap(Image.new("1", (384, 24), 0))

    records = [json.loads(line) for line in incident_path.read_text().splitlines()]
    assert records[-1]["event"] == "bitmap_send_failed"
    assert records[-1]["strip"] == 1
    assert records[-1]["bytes_sent"] == 100
    assert driver.last_transport_stats["failed_strip"] == 1


class _FakeClock:
    """Deterministic stand-in for the driver's `time` module."""

    def __init__(self):
        self.now = 1000.0
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds

    def time(self):
        return self.now


def _paced_driver(monkeypatch, *, baudrate, cap):
    from app.drivers import printer_serial

    clock = _FakeClock()
    monkeypatch.setattr(printer_serial, "time", clock)
    monkeypatch.setattr(PrinterDriver, "SERIAL_MAX_BYTES_PER_SECOND", cap)
    driver = _make_driver(_FakeSerial())
    driver.baudrate = baudrate
    return driver, clock


def test_fast_link_is_paced_to_the_byte_rate_cap(monkeypatch):
    driver, clock = _paced_driver(monkeypatch, baudrate=115200, cap=960.0)

    stats = driver._send_bitmap(Image.new("1", (384, 48), 1))

    # 2 strips of (8 header + 48*24 payload + 4 guard) = 2328 bytes. The last
    # chunk is not waited on, so the delay covers everything sent before it.
    # Chunks are cut per strip (1164 bytes = 9 * 128 + 12), so the final chunk
    # is 12 bytes.
    strip_bytes = stats["bytes_sent"] // 2
    last_chunk = strip_bytes % PrinterDriver.SERIAL_WRITE_CHUNK_BYTES
    expected = (stats["bytes_sent"] - last_chunk) / 960.0
    assert stats["bytes_sent"] == 2328
    # Other sleeps (the UART flush after each strip) are not pacing waits, so
    # compare against the pacing counter rather than every sleep.
    assert stats["pace_wait_seconds"] == pytest.approx(expected, abs=0.01)
    assert sum(clock.sleeps) >= stats["pace_wait_seconds"]


def test_9600_baud_link_is_not_paced_again(monkeypatch):
    driver, clock = _paced_driver(monkeypatch, baudrate=9600, cap=960.0)

    stats = driver._send_bitmap(Image.new("1", (384, 48), 1))

    assert clock.sleeps == []
    assert stats["pace_wait_seconds"] == 0.0


def test_pace_cap_of_zero_disables_pacing(monkeypatch):
    driver, clock = _paced_driver(monkeypatch, baudrate=115200, cap=0.0)

    stats = driver._send_bitmap(Image.new("1", (384, 48), 1))

    assert clock.sleeps == []
    assert stats["pace_wait_seconds"] == 0.0


def test_higher_cap_paces_less(monkeypatch):
    slow, slow_clock = _paced_driver(monkeypatch, baudrate=115200, cap=960.0)
    slow._send_bitmap(Image.new("1", (384, 48), 1))
    fast, fast_clock = _paced_driver(monkeypatch, baudrate=115200, cap=4800.0)
    fast._send_bitmap(Image.new("1", (384, 48), 1))

    assert sum(fast_clock.sleeps) < sum(slow_clock.sleeps) / 4


def test_dense_black_strips_are_paced_slower_than_blank_ones(monkeypatch):
    blank, blank_clock = _paced_driver(monkeypatch, baudrate=115200, cap=960.0)
    blank._send_bitmap(Image.new("1", (384, 48), 1))
    dark, dark_clock = _paced_driver(monkeypatch, baudrate=115200, cap=960.0)
    dark_stats = dark._send_bitmap(Image.new("1", (384, 48), 0))

    # Two all-black strips add 0.07 s for each of their 48 rows on top of the
    # byte-rate time (about 2.4 s).
    assert sum(dark_clock.sleeps) > sum(blank_clock.sleeps) + 3.0
    assert dark_stats["pace_wait_seconds"] > 5.0


def test_dark_row_seconds_of_zero_disables_density_adjustment(monkeypatch):
    monkeypatch.setattr(PrinterDriver, "SERIAL_DARK_ROW_SECONDS", 0.0)
    plain, plain_clock = _paced_driver(monkeypatch, baudrate=115200, cap=960.0)
    plain._send_bitmap(Image.new("1", (384, 48), 1))
    dark, dark_clock = _paced_driver(monkeypatch, baudrate=115200, cap=960.0)
    dark._send_bitmap(Image.new("1", (384, 48), 0))

    assert sum(dark_clock.sleeps) == pytest.approx(sum(plain_clock.sleeps), abs=0.01)


def test_dense_strips_are_not_slowed_on_a_9600_baud_link(monkeypatch):
    driver, clock = _paced_driver(monkeypatch, baudrate=9600, cap=960.0)

    driver._send_bitmap(Image.new("1", (384, 48), 0))

    assert clock.sleeps == []


def _feed_bytes(monkeypatch, mode, lines):
    monkeypatch.setattr(PrinterDriver, "FEED_COMMAND", mode)
    fake = _FakeSerial()
    driver = _make_driver(fake)
    driver.feed_direct(lines)
    return bytes(fake.written)


def test_feed_direct_both_sends_esc_d_and_esc_j(monkeypatch):
    sent = _feed_bytes(monkeypatch, "both", 2)
    assert b"\x1b\x64\x02" in sent
    assert b"\x1b\x4a\x30" in sent


def test_feed_direct_esc_j_only_feeds_once(monkeypatch):
    sent = _feed_bytes(monkeypatch, "esc_j", 2)
    assert b"\x1b\x64" not in sent
    assert b"\x1b\x4a\x30" in sent


def test_feed_direct_esc_d_only_feeds_once(monkeypatch):
    sent = _feed_bytes(monkeypatch, "esc_d", 2)
    assert b"\x1b\x64\x02" in sent
    assert b"\x1b\x4a" not in sent
