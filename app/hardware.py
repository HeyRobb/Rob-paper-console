import logging
import os
import platform
import threading
import time
from collections import deque
from typing import Callable, Deque, Optional

from app.config import PRINTER_WIDTH

logger = logging.getLogger(__name__)

# Auto-detect platform and use appropriate drivers
_is_raspberry_pi = platform.system() == "Linux" and os.path.exists(
    "/proc/device-tree/model"
)

printer_uart_preparation = None
printer_uart_reboot_pending = False
if _is_raspberry_pi:
    from app.printer_uart import prepare_printer_uart

    printer_uart_preparation = prepare_printer_uart()
    printer_uart_reboot_pending = printer_uart_preparation.suppress_printer_output

if _is_raspberry_pi and not printer_uart_reboot_pending:
    try:
        from app.drivers.printer_serial import PrinterDriver
    except ImportError:
        from app.drivers.printer_mock import PrinterDriver
else:
    from app.drivers.printer_mock import PrinterDriver

if _is_raspberry_pi:
    try:
        from app.drivers.dial_gpio import DialDriver
        from app.drivers.button_gpio import ButtonDriver
    except ImportError:
        from app.drivers.dial_mock import DialDriver
        from app.drivers.button_mock import ButtonDriver
else:
    from app.drivers.dial_mock import DialDriver
    from app.drivers.button_mock import ButtonDriver

# Global Hardware Instances
printer = PrinterDriver(width=PRINTER_WIDTH)
dial = DialDriver()

# Main Interface Button (Print / WiFi Setup / Reset) - GPIO 25 (Pin 22)
button = ButtonDriver(pin=25)

# --- PRINT ORCHESTRATION ---
# Shared reservation state so any producer (button, scheduler, email poll,
# webhooks, Slack, etc.) can claim the printer without racing the others.
print_lock = threading.Lock()
print_in_progress = False
hold_action_in_progress = False
hold_action_started_at = 0.0
last_print_time = 0.0
PRINT_DEBOUNCE_SECONDS = 3.0  # Minimum time between print jobs
HOLD_ACTION_TIMEOUT_SECONDS = 20.0

# --- BUTTON PRESS QUEUE ---
# Short presses made while a receipt is printing are remembered together with
# the dial position they were made at, then replayed in order as soon as the
# printer is idle. All queue state is guarded by print_lock so that "is the
# printer busy?" and "add to the queue" are one atomic decision.
MAX_QUEUED_PRESSES = 8
queued_presses: Deque[int] = deque()
last_accepted_press_position: Optional[int] = None
last_accepted_press_time = 0.0
_idle_callback: Optional[Callable[[], None]] = None


def printer_reserved_locked() -> bool:
    """Return True when the printer is reserved for a print or hold action."""
    return print_in_progress or hold_action_in_progress


def expire_stale_hold_action_locked(current_time: float):
    """Release a long-hold reservation if its matching release event was lost."""
    global hold_action_in_progress, hold_action_started_at

    if not hold_action_in_progress:
        return
    if (current_time - hold_action_started_at) < HOLD_ACTION_TIMEOUT_SECONDS:
        return

    hold_action_in_progress = False
    hold_action_started_at = 0.0
    logger.warning(
        "Cleared stale hold reservation after %.1fs without a matching release.",
        HOLD_ACTION_TIMEOUT_SECONDS,
    )


def _begin_print_job_locked(current_time: float):
    """Mark the printer as printing. Caller must hold print_lock."""
    global print_in_progress, last_print_time

    print_in_progress = True
    last_print_time = current_time
    if hasattr(printer, "clear_cancel_request"):
        printer.clear_cancel_request()


def try_begin_print_job(*, debounce: bool = False) -> bool:
    """Reserve the printer for a new print job."""
    with print_lock:
        current_time = time.time()
        expire_stale_hold_action_locked(current_time)

        if printer_reserved_locked():
            return False

        if debounce and (current_time - last_print_time) < PRINT_DEBOUNCE_SECONDS:
            return False

        _begin_print_job_locked(current_time)
        return True


