"""桌宠原生窗口层级的低频、非激活平台桥。

Qt flags 是唯一的窗口策略来源；本模块只在 Show、WinIdChange、屏幕、
应用生命周期节点或低频 watchdog 中校验已经存在的 native handle。Windows 使用
``SetWindowPos`` 的 ``NOACTIVATE`` 方式，macOS 使用 PyObjC 包装 Qt
创建的 NSWindow，并用正确的无参数 ``orderFrontRegardless`` 选择器恢复浮动层级；
不通过裸 Objective-C ABI 调用、不接管 Cocoa delegate，也不抢占焦点。
"""

from __future__ import annotations

import os
import sys
from typing import Any


def _base_result(native_id: int, qt_stays_on_top: bool) -> dict[str, Any]:
    return {
        "native_id": int(native_id),
        "qt_stays_on_top": bool(qt_stays_on_top),
        "native_level": None,
        "native_topmost": None,
        "action": "verify",
        "available": True,
    }


def _is_headless_qt_backend() -> bool:
    """识别没有 Cocoa NSWindow 的 Qt 离屏后端，避免误把句柄当作 NSView。"""

    try:
        from PySide6.QtGui import QGuiApplication

        application = QGuiApplication.instance()
        if application is None:
            return False
        return str(application.platformName()).lower() in {
            "offscreen",
            "minimal",
            "minimalegl",
        }
    except Exception:
        return False


def apply_windows_window_policy(
    widget: object,
    *,
    topmost: bool,
    qt_stays_on_top: bool,
    force_topmost: bool = False,
) -> dict[str, Any]:
    """在不移动、不改变焦点的前提下校验一个 HWND 的 topmost 状态。"""

    try:
        import ctypes

        user32 = ctypes.windll.user32
        native_id = int(widget.winId())  # type: ignore[attr-defined]
        result = _base_result(native_id, qt_stays_on_top)
        if native_id <= 0:
            result.update({"available": False, "action": "handle_unavailable"})
            return result

        get_style = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
        set_style = getattr(user32, "SetWindowLongPtrW", user32.SetWindowLongW)
        extended = int(get_style(native_id, -20))
        if extended == -1:
            result.update({"available": False, "action": "query_failed"})
            return result

        # WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE. These are compatible with
        # Qt::Tool and WindowDoesNotAcceptFocus and do not activate the HWND.
        desired_extended = extended | 0x00000080 | 0x08000000
        if desired_extended != extended:
            set_style(native_id, -20, desired_extended)

        hwnd_topmost = bool(extended & 0x00000008)  # WS_EX_TOPMOST
        result["native_topmost"] = hwnd_topmost
        # Some applications (notably remote-control clients and Office) can
        # reorder an HWND without changing WS_EX_TOPMOST.  The style bit alone
        # then says "topmost" while the user can still see another normal
        # window in front of the pet.  A low-frequency watchdog may explicitly
        # reassert the level.  It deliberately keeps SWP_NOACTIVATE, so this
        # cannot steal keyboard focus from the foreground app.
        should_reassert_topmost = bool(topmost and force_topmost)
        if hwnd_topmost != bool(topmost) or should_reassert_topmost:
            insert_after = -1 if topmost else -2  # HWND_TOPMOST/NOTOPMOST
            flags = 0x0001 | 0x0002 | 0x0010 | 0x0200  # NOMOVE/NOSIZE/NOACTIVATE/NOOWNERZORDER
            if not bool(user32.SetWindowPos(native_id, insert_after, 0, 0, 0, 0, flags)):
                result.update({"available": False, "action": "restore_failed"})
                return result
            refreshed = int(get_style(native_id, -20))
            result["native_topmost"] = bool(refreshed & 0x00000008)
            if topmost:
                result["action"] = (
                    "reassert_topmost" if should_reassert_topmost else "restore_topmost"
                )
            else:
                result["action"] = "restore_normal_level"
        elif desired_extended != extended:
            # The style repair itself is enough; SetWindowPos is not needed.
            result["action"] = "restore_nonactivating_style"
        return result
    except Exception as exc:
        return {
            "native_id": 0,
            "qt_stays_on_top": bool(qt_stays_on_top),
            "native_level": None,
            "native_topmost": None,
            "action": "native_policy_error",
            "available": False,
            "error": str(exc),
        }


