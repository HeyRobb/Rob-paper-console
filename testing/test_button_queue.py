"""Button presses made while a receipt is printing are queued and replayed in order."""

import asyncio
import random
import threading
import time
from collections import deque

import pytest

import app.hardware as hardware_module
import app.main as main_module
import app.selection_mode as selection_mode


@pytest.fixture(autouse=True)
def clean_printer_state(monkeypatch):
    monkeypatch.setattr(hardware_module, "print_in_progress", False)
    monkeypatch.setattr(hardware_module, "hold_action_in_progress", False)
    monkeypatch.setattr(hardware_module, "hold_action_started_at", 0.0)
    monkeypatch.setattr(hardware_module, "last_print_time", 0.0)
    monkeypatch.setattr(hardware_module, "queued_presses", deque())
    monkeypatch.setattr(hardware_module, "last_accepted_press_position", None)
    monkeypatch.setattr(hardware_module, "last_accepted_press_time", 0.0)
    monkeypatch.setattr(hardware_module, "_idle_callback", None)
    selection_mode.exit_selection_mode()
    yield
    selection_mode.exit_selection_mode()


class Clock:
    def __init__(self, now=1_000.0):
        self.now = now

    def __call__(self):
        return self.now


@pytest.fixture
def clock(monkeypatch):
    fake = Clock()
    monkeypatch.setattr(hardware_module.time, "time", fake)
    return fake


# --- hardware: submit_button_press -------------------------------------------


def test_press_on_idle_printer_starts_a_job(clock):
    assert hardware_module.submit_button_press(3) == "started"
    assert hardware_module.print_in_progress is True
    assert list(hardware_module.queued_presses) == []


def test_presses_during_a_print_are_queued_in_order(clock):
    assert hardware_module.submit_button_press(1) == "started"

    assert hardware_module.submit_button_press(4) == "queued"
    assert hardware_module.submit_button_press(2) == "queued"

    assert list(hardware_module.queued_presses) == [4, 2]


def test_double_tap_of_the_same_position_does_not_queue_a_second_copy(clock):
    assert hardware_module.submit_button_press(3) == "started"

    clock.now += 0.5
    assert hardware_module.submit_button_press(3) == "ignored"
    assert list(hardware_module.queued_presses) == []


def test_same_position_after_the_debounce_window_is_a_deliberate_repeat(clock):
    assert hardware_module.submit_button_press(3) == "started"

    clock.now += hardware_module.PRINT_DEBOUNCE_SECONDS + 0.1
    assert hardware_module.submit_button_press(3) == "queued"
    assert list(hardware_module.queued_presses) == [3]


def test_a_different_position_right_away_is_queued_not_treated_as_double_tap(clock):
    assert hardware_module.submit_button_press(3) == "started"

    clock.now += 0.2
    assert hardware_module.submit_button_press(4) == "queued"


def test_queue_is_capped(clock):
    assert hardware_module.submit_button_press(1) == "started"

    for i in range(hardware_module.MAX_QUEUED_PRESSES):
        # Alternate positions so no press is mistaken for a double-tap.
        assert hardware_module.submit_button_press(2 + (i % 2)) == "queued"

    assert hardware_module.submit_button_press(8) == "ignored"
    assert len(hardware_module.queued_presses) == hardware_module.MAX_QUEUED_PRESSES


def test_press_during_a_hold_reservation_is_ignored(clock):
    hardware_module.hold_action_in_progress = True
    hardware_module.hold_action_started_at = clock.now

    assert hardware_module.submit_button_press(2) == "ignored"
    assert list(hardware_module.queued_presses) == []
    assert hardware_module.print_in_progress is False


def test_idle_press_right_after_a_print_still_uses_post_print_debounce(clock):
    hardware_module.last_print_time = clock.now - 1.0

    assert hardware_module.submit_button_press(2) == "ignored"
    assert hardware_module.print_in_progress is False

    clock.now += hardware_module.PRINT_DEBOUNCE_SECONDS
    assert hardware_module.submit_button_press(2) == "started"


# --- hardware: begin_next_queued_press / idle hook ---------------------------


