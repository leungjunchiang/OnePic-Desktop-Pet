from __future__ import annotations

import sys
import types

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


def test_dock_menu_uses_qt_native_dock_bridge(monkeypatch) -> None:
    """The real macOS path must register QMenu with Qt's Dock bridge."""

    monkeypatch.setattr(macos_dock.sys, "platform", "darwin")

    class Signal:
        def __init__(self) -> None:
            self.callbacks = []

        def connect(self, callback) -> None:
            self.callbacks.append(callback)

        def emit(self) -> None:
            for callback in self.callbacks:
                callback()

    class FakeQMenu:
        instances = []

        def __init__(self) -> None:
            self.aboutToShow = Signal()
            self.clear_count = 0
            self.as_dock_menu = False
            FakeQMenu.instances.append(self)

        def clear(self) -> None:
            self.clear_count += 1

        def setAsDockMenu(self) -> None:
            self.as_dock_menu = True

        def hide(self) -> None:
            pass

        def deleteLater(self) -> None:
            pass

    class FakeQApplication:
        @staticmethod
        def instance():
            return object()

    qt_widgets = types.ModuleType("PySide6.QtWidgets")
    qt_widgets.QApplication = FakeQApplication
    qt_widgets.QMenu = FakeQMenu
    pyside = types.ModuleType("PySide6")
    pyside.QtWidgets = qt_widgets
    monkeypatch.setitem(sys.modules, "PySide6", pyside)
    monkeypatch.setitem(sys.modules, "PySide6.QtWidgets", qt_widgets)

    model = UnifiedMenuModel(
        pet_name="六毛",
        state_provider=lambda: {"visible": True},
        callbacks={"chat": lambda _checked=False: None},
    )
    rendered_contexts = []
    monkeypatch.setattr(
        macos_dock,
        "populate_qmenu",
        lambda _menu, _model, context: rendered_contexts.append(context),
    )

    controller = macos_dock.install_dock_menu(model)
    assert controller.installed is True
    assert len(FakeQMenu.instances) == 1
    menu = FakeQMenu.instances[0]
    assert menu.as_dock_menu is True
    assert rendered_contexts == ["pet"]

    # Dynamic work status is refreshed when the Dock asks Qt to show it.
    menu.aboutToShow.emit()
    assert rendered_contexts == ["pet", "pet"]
    controller.close()


def test_dock_menu_reasserts_qt_bridge_after_startup(monkeypatch) -> None:
    """A late Qt delegate change must not discard the Dock menu."""

    monkeypatch.setattr(macos_dock.sys, "platform", "darwin")

    class Signal:
        def connect(self, _callback) -> None:
            pass

    class FakeQMenu:
        def __init__(self) -> None:
            self.aboutToShow = Signal()
            self.set_calls = 0

        def clear(self) -> None:
            pass

        def setAsDockMenu(self) -> None:
            self.set_calls += 1

        def hide(self) -> None:
            pass

        def deleteLater(self) -> None:
            pass

    class FakeQApplication:
        @staticmethod
        def instance():
            return object()

    qt_widgets = types.ModuleType("PySide6.QtWidgets")
    qt_widgets.QApplication = FakeQApplication
    qt_widgets.QMenu = FakeQMenu
    qt_core = types.ModuleType("PySide6.QtCore")

    class FakeQTimer:
        @staticmethod
        def singleShot(_delay, callback) -> None:
            callback()

    qt_core.QTimer = FakeQTimer
    pyside = types.ModuleType("PySide6")
    pyside.QtWidgets = qt_widgets
    pyside.QtCore = qt_core
    monkeypatch.setitem(sys.modules, "PySide6", pyside)
    monkeypatch.setitem(sys.modules, "PySide6.QtWidgets", qt_widgets)
    monkeypatch.setitem(sys.modules, "PySide6.QtCore", qt_core)
    monkeypatch.setattr(
        macos_dock,
        "populate_qmenu",
        lambda _menu, _model, _context: None,
    )

    model = UnifiedMenuModel(
        pet_name="六毛",
        state_provider=lambda: {"visible": True},
        callbacks={"chat": lambda _checked=False: None},
    )
    controller = macos_dock.install_dock_menu(model)
    assert controller._qt_menu.set_calls == 3
    controller.close()


