"""Native macOS status-item and Dock-menu integration.

The menu-bar status item and the Dock icon both project the same
UnifiedMenuModel. This keeps the Windows tray, macOS Dock, macOS status
item, and pet context menu aligned while retaining native menu rendering.
"""

from __future__ import annotations

import sys
from collections.abc import Callable

from .menu_model import MenuItemSpec, UnifiedMenuModel, populate_qmenu
from .resources import resource_path


ModelSource = UnifiedMenuModel | Callable[[], UnifiedMenuModel]


_DOCK_TARGET_CLASS = None
_DOCK_DELEGATE_CLASS = None
_ACTIVE_DOCK_CONTROLLER = None


def _dock_native_classes():
    """Return process-wide PyObjC classes used by Dock menu controllers.

    PyObjC registers Python subclasses with the Objective-C runtime by class
    name. Defining the subclasses inside every controller instance therefore
    raises ``objc.error`` when a second controller is created in the same
    process (and is common in tests or during a Qt reinitialisation).
    """

    global _DOCK_TARGET_CLASS, _DOCK_DELEGATE_CLASS
    if _DOCK_TARGET_CLASS is not None and _DOCK_DELEGATE_CLASS is not None:
        return _DOCK_TARGET_CLASS, _DOCK_DELEGATE_CLASS

    from AppKit import NSMenu, NSMenuItem
    from Foundation import NSObject

    class DockTarget(NSObject):
        def triggerCommand_(self, item) -> None:
            # Keep the controller lookup outside the PyObjC instance.  Some
            # macOS/PyObjC combinations do not reliably retain arbitrary
            # Python attributes on Objective-C proxy objects, which could
            # make Dock actions silently fall back to Qt's default menu.
            controller = _ACTIVE_DOCK_CONTROLLER
            command = str(item.representedObject() or "")
            if controller is not None and command:
                controller._model_provider().execute(command, bool(item.state()))

    class DockApplicationDelegate(NSObject):
        def applicationDockMenu_(self, _application):
            controller = _ACTIVE_DOCK_CONTROLLER
            if controller is None:
                return None
            return controller._build_native_menu(NSMenu, NSMenuItem, DockTarget)

        def respondsToSelector_(self, selector) -> bool:
            controller = _ACTIVE_DOCK_CONTROLLER
            if selector in ("applicationDockMenu:", b"applicationDockMenu:"):
                return True
            previous = getattr(controller, "_previous_delegate", None)
            if previous is not None:
                try:
                    return bool(previous.respondsToSelector_(selector))
                except (AttributeError, TypeError, ValueError):
                    pass
            return False

        def forwardingTargetForSelector_(self, selector):
            """Keep Qt's existing delegate behavior for unrelated selectors."""
            if selector in ("applicationDockMenu:", b"applicationDockMenu:"):
                return self
            controller = _ACTIVE_DOCK_CONTROLLER
            return getattr(controller, "_previous_delegate", None)

    _DOCK_TARGET_CLASS = DockTarget
    _DOCK_DELEGATE_CLASS = DockApplicationDelegate
    return _DOCK_TARGET_CLASS, _DOCK_DELEGATE_CLASS


def _coerce_model_provider(source: ModelSource) -> Callable[[], UnifiedMenuModel]:
    if callable(source):
        return source
    return lambda: source