def test_begin_next_queued_press_waits_for_an_idle_printer(clock):
    hardware_module.queued_presses.extend([5, 6])
    hardware_module.print_in_progress = True

    assert hardware_module.begin_next_queued_press() is None
    assert list(hardware_module.queued_presses) == [5, 6]

    hardware_module.print_in_progress = False
    hardware_module.hold_action_in_progress = True
    hardware_module.hold_action_started_at = clock.now
    assert hardware_module.begin_next_queued_press() is None


def test_begin_next_queued_press_is_fifo_and_reserves_the_printer(clock):
    hardware_module.queued_presses.extend([5, 6])

    assert hardware_module.begin_next_queued_press() == (5, 0.0)
    assert hardware_module.print_in_progress is True
    assert hardware_module.begin_next_queued_press() is None  # printer now reserved
    assert list(hardware_module.queued_presses) == [6]


def test_begin_next_queued_press_reports_what_is_left_of_the_settle_time(clock, monkeypatch):
    monkeypatch.setattr(hardware_module, "QUEUED_PRESS_SETTLE_SECONDS", 3.0)
    hardware_module.queued_presses.extend([5, 6, 7])

    # A receipt ended 1 s ago: 2 s of the 3 s settle time remain.
    hardware_module.last_print_time = clock.now - 1.0
    assert hardware_module.begin_next_queued_press() == (5, pytest.approx(2.0))
    hardware_module.print_in_progress = False

    # It ended just now: the whole settle time remains.
    hardware_module.last_print_time = clock.now
    assert hardware_module.begin_next_queued_press() == (6, pytest.approx(3.0))
    hardware_module.print_in_progress = False

    # It ended longer ago than the settle time: nothing left to wait.
    hardware_module.last_print_time = clock.now - 10.0
    assert hardware_module.begin_next_queued_press() == (7, 0.0)


def test_settle_time_can_be_overridden_from_the_environment(monkeypatch):
    monkeypatch.delenv("PC1_TEST_SETTLE", raising=False)
    assert hardware_module._float_env("PC1_TEST_SETTLE", 3.0) == 3.0

    monkeypatch.setenv("PC1_TEST_SETTLE", "1.5")
    assert hardware_module._float_env("PC1_TEST_SETTLE", 3.0) == 1.5

    monkeypatch.setenv("PC1_TEST_SETTLE", "0")
    assert hardware_module._float_env("PC1_TEST_SETTLE", 3.0) == 0.0

    for bad in ("soon", "", "-2"):
        monkeypatch.setenv("PC1_TEST_SETTLE", bad)
        assert hardware_module._float_env("PC1_TEST_SETTLE", 3.0) == 3.0


def test_begin_next_queued_press_with_nothing_waiting_does_not_reserve(clock):
    assert hardware_module.begin_next_queued_press() is None
    assert hardware_module.print_in_progress is False


def test_finishing_a_print_notifies_idle_callback_when_presses_are_waiting(clock):
    calls = []
    hardware_module.set_idle_callback(lambda: calls.append("idle"))
    hardware_module.print_in_progress = True
    hardware_module.queued_presses.append(4)

    hardware_module.clear_print_reservation(clear_hold=False)

    assert calls == ["idle"]


def test_finishing_a_print_with_an_empty_queue_does_not_notify(clock):
    calls = []
    hardware_module.set_idle_callback(lambda: calls.append("idle"))
    hardware_module.print_in_progress = True

    hardware_module.clear_print_reservation(clear_hold=False)

    assert calls == []


def test_idle_callback_is_not_fired_while_a_hold_still_reserves_the_printer(clock):
    calls = []
    hardware_module.set_idle_callback(lambda: calls.append("idle"))
    hardware_module.print_in_progress = True
    hardware_module.hold_action_in_progress = True
    hardware_module.hold_action_started_at = clock.now
    hardware_module.queued_presses.append(4)

    hardware_module.clear_print_reservation(clear_hold=False)

    assert calls == []
    assert list(hardware_module.queued_presses) == [4]


def test_a_failing_idle_callback_does_not_break_clearing_the_reservation(clock):
    def boom():
        raise RuntimeError("dispatch failed")

    hardware_module.set_idle_callback(boom)
    hardware_module.print_in_progress = True
    hardware_module.queued_presses.append(4)

    hardware_module.clear_print_reservation(clear_hold=False)

    assert hardware_module.print_in_progress is False