def apply_macos_window_policy(
    widget: object,
    *,
    topmost: bool,
    qt_stays_on_top: bool,
    force_topmost: bool = False,
) -> dict[str, Any]:
    """用 PyObjC 设置浮动层级，并明确拒绝加入全屏 Space。"""

    try:
        # macOS CI 使用 Qt offscreen/minimal 后端；此时 winId() 不是可供
        # Cocoa 包装的 NSView 指针。真实桌面后端不经过此分支。
        if _is_headless_qt_backend():
            return {
                "native_id": 0,
                "qt_stays_on_top": bool(qt_stays_on_top),
                "native_level": None,
                "native_topmost": None,
                "action": "headless_qt_backend",
                "available": True,
            }

        import ctypes
        import objc
        from AppKit import (
            NSFloatingWindowLevel,
            NSNormalWindowLevel,
            NSWindowCollectionBehaviorFullScreenNone,
            NSWindowStyleMaskNonactivatingPanel,
        )

        native_id = int(widget.winId())  # type: ignore[attr-defined]
        result = _base_result(native_id, qt_stays_on_top)
        if native_id <= 0:
            result.update({"available": False, "action": "handle_unavailable"})
            return result

        # This is a supported PyObjC pointer conversion, not a raw
        # Objective-C ABI call. PyObjC dispatches the following messages with
        # the framework's registered method signatures.
        view = objc.objc_object(c_void_p=ctypes.c_void_p(native_id))
        window = view.window()
        if window is None:
            result.update({"available": False, "action": "window_unavailable"})
            return result

        desired_level = int(NSFloatingWindowLevel if topmost else NSNormalWindowLevel)
        current_level = int(window.level())
        desired_behavior = int(NSWindowCollectionBehaviorFullScreenNone)
        current_behavior = int(window.collectionBehavior())
        style_mask = int(window.styleMask())
        nonactivating_mask = int(NSWindowStyleMaskNonactivatingPanel)
        desired_style_mask = style_mask | nonactivating_mask

        changed_level = current_level != desired_level
        changed_behavior = current_behavior != desired_behavior
        changed_style = style_mask != desired_style_mask
        if changed_level:
            window.setLevel_(desired_level)
        if changed_behavior:
            # Do not opt into full-screen auxiliary or all-spaces behavior:
            # full-screen media/presentations remain owned by the system.
            window.setCollectionBehavior_(desired_behavior)
        if changed_style:
            window.setStyleMask_(desired_style_mask)
        # A different app can reorder a floating NSWindow without changing its
        # numeric level.  Reassert ordering only from the low-frequency
        # watchdog or app-deactivation repair. orderFrontRegardless() orders
        # the panel without making it key, so the foreground app keeps focus.
        should_reassert_topmost = bool(topmost and force_topmost)
        if should_reassert_topmost:
            # PyObjC replaces Objective-C selector colons with underscores.
            # This selector has no arguments, so the real Python name has no
            # trailing underscore. Treat a missing bridge method as failure
            # instead of logging a false successful reassertion.
            order_front_regardless = getattr(window, "orderFrontRegardless", None)
            if not callable(order_front_regardless):
                result.update(
                    {
                        "available": False,
                        "action": "reassert_selector_unavailable",
                    }
                )
                return result
            order_front_regardless()
        hides_on_deactivate = getattr(window, "setHidesOnDeactivate_", None)
        if callable(hides_on_deactivate):
            hides_on_deactivate(False)
        becomes_key_only = getattr(window, "setBecomesKeyOnlyIfNeeded_", None)
        if callable(becomes_key_only):
            becomes_key_only(True)

        result["native_level"] = int(window.level())
        result["native_topmost"] = bool(
            topmost and result["native_level"] == desired_level
        )
        if should_reassert_topmost:
            result["action"] = "reassert_topmost"
        elif changed_level or changed_behavior or changed_style:
            result["action"] = "restore_topmost" if topmost else "restore_normal_level"
        return result
    except Exception as exc:
        return {
            "native_id": 0,
            "qt_stays_on_top": bool(qt_stays_on_top),
            "native_level": None,
            "native_topmost": None,
            "action": "native_policy_unavailable",
            "available": False,
            "error": str(exc),
        }


def apply_native_window_policy(
    widget: object,
    *,
    topmost: bool,
    qt_stays_on_top: bool,
    force_topmost: bool = False,
) -> dict[str, Any]:
    """按当前平台调用唯一 native 层级入口。"""

    if os.name == "nt":
        return apply_windows_window_policy(
            widget,
            topmost=topmost,
            qt_stays_on_top=qt_stays_on_top,
            force_topmost=force_topmost,
        )
    if sys.platform == "darwin":
        return apply_macos_window_policy(
            widget,
            topmost=topmost,
            qt_stays_on_top=qt_stays_on_top,
            force_topmost=force_topmost,
        )
    return {
        "native_id": 0,
        "qt_stays_on_top": bool(qt_stays_on_top),
        "native_level": None,
        "native_topmost": None,
        "action": "qt_only",
        "available": True,
    }