class MacDockMenuController:
    """Expose the unified Lili menu from the macOS Dock icon."""

    def __init__(self, model_provider: ModelSource) -> None:
        global _ACTIVE_DOCK_CONTROLLER
        self._model_provider = _coerce_model_provider(model_provider)
        self._application = None
        self._previous_delegate = None
        self._delegate = None
        self._target = None
        self._qt_menu = None
        self._native = False

        if sys.platform != "darwin":
            return

        # Qt owns the NSApplication delegate in a PySide application.  Using
        # QMenu's supported Dock bridge keeps that delegate intact; replacing
        # it with a second PyObjC delegate is racy because Qt may restore its
        # own delegate after startup, leaving the Dock with the default menu.
        if self._install_qt_dock_menu():
            self._schedule_dock_reassertion()
            return

        # Keep the AppKit implementation as a compatibility fallback for
        # environments where the Qt binding does not expose setAsDockMenu().
        # Normal PySide6 macOS builds take the Qt path above.
        try:
            from AppKit import NSApplication
        except ImportError:
            return

        self._target_class, self._delegate_class = _dock_native_classes()
        self._application = NSApplication.sharedApplication()
        self._previous_delegate = self._application.delegate()
        self._target = self._target_class.alloc().init()
        self._delegate = self._delegate_class.alloc().init()
        _ACTIVE_DOCK_CONTROLLER = self
        self._application.setDelegate_(self._delegate)
        self._native = True
        # Qt may finish installing/restoring its Cocoa delegate during the
        # first event-loop turn.  Re-assert our delegate after Qt is settled;
        # otherwise macOS silently falls back to the standard Dock menu.
        self._schedule_dock_reassertion()

    def _schedule_dock_reassertion(self) -> None:
        """Re-assert the Dock bridge after Qt finishes Cocoa setup."""

        try:
            from PySide6.QtCore import QTimer
        except ImportError:
            return
        # Run once immediately after construction and once after native
        # application objects have had a chance to settle.  The callbacks are
        # harmless after close() because ensure_installed() checks _native.
        QTimer.singleShot(0, self.ensure_installed)
        QTimer.singleShot(500, self.ensure_installed)

    def ensure_installed(self) -> None:
        """Keep the actual macOS Dock hook installed after Qt startup."""

        if not self._native:
            return
        if self._qt_menu is not None:
            try:
                self._refresh_qt_dock_menu()
                set_as_dock_menu = getattr(self._qt_menu, "setAsDockMenu", None)
                if callable(set_as_dock_menu):
                    set_as_dock_menu()
            except (AttributeError, RuntimeError, TypeError, ValueError):
                pass
            return
        if self._application is None or self._delegate is None:
            return
        try:
            if self._application.delegate() is not self._delegate:
                global _ACTIVE_DOCK_CONTROLLER
                _ACTIVE_DOCK_CONTROLLER = self
                self._application.setDelegate_(self._delegate)
        except (AttributeError, RuntimeError, TypeError, ValueError):
            pass

    def _install_qt_dock_menu(self) -> bool:
        """Install the Dock menu through Qt's native Cocoa bridge.

        ``QMenu.setAsDockMenu()`` registers the menu with Qt's existing
        ``NSApplicationDelegate``.  This is the important distinction from
        replacing the delegate ourselves: Qt keeps all of its normal Cocoa
        lifecycle callbacks while the Dock receives this exact QMenu.
        """

        try:
            from PySide6.QtWidgets import QApplication, QMenu
        except ImportError:
            return False

        # QMenu must be created after QApplication.  Unit tests and library
        # imports can inspect this controller before Qt has been started; in
        # that case leave installation to the AppKit fallback (or no-op)
        # instead of letting Qt abort the process with a fatal constructor
        # error.
        if QApplication.instance() is None:
            return False

        try:
            menu = QMenu()
            self._qt_menu = menu
            self._refresh_qt_dock_menu()
            set_as_dock_menu = getattr(menu, "setAsDockMenu", None)
            if not callable(set_as_dock_menu):
                self._qt_menu = None
                return False
            set_as_dock_menu()
            about_to_show = getattr(menu, "aboutToShow", None)
            connect = getattr(about_to_show, "connect", None)
            if callable(connect):
                connect(self._refresh_qt_dock_menu)
        except (AttributeError, RuntimeError, TypeError, ValueError):
            self._qt_menu = None
            return False

        self._native = True
        return True

    def _refresh_qt_dock_menu(self) -> None:
        """Refresh the QMenu before the Dock opens it."""

        if self._qt_menu is None:
            return
        self._qt_menu.clear()
        # The pet projection is the canonical right-click menu.  The same
        # model object is used by the pet window and by the status item.
        populate_qmenu(self._qt_menu, self._model_provider(), "pet")

    @property
    def installed(self) -> bool:
        return self._native

    def _build_native_menu(self, NSMenu, NSMenuItem, target_class):
        menu = NSMenu.alloc().initWithTitle_("六毛")
        target = self._target or target_class.alloc().init()

        def render(items: tuple[MenuItemSpec, ...], destination) -> None:
            for spec in items:
                if spec.separator:
                    destination.addItem_(NSMenuItem.separatorItem())
                    continue
                item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                    spec.title,
                    "triggerCommand:",
                    "",
                )
                item.setTarget_(target)
                if spec.command:
                    item.setRepresentedObject_(spec.command)
                item.setEnabled_(spec.enabled)
                if spec.checkable:
                    item.setState_(1 if spec.checked else 0)
                if spec.children:
                    submenu = NSMenu.alloc().initWithTitle_(spec.title)
                    render(spec.children, submenu)
                    item.setSubmenu_(submenu)
                destination.addItem_(item)

        # The pet context menu is the canonical menu. Keep the Dock
        # projection on that exact context so a future platform-specific
        # branch cannot silently make the Dock right-click list diverge from
        # the menu users see on 六毛 itself.
        # The pet context is the canonical user-facing menu.  Render that
        # exact projection for Dock instead of relying on a platform context
        # name that could later grow divergent entries.
        render(self._model_provider().items("pet"), menu)
        return menu

    def close(self) -> None:
        """Restore the previous application delegate during shutdown."""

        global _ACTIVE_DOCK_CONTROLLER
        if self._qt_menu is not None:
            try:
                self._qt_menu.hide()
                delete_later = getattr(self._qt_menu, "deleteLater", None)
                if callable(delete_later):
                    delete_later()
            except (AttributeError, RuntimeError, TypeError, ValueError):
                pass
            self._qt_menu = None
        if self._native and self._application is not None:
            try:
                if self._application.delegate() is self._delegate:
                    self._application.setDelegate_(self._previous_delegate)
            except (AttributeError, TypeError, ValueError):
                pass
        if _ACTIVE_DOCK_CONTROLLER is self:
            _ACTIVE_DOCK_CONTROLLER = None
        self._application = None
        self._previous_delegate = None
        self._delegate = None
        self._target = None
        self._native = False