def test_cancelling_a_print_also_drops_queued_presses(clock, monkeypatch):
    class FakePrinter:
        def request_cancel(self):
            pass

        def clear_cancel_request(self):
            pass

    monkeypatch.setattr(hardware_module, "printer", FakePrinter())
    assert hardware_module.submit_button_press(1) == "started"
    assert hardware_module.submit_button_press(2) == "queued"
    assert hardware_module.submit_button_press(3) == "queued"

    assert hardware_module.request_print_cancel() is True

    assert list(hardware_module.queued_presses) == []


def test_clear_press_queue_reports_how_many_were_dropped():
    hardware_module.queued_presses.extend([1, 2, 3])

    assert hardware_module.clear_press_queue() == 3
    assert hardware_module.clear_press_queue() == 0


def test_no_queued_press_is_lost_or_run_twice_under_concurrency(monkeypatch):
    """Presses race with jobs finishing; every accepted press must run exactly once."""
    monkeypatch.setattr(hardware_module, "PRINT_DEBOUNCE_SECONDS", 0.0)
    monkeypatch.setattr(hardware_module, "MAX_QUEUED_PRESSES", 10_000)

    ran = []
    accepted = []
    bookkeeping = threading.Lock()
    job_threads = []

    def run_job(position):
        with bookkeeping:
            ran.append(position)
        time.sleep(random.uniform(0, 0.002))
        hardware_module.clear_print_reservation(clear_hold=False)

    def start_job(position):
        thread = threading.Thread(target=run_job, args=(position,))
        with bookkeeping:
            job_threads.append(thread)
        thread.start()

    def idle_callback():
        next_press = hardware_module.begin_next_queued_press()
        if next_press is not None:
            start_job(next_press[0])

    hardware_module.set_idle_callback(idle_callback)

    def producer(seed):
        rng = random.Random(seed)
        for _ in range(60):
            position = rng.randint(1, 8)
            outcome = hardware_module.submit_button_press(position)
            if outcome == "started":
                start_job(position)
            if outcome in ("started", "queued"):
                with bookkeeping:
                    accepted.append(position)
            time.sleep(rng.uniform(0, 0.001))

    producers = [threading.Thread(target=producer, args=(seed,)) for seed in range(4)]
    for thread in producers:
        thread.start()
    for thread in producers:
        thread.join()

    deadline = time.time() + 10
    while time.time() < deadline:
        with hardware_module.print_lock:
            settled = (
                not hardware_module.print_in_progress
                and not hardware_module.queued_presses
            )
        if settled:
            break
        time.sleep(0.01)

    while True:
        with bookkeeping:
            pending = [t for t in job_threads if t.is_alive()]
        if not pending:
            break
        pending[0].join()

    assert list(hardware_module.queued_presses) == []
    assert hardware_module.print_in_progress is False
    assert sorted(ran) == sorted(accepted)


# --- main: button handler and replay ----------------------------------------


class FakeDial:
    def __init__(self, position=1):
        self.position = position

    def read_position(self):
        return self.position


class RunningLoop:
    def is_running(self):
        return True


@pytest.fixture
def scheduled(monkeypatch):
    """Capture coroutines the button handlers hand to the event loop."""
    captured = []

    def fake_run_coroutine_threadsafe(coro, loop):
        captured.append(coro)
        return None

    monkeypatch.setattr(main_module, "global_loop", RunningLoop())
    monkeypatch.setattr(
        main_module.asyncio, "run_coroutine_threadsafe", fake_run_coroutine_threadsafe
    )
    yield captured
    for coro in captured:
        coro.close()


def test_button_press_while_printing_is_queued_with_its_dial_position(monkeypatch, scheduled):
    monkeypatch.setattr(main_module, "dial", FakeDial(position=4))
    hardware_module.print_in_progress = True

    main_module.on_button_press_threadsafe()

    assert list(hardware_module.queued_presses) == [4]
    assert scheduled == []


