"""桌宠原生窗口层级的低频、非激活平台桥。

Qt flags 是唯一的窗口策略来源；本模块只在 Show、WinIdChange、屏幕或
应用生命周期节点校验已经存在的 native handle。Windows 使用
``SetWindowPos`` 的 ``NOACTIVATE`` 方式，macOS 使用 PyObjC 包装 Qt
创建的 NSWindow；不通过裸 Objective-C ABI 调用、不接管 Cocoa delegate，也不抢占焦点。
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


def apply_windows_window_policy(
    widget: object,
    *,
    topmost: bool,
    qt_stays_on_top: bool,
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
        if hwnd_topmost != bool(topmost):
            insert_after = -1 if topmost else -2  # HWND_TOPMOST/NOTOPMOST
            flags = 0x0001 | 0x0002 | 0x0010 | 0x0200  # NOMOVE/NOSIZE/NOACTIVATE/NOOWNERZORDER
            if not bool(user32.SetWindowPos(native_id, insert_after, 0, 0, 0, 0, flags)):
                result.update({"available": False, "action": "restore_failed"})
                return result
            refreshed = int(get_style(native_id, -20))
            result["native_topmost"] = bool(refreshed & 0x00000008)
            result["action"] = "restore_topmost" if topmost else "restore_normal_level"
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
) -> dict[str, Any]:
    """用 PyObjC 设置浮动层级，并明确拒绝加入全屏 Space。"""

    try:
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
        if changed_level or changed_behavior or changed_style:
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
) -> dict[str, Any]:
    """按当前平台调用唯一 native 层级入口。"""

    if os.name == "nt":
        return apply_windows_window_policy(
            widget,
            topmost=topmost,
            qt_stays_on_top=qt_stays_on_top,
        )
    if sys.platform == "darwin":
        return apply_macos_window_policy(
            widget,
            topmost=topmost,
            qt_stays_on_top=qt_stays_on_top,
        )
    return {
        "native_id": 0,
        "qt_stays_on_top": bool(qt_stays_on_top),
        "native_level": None,
        "native_topmost": None,
        "action": "qt_only",
        "available": True,
    }