def submit_button_press(position: int) -> str:
    """Handle a short button press made with the dial at ``position``.

    Returns one of:
      "started": the printer was idle and is now reserved for this press.
      "queued":  a receipt is printing; the press will be replayed, in order,
                 once the printer is idle.
      "ignored": dropped (hold in progress, post-print debounce, an accidental
                 double-tap of the same position, or a full queue).
    """
    global last_accepted_press_position, last_accepted_press_time

    with print_lock:
        current_time = time.time()
        expire_stale_hold_action_locked(current_time)

        # Pressing the same position again within the debounce window is a
        # double-tap, not a request for a second copy. Before queueing existed
        # the busy printer swallowed it; keep that behaviour.
        is_double_tap = (
            last_accepted_press_position == position
            and (current_time - last_accepted_press_time) < PRINT_DEBOUNCE_SECONDS
        )

        if print_in_progress:
            if is_double_tap:
                return "ignored"
            if len(queued_presses) >= MAX_QUEUED_PRESSES:
                logger.info(
                    "Ignoring button press for position %s: %d presses already queued.",
                    position,
                    len(queued_presses),
                )
                return "ignored"
            queued_presses.append(position)
            last_accepted_press_position = position
            last_accepted_press_time = current_time
            logger.info(
                "Queued button press for position %s (%d waiting).",
                position,
                len(queued_presses),
            )
            return "queued"

        if hold_action_in_progress:
            return "ignored"

        if (current_time - last_print_time) < PRINT_DEBOUNCE_SECONDS:
            return "ignored"

        _begin_print_job_locked(current_time)
        last_accepted_press_position = position
        last_accepted_press_time = current_time
        return "started"


def begin_next_queued_press() -> Optional[int]:
    """Reserve the idle printer for the oldest queued press.

    Returns that press's dial position, or None when the printer is reserved
    or nothing is waiting. Reserving and dequeuing happen under one lock so two
    threads can never both claim the same press, or one press and the printer.
    """
    with print_lock:
        current_time = time.time()
        expire_stale_hold_action_locked(current_time)

        if printer_reserved_locked() or not queued_presses:
            return None

        position = queued_presses.popleft()
        _begin_print_job_locked(current_time)
        return position


def clear_press_queue() -> int:
    """Drop every queued press and return how many were discarded."""
    with print_lock:
        dropped = len(queued_presses)
        queued_presses.clear()
        return dropped


def set_idle_callback(callback: Optional[Callable[[], None]]):
    """Register a function to call when the printer becomes idle with presses queued."""
    global _idle_callback
    _idle_callback = callback


def _notify_printer_idle():
    """Tell the registered callback to drain the queue, if there is anything to drain."""
    callback = _idle_callback
    if callback is None:
        return

    with print_lock:
        if printer_reserved_locked() or not queued_presses:
            return

    try:
        callback()
    except Exception:
        logger.exception("Dispatching a queued button press failed")


def request_print_cancel() -> bool:
    """Cancel the active logical print job, if one is currently reserved.

    Cancelling also drops any queued button presses: the user asked the printer
    to stop, so it should not immediately start the next receipt on its own.
    """
    with print_lock:
        if not print_in_progress:
            return False
        if not hasattr(printer, "request_cancel"):
            return False
        printer.request_cancel()
        dropped = len(queued_presses)
        queued_presses.clear()
        logger.info("Print cancellation requested by the user")
        if dropped:
            logger.info("Dropped %d queued button press(es) along with the cancelled print.", dropped)
        return True


def reserve_hold_action() -> bool:
    """Reserve the printer once the user crosses a long-hold threshold."""
    global hold_action_in_progress, hold_action_started_at, last_print_time

    with print_lock:
        current_time = time.time()
        expire_stale_hold_action_locked(current_time)

        if print_in_progress:
            return False

        hold_action_in_progress = True
        hold_action_started_at = current_time
        last_print_time = current_time
        return True


def promote_hold_to_print_job() -> bool:
    """Convert a hold reservation into an active print job."""
    global print_in_progress, hold_action_in_progress, hold_action_started_at, last_print_time

    with print_lock:
        current_time = time.time()
        expire_stale_hold_action_locked(current_time)

        if print_in_progress:
            return False

        hold_action_in_progress = False
        hold_action_started_at = 0.0
        print_in_progress = True
        last_print_time = current_time
        return True


def clear_print_reservation(*, clear_hold: bool = True):
    """Release active print/hold reservations."""
    global print_in_progress, hold_action_in_progress, hold_action_started_at, last_print_time

    with print_lock:
        if print_in_progress:
            # Start debounce from the end of the physical print window, not the start.
            last_print_time = time.time()
        print_in_progress = False
        if hasattr(printer, "clear_cancel_request"):
            printer.clear_cancel_request()
        if clear_hold:
            hold_action_in_progress = False
            hold_action_started_at = 0.0

    # Do not disturb the press that requested cancellation while it is still
    # physically held; its release must remain marked as consumed.
    if not getattr(button, "is_pressed", False) and hasattr(
        button, "drain_pending_events"
    ):
        try:
            button.drain_pending_events()
        except Exception:
            logger.debug("Failed to drain button events after print completion", exc_info=True)

    # The printer may now be free: let any queued button presses run.
    _notify_printer_idle()