def test_button_press_on_idle_printer_triggers_the_current_channel(monkeypatch, scheduled):
    monkeypatch.setattr(main_module, "dial", FakeDial(position=2))

    main_module.on_button_press_threadsafe()

    assert [coro.cr_code.co_name for coro in scheduled] == ["trigger_current_channel"]
    assert hardware_module.print_in_progress is True
    assert list(hardware_module.queued_presses) == []


def test_button_press_in_selection_mode_passes_the_dial_position(monkeypatch, scheduled):
    monkeypatch.setattr(main_module, "dial", FakeDial(position=6))
    selection_mode.enter_selection_mode(lambda pos: None, "test-menu")

    main_module.on_button_press_threadsafe()

    assert [coro.cr_code.co_name for coro in scheduled] == ["handle_selection_async"]
    assert scheduled[0].cr_frame.f_locals["dial_position"] == 6


def test_dispatch_replays_the_oldest_queued_press_and_holds_the_printer(scheduled):
    hardware_module.queued_presses.extend([7, 2])

    main_module._dispatch_next_queued_press()

    assert [coro.cr_code.co_name for coro in scheduled] == ["handle_queued_press_async"]
    assert scheduled[0].cr_frame.f_locals["position"] == 7
    assert hardware_module.print_in_progress is True
    assert list(hardware_module.queued_presses) == [2]


def test_dispatch_asks_the_replay_to_wait_out_the_settle_time(scheduled, monkeypatch):
    monkeypatch.setattr(hardware_module, "QUEUED_PRESS_SETTLE_SECONDS", 3.0)
    hardware_module.last_print_time = time.time()  # a receipt just finished
    hardware_module.queued_presses.append(4)

    main_module._dispatch_next_queued_press()

    assert scheduled[0].cr_frame.f_locals["position"] == 4
    assert scheduled[0].cr_frame.f_locals["settle_wait"] == pytest.approx(3.0, abs=0.5)


def test_dispatch_does_nothing_when_the_printer_is_busy(scheduled):
    hardware_module.queued_presses.append(7)
    hardware_module.print_in_progress = True

    main_module._dispatch_next_queued_press()

    assert scheduled == []
    assert list(hardware_module.queued_presses) == [7]


def test_dispatch_does_nothing_when_nothing_is_queued(scheduled):
    main_module._dispatch_next_queued_press()

    assert scheduled == []
    assert hardware_module.print_in_progress is False


def test_dispatch_releases_the_printer_if_the_event_loop_is_not_running(monkeypatch):
    monkeypatch.setattr(main_module, "global_loop", None)
    hardware_module.queued_presses.append(7)

    main_module._dispatch_next_queued_press()

    assert hardware_module.print_in_progress is False
    assert list(hardware_module.queued_presses) == []


def test_replayed_press_prints_the_channel_it_was_made_on(monkeypatch):
    calls = []

    async def fake_trigger_channel(position, scheduled=False):
        calls.append(("channel", position))

    async def fake_handle_selection(position):
        calls.append(("selection", position))

    monkeypatch.setattr(main_module, "trigger_channel", fake_trigger_channel)
    monkeypatch.setattr(main_module, "handle_selection_async", fake_handle_selection)

    asyncio.run(main_module.handle_queued_press_async(5))

    assert calls == [("channel", 5)]


def test_replayed_press_becomes_a_menu_choice_when_a_selection_mode_is_active(monkeypatch):
    calls = []

    async def fake_trigger_channel(position, scheduled=False):
        calls.append(("channel", position))

    async def fake_handle_selection(position):
        calls.append(("selection", position))

    monkeypatch.setattr(main_module, "trigger_channel", fake_trigger_channel)
    monkeypatch.setattr(main_module, "handle_selection_async", fake_handle_selection)
    selection_mode.enter_selection_mode(lambda pos: None, "adventure-test")

    asyncio.run(main_module.handle_queued_press_async(3))

    assert calls == [("selection", 3)]


