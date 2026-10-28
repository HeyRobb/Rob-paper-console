"""Printer UART speed: shared default and the PC1_PRINTER_BAUDRATE override."""

import inspect
import logging

import pytest

import app.config as config
import app.hardware as hardware
from app.drivers import printer_mock, printer_serial


def test_default_printer_baudrate_is_115200():
    assert config.DEFAULT_PRINTER_BAUDRATE == 115200


@pytest.mark.parametrize("module", [printer_serial, printer_mock])
def test_drivers_default_to_the_shared_baudrate(module):
    parameter = inspect.signature(module.PrinterDriver.__init__).parameters["baudrate"]
    assert parameter.default == config.DEFAULT_PRINTER_BAUDRATE


def test_baudrate_env_unset_uses_default(monkeypatch):
    monkeypatch.delenv("PC1_PRINTER_BAUDRATE", raising=False)
    assert hardware._baudrate_env("PC1_PRINTER_BAUDRATE", 115200) == 115200


@pytest.mark.parametrize(
    "raw,expected",
    [("9600", 9600), (" 57600 ", 57600), ("115200", 115200)],
)
def test_baudrate_env_accepts_positive_integers(monkeypatch, raw, expected):
    monkeypatch.setenv("PC1_PRINTER_BAUDRATE", raw)
    assert hardware._baudrate_env("PC1_PRINTER_BAUDRATE", 115200) == expected


@pytest.mark.parametrize("raw", ["", "   ", "fast", "96e2", "0", "-9600", "9600.5"])
def test_baudrate_env_ignores_bad_values(monkeypatch, raw):
    monkeypatch.setenv("PC1_PRINTER_BAUDRATE", raw)
    assert hardware._baudrate_env("PC1_PRINTER_BAUDRATE", 115200) == 115200


def test_baudrate_env_warns_on_invalid_value(monkeypatch, caplog):
    monkeypatch.setenv("PC1_PRINTER_BAUDRATE", "fast")
    with caplog.at_level(logging.WARNING, logger=hardware.logger.name):
        hardware._baudrate_env("PC1_PRINTER_BAUDRATE", 115200)
    assert "PC1_PRINTER_BAUDRATE" in caplog.text