def install_dock_menu(model_provider: ModelSource) -> MacDockMenuController:
    """Install a native Dock menu on macOS, or a no-op elsewhere."""

    return MacDockMenuController(model_provider)


class MacStatusBarController:
    """Provide the same unified commands from a native macOS status item.

    The status item is deliberately only a projection of UnifiedMenuModel.
    It does not own update, visibility, Todo, or settings logic; all actions
    are dispatched through the same command callbacks used by the pet window
    and Windows tray.
    """

    def __init__(self, model_provider: ModelSource) -> None:
        self._model_provider = _coerce_model_provider(model_provider)
        self._status_bar = None
        self._status_item = None
        self._menu = None
        self._menu_delegate = None
        self._target = None
        self._native = False

        if sys.platform != "darwin":
            return
        try:
            import objc
            from AppKit import (
                NSImage,
                NSMenu,
                NSMenuItem,
                NSStatusBar,
                NSVariableStatusItemLength,
            )
            from Foundation import NSObject
        except ImportError:
            return

        controller = self

        class _StatusTarget(NSObject):
            def triggerCommand_(self, item) -> None:
                command = str(item.representedObject() or "")
                if command:
                    controller._model_provider().execute(command, bool(item.state()))

        class _StatusMenuDelegate(NSObject):
            def menuNeedsUpdate_(self, menu) -> None:
                controller._populate_native_menu(menu, NSMenu, NSMenuItem, _StatusTarget)

        self._target_class = _StatusTarget
        self._menu_delegate_class = _StatusMenuDelegate
        self._status_bar = NSStatusBar.systemStatusBar()
        self._status_item = self._status_bar.statusItemWithLength_(NSVariableStatusItemLength)
        button = self._status_item.button()
        if button is not None:
            button.setToolTip_("六毛")
            try:
                image = NSImage.alloc().initWithContentsOfFile_(
                    str(resource_path("assets/icons/pet.png"))
                )
                if image is not None:
                    image.setSize_((18, 18))
                    button.setImage_(image)
            except (AttributeError, OSError, TypeError, ValueError):
                # The status item remains useful with the default empty button
                # if an optional packaged icon cannot be loaded.
                pass

        self._target = _StatusTarget.alloc().init()
        self._menu = NSMenu.alloc().initWithTitle_("六毛")
        self._menu_delegate = _StatusMenuDelegate.alloc().init()
        self._menu.setDelegate_(self._menu_delegate)
        self._status_item.setMenu_(self._menu)
        self._populate_native_menu(self._menu, NSMenu, NSMenuItem, _StatusTarget)
        self._native = True

    @property
    def installed(self) -> bool:
        return self._native

    def _populate_native_menu(self, menu, NSMenu, NSMenuItem, target_class) -> None:
        """Rebuild dynamic labels/states immediately before the menu opens."""

        if menu is None:
            return
        menu.removeAllItems()
        target = self._target or target_class.alloc().init()

        def render(items: tuple[MenuItemSpec, ...], destination) -> None:
            for spec in items:
                if spec.separator:
                    destination.addItem_(NSMenuItem.separatorItem())
                    continue
                item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                    spec.title,
                    "triggerCommand:",
                    "",
                )
                item.setTarget_(target)
                if spec.command:
                    item.setRepresentedObject_(spec.command)
                item.setEnabled_(spec.enabled)
                if spec.checkable:
                    item.setState_(1 if spec.checked else 0)
                if spec.children:
                    submenu = NSMenu.alloc().initWithTitle_(spec.title)
                    render(spec.children, submenu)
                    item.setSubmenu_(submenu)
                destination.addItem_(item)

        # Keep the menu-bar item on the same canonical projection as the pet
        # and Dock.  It is native-rendered, but its commands and ordering must
        # not drift by context.
        render(self._model_provider().items("pet"), menu)

    def close(self) -> None:
        """Remove the native status item during application shutdown."""

        if self._native and self._status_bar is not None and self._status_item is not None:
            self._status_bar.removeStatusItem_(self._status_item)
        self._status_item = None
        self._menu = None
        self._menu_delegate = None
        self._target = None
        self._native = False


def install_status_item(model_provider: ModelSource) -> MacStatusBarController:
    """Create a native macOS menu-bar entry, or a no-op elsewhere."""

    return MacStatusBarController(model_provider)