def test_replayed_press_waits_for_the_printer_to_settle_before_acting(monkeypatch):
    order = []

    async def fake_sleep(seconds):
        order.append(f"sleep:{seconds}")

    async def fake_trigger_channel(position, scheduled=False):
        order.append(f"channel:{position}")

    monkeypatch.setattr(main_module.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(main_module, "trigger_channel", fake_trigger_channel)

    asyncio.run(main_module.handle_queued_press_async(5, 2.5))

    assert order == ["sleep:2.5", "channel:5"]


def test_replayed_press_does_not_sleep_when_the_printer_has_already_settled(monkeypatch):
    order = []

    async def fake_sleep(seconds):
        order.append(f"sleep:{seconds}")

    async def fake_trigger_channel(position, scheduled=False):
        order.append(f"channel:{position}")

    monkeypatch.setattr(main_module.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(main_module, "trigger_channel", fake_trigger_channel)

    asyncio.run(main_module.handle_queued_press_async(5, 0.0))

    assert order == ["channel:5"]


def test_cancelling_during_the_settle_pause_drops_the_replay_and_frees_the_printer(monkeypatch):
    calls = []

    class CancelledPrinter:
        def is_cancel_requested(self):
            return True

    async def fake_sleep(seconds):
        pass

    async def fake_trigger_channel(position, scheduled=False):
        calls.append(("channel", position))

    monkeypatch.setattr(main_module, "printer", CancelledPrinter())
    monkeypatch.setattr(main_module.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(main_module, "trigger_channel", fake_trigger_channel)
    hardware_module.print_in_progress = True  # reserved by the dispatcher

    asyncio.run(main_module.handle_queued_press_async(5, 3.0))

    assert calls == []
    assert hardware_module.print_in_progress is False


def test_factory_reset_discards_queued_presses(monkeypatch, scheduled):
    hardware_module.hold_action_in_progress = True
    hardware_module.hold_action_started_at = time.time()
    hardware_module.queued_presses.extend([1, 2])

    main_module.on_factory_reset_threadsafe()

    assert list(hardware_module.queued_presses) == []


def test_queued_presses_replay_in_order_without_overlapping(monkeypatch):
    """Full chain on a real event loop: press, queue two more, turn the knob away."""
    events = []
    stamps = {}
    settle = 0.2
    dial = FakeDial(position=3)
    monkeypatch.setattr(main_module, "dial", dial)
    monkeypatch.setattr(hardware_module, "PRINT_DEBOUNCE_SECONDS", 0.0)
    monkeypatch.setattr(hardware_module, "QUEUED_PRESS_SETTLE_SECONDS", settle)

    async def fake_trigger_channel(position, scheduled=False):
        events.append(("start", position))
        stamps[("start", position)] = time.monotonic()
        await asyncio.sleep(0.05)
        events.append(("end", position))
        stamps[("end", position)] = time.monotonic()
        # trigger_channel releases the reservation in its finally block.
        hardware_module.clear_print_reservation(clear_hold=False)

    monkeypatch.setattr(main_module, "trigger_channel", fake_trigger_channel)

    async def scenario():
        loop = asyncio.get_running_loop()
        monkeypatch.setattr(main_module, "global_loop", loop)
        hardware_module.set_idle_callback(main_module._dispatch_next_queued_press)

        def press(position):
            dial.position = position
            main_module.on_button_press_threadsafe()

        # The real button driver calls back from its own thread.
        await loop.run_in_executor(None, press, 3)  # starts printing channel 3
        await asyncio.sleep(0.01)
        await loop.run_in_executor(None, press, 5)  # queued
        await loop.run_in_executor(None, press, 7)  # queued
        dial.position = 1  # turning the knob afterwards must not change them

        deadline = time.time() + 5
        while time.time() < deadline:
            with hardware_module.print_lock:
                busy = (
                    hardware_module.print_in_progress
                    or bool(hardware_module.queued_presses)
                )
            if not busy:
                break
            await asyncio.sleep(0.01)

    asyncio.run(scenario())

    assert events == [
        ("start", 3),
        ("end", 3),
        ("start", 5),
        ("end", 5),
        ("start", 7),
        ("end", 7),
    ]
    # Each queued receipt waits out the settle time after the previous one ends,
    # so the printer never gets new data while still feeding the last paper.
    for finished, started in ((3, 5), (5, 7)):
        gap = stamps[("start", started)] - stamps[("end", finished)]
        assert gap >= settle - 0.02
    assert hardware_module.print_in_progress is False
