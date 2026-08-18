from __future__ import annotations

from onepic_desktop_pet import macos_dock
from onepic_desktop_pet.menu_model import UnifiedMenuModel


def test_status_item_is_a_noop_off_macos(monkeypatch) -> None:
    """Windows/Linux must not import or create a native menu-bar item."""

    monkeypatch.setattr(macos_dock.sys, "platform", "win32")
    model = UnifiedMenuModel(
        pet_name="六毛",
        state_provider=lambda: {},
        callbacks={},
    )
    controller = macos_dock.install_status_item(model)
    assert controller.installed is False
    controller.close()
