"""Native macOS status-item and Dock-menu integration.

The menu-bar status item and the Dock icon both project the same
UnifiedMenuModel. This keeps the Windows tray, macOS Dock, macOS status
item, and pet context menu aligned while retaining native menu rendering.
"""

from __future__ import annotations

import sys
from collections.abc import Callable

from .menu_model import MenuItemSpec, UnifiedMenuModel
from .resources import resource_path


ModelSource = UnifiedMenuModel | Callable[[], UnifiedMenuModel]


def _coerce_model_provider(source: ModelSource) -> Callable[[], UnifiedMenuModel]:
    if callable(source):
        return source
    return lambda: source


class MacDockMenuController:
    """Expose the unified Lili menu from the macOS Dock icon."""

    def __init__(self, model_provider: ModelSource) -> None:
        self._model_provider = _coerce_model_provider(model_provider)
        self._application = None
        self._previous_delegate = None
        self._delegate = None
        self._target = None
        self._native = False

        if sys.platform != "darwin":
            return
        try:
            from AppKit import NSApplication, NSMenu, NSMenuItem
            from Foundation import NSObject
        except ImportError:
            return

        controller = self

        class _DockTarget(NSObject):
            def triggerCommand_(self, item) -> None:
                command = str(item.representedObject() or "")
                if command:
                    controller._model_provider().execute(command, bool(item.state()))

        class _DockApplicationDelegate(NSObject):
            def applicationDockMenu_(self, _application):
                return controller._build_native_menu(NSMenu, NSMenuItem, _DockTarget)

            def respondsToSelector_(self, selector) -> bool:
                if selector in ("applicationDockMenu:", b"applicationDockMenu:"):
                    return True
                previous = controller._previous_delegate
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
                return controller._previous_delegate

        self._target_class = _DockTarget
        self._delegate_class = _DockApplicationDelegate
        self._application = NSApplication.sharedApplication()
        self._previous_delegate = self._application.delegate()
        self._target = _DockTarget.alloc().init()
        self._delegate = _DockApplicationDelegate.alloc().init()
        self._application.setDelegate_(self._delegate)
        self._native = True

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

        render(self._model_provider().items("dock"), menu)
        return menu

    def close(self) -> None:
        """Restore the previous application delegate during shutdown."""

        if self._native and self._application is not None:
            try:
                if self._application.delegate() is self._delegate:
                    self._application.setDelegate_(self._previous_delegate)
            except (AttributeError, TypeError, ValueError):
                pass
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

        render(self._model_provider().items("macos"), menu)

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
