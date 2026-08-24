from __future__ import annotations

from onepic_desktop_pet import app as application
from onepic_desktop_pet import macos_dock
from onepic_desktop_pet.menu_model import UnifiedMenuModel


def test_status_item_is_a_noop_off_macos(monkeypatch) -> None:
    monkeypatch.setattr(macos_dock.sys, "platform", "win32")
    model = UnifiedMenuModel(
        pet_name="六毛",
        state_provider=lambda: {},
        callbacks={},
    )
    controller = macos_dock.install_status_item(model)
    assert controller.installed is False
    controller.close()


def test_dock_menu_is_a_noop_off_native_macos(monkeypatch) -> None:
    monkeypatch.setattr(macos_dock.sys, "platform", "win32")
    model = UnifiedMenuModel(
        pet_name="六毛",
        state_provider=lambda: {},
        callbacks={},
    )
    controller = macos_dock.install_dock_menu(model)
    assert controller.installed is False
    controller.close()


def test_dock_menu_uses_the_unified_model(monkeypatch) -> None:
    monkeypatch.setattr(macos_dock.sys, "platform", "darwin")
    model = UnifiedMenuModel(
        pet_name="六毛",
        state_provider=lambda: {},
        callbacks={},
    )
    controller = macos_dock.install_dock_menu(model)
    assert controller._model_provider().items("dock") == model.items("tray")
    controller.close()


def test_dock_projection_uses_pet_context_as_canonical(monkeypatch) -> None:
    """Dock and pet right-click menus must render the same model context."""

    monkeypatch.setattr(macos_dock.sys, "platform", "darwin")
    model = UnifiedMenuModel(
        pet_name="六毛",
        state_provider=lambda: {"visible": True},
        callbacks={"chat": lambda _checked=False: None},
    )
    controller = macos_dock.install_dock_menu(model)
    assert controller._model_provider().items("pet") == model.items("pet")
    assert controller._model_provider().items("dock") == model.items("pet")
    controller.close()


def test_macos_uses_native_status_item_for_tray(monkeypatch) -> None:
    monkeypatch.setattr(application.sys, "platform", "darwin")
    assert application._uses_qt_system_tray() is False

    monkeypatch.setattr(application.sys, "platform", "win32")
    assert application._uses_qt_system_tray() is True