def test_dock_menu_reasserts_appkit_delegate_when_qt_bridge_is_missing(monkeypatch) -> None:
    """The packaged fallback keeps its delegate after Qt changes it."""

    monkeypatch.setattr(macos_dock.sys, "platform", "darwin")

    class FakeQObject:
        @classmethod
        def alloc(cls):
            return cls()

        def init(self):
            return self

    class FakeApplication:
        def __init__(self) -> None:
            self.current_delegate = object()
            self.set_calls = 0

        def delegate(self):
            return self.current_delegate

        def setDelegate_(self, delegate) -> None:
            self.current_delegate = delegate
            self.set_calls += 1

    fake_application = FakeApplication()

    class FakeNSApplication:
        @staticmethod
        def sharedApplication():
            return fake_application

    class FakeNSMenu:
        @classmethod
        def alloc(cls):
            return cls()

        def initWithTitle_(self, _title):
            return self

    class FakeNSMenuItem:
        pass

    class FakeQMenu:
        def __init__(self) -> None:
            self.aboutToShow = type("Signal", (), {"connect": lambda *_args: None})()

        def clear(self) -> None:
            pass

    class FakeQApplication:
        @staticmethod
        def instance():
            return object()

    class FakeQTimer:
        calls = 0

        @staticmethod
        def singleShot(_delay, callback) -> None:
            FakeQTimer.calls += 1
            if FakeQTimer.calls == 2:
                fake_application.current_delegate = object()
            callback()

    qt_widgets = types.ModuleType("PySide6.QtWidgets")
    qt_widgets.QApplication = FakeQApplication
    qt_widgets.QMenu = FakeQMenu
    qt_core = types.ModuleType("PySide6.QtCore")
    qt_core.QTimer = FakeQTimer
    pyside = types.ModuleType("PySide6")
    pyside.QtWidgets = qt_widgets
    pyside.QtCore = qt_core
    appkit = types.ModuleType("AppKit")
    appkit.NSApplication = FakeNSApplication
    appkit.NSMenu = FakeNSMenu
    appkit.NSMenuItem = FakeNSMenuItem
    foundation = types.ModuleType("Foundation")
    foundation.NSObject = FakeQObject
    monkeypatch.setitem(sys.modules, "PySide6", pyside)
    monkeypatch.setitem(sys.modules, "PySide6.QtWidgets", qt_widgets)
    monkeypatch.setitem(sys.modules, "PySide6.QtCore", qt_core)
    monkeypatch.setitem(sys.modules, "AppKit", appkit)
    monkeypatch.setitem(sys.modules, "Foundation", foundation)
    monkeypatch.setattr(macos_dock, "_DOCK_TARGET_CLASS", None)
    monkeypatch.setattr(macos_dock, "_DOCK_DELEGATE_CLASS", None)
    monkeypatch.setattr(
        macos_dock,
        "populate_qmenu",
        lambda _menu, _model, _context: None,
    )

    model = UnifiedMenuModel(
        pet_name="六毛",
        state_provider=lambda: {"visible": True},
        callbacks={"chat": lambda _checked=False: None},
    )
    controller = macos_dock.install_dock_menu(model)
    assert controller.installed is True
    assert fake_application.set_calls == 2
    controller.close()


def test_macos_uses_native_status_item_for_tray(monkeypatch) -> None:
    monkeypatch.setattr(application.sys, "platform", "darwin")
    assert application._uses_qt_system_tray() is False

    monkeypatch.setattr(application.sys, "platform", "win32")
    assert application._uses_qt_system_tray() is True
