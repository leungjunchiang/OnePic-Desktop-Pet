"""Regression checks for OS-level keyboard/mouse idle calculation."""

from onepic_desktop_pet.input_activity import InputIdleSnapshot, _windows_elapsed_ms


def test_windows_idle_tick_calculation_handles_32_bit_wrap() -> None:
    assert _windows_elapsed_ms(0x00000020, 0xFFFFFFF0) == 0x30


def test_windows_idle_tick_calculation_never_uses_signed_delta() -> None:
    assert _windows_elapsed_ms(1000, 900) == 100
    assert _windows_elapsed_ms(900, 1000) == 0xFFFFFFFF - 99


def test_idle_probe_failure_is_distinguishable_from_zero_idle() -> None:
    failed = InputIdleSnapshot(None, False, "test", "native failure")
    recent = InputIdleSnapshot(0.0, True, "test")
    assert failed.idle_seconds is None
    assert failed.available is False
    assert recent.idle_seconds == 0.0
    assert recent.available is True
