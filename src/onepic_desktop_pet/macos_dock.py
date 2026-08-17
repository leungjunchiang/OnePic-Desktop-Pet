"""Optional native macOS Dock menu backed by the shared menu model."""

from __future__ import annotations

import sys
from collections.abc import Callable

from .menu_model import MenuItemSpec, UnifiedMenuModel


class MacDockMenuController:
    """Install ``applicationDockMenu:`` without affecting Windows startup."""

    def __init__(self, model_provider: Callable[[], UnifiedMenuModel]) -> None:
        self._model_provider = model_provider
        self._application = None
        self._previous_delegate = None
        self._delegate = None
        self._native = False

        if sys.platform != "darwin":
            return
        try:
            import objc
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

        class _DockDelegate(NSObject):
            def initWithProvider_previousDelegate_(self, provider, previous_delegate):
                self = objc.super(_DockDelegate, self).init()
                if self is not None:
                    self.provider = provider
                    self.previous_delegate = previous_delegate
                return self

            def applicationDockMenu_(self, _application):
                return controller._build_native_menu(NSMenu, NSMenuItem, _DockTarget)

            def forwardingTargetForSelector_(self, selector):
                previous = getattr(self, "previous_delegate", None)
                if previous is not None:
                    return previous
                return objc.super(_DockDelegate, self).forwardingTargetForSelector_(selector)

        self._application = NSApplication.sharedApplication()
        self._previous_delegate = self._application.delegate()
        self._target_class = _DockTarget
        self._delegate_class = _DockDelegate
        self._delegate = _DockDelegate.alloc().initWithProvider_previousDelegate_(
            self, self._previous_delegate
        )
        # Qt does not expose applicationDockMenu:, so a small retained Cocoa
        # delegate is required.  The controller keeps it alive for the app.
        self._application.setDelegate_(self._delegate)
        self._native = True

    @property
    def installed(self) -> bool:
        return self._native

    def _build_native_menu(self, NSMenu, NSMenuItem, target_class):
        target = target_class.alloc().init()

        def render(menu, items: tuple[MenuItemSpec, ...]) -> None:
            for spec in items:
                if spec.separator:
                    menu.addItem_(NSMenuItem.separatorItem())
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
                    render(submenu, spec.children)
                    item.setSubmenu_(submenu)
                menu.addItem_(item)

        menu = NSMenu.alloc().initWithTitle_("六毛")
        render(menu, self._model_provider().items("dock"))
        return menu

    def close(self) -> None:
        """Restore the prior Cocoa delegate when the Qt app is quitting."""

        if self._native and self._application is not None:
            self._application.setDelegate_(self._previous_delegate)
            self._native = False


def install_dock_menu(model_provider: Callable[[], UnifiedMenuModel]) -> MacDockMenuController:
    """Create an installed controller on macOS and a harmless no-op elsewhere."""

    return MacDockMenuController(model_provider)

