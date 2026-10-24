"""验证就绪检查忽略展开窗口的隐藏登录节点，保留后台检测且不输出账号或搜索值。"""

from contextlib import nullcontext
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from onepic_desktop_pet import qqmusic_startup
from onepic_desktop_pet import music_background
from onepic_desktop_pet.qqmusic_queue_mode import _WindowsQQModeNativeBackend


def backend(monkeypatch, rows, *, background=False, title='PRIVATE-ACCOUNT-NAME'):
    kinds = SimpleNamespace(EditControl=1, ComboBoxControl=2, TextControl=3, ButtonControl=4)
    elements = [SimpleNamespace(CachedControlType=getattr(kinds, role), CachedName=name,
                                CachedIsOffscreen=hidden) for role, name, hidden in rows]
    array = SimpleNamespace(Length=len(elements), GetElement=elements.__getitem__)
    # Some QQ builds claim the minimized root is onscreen while all children are offscreen.
    root = SimpleNamespace(Name=title, IsOffscreen=False, NativeWindowHandle=123,
        Element=SimpleNamespace(FindAllBuildCache=lambda *_: array))
    monkeypatch.setattr(music_background, 'music_window_is_background', lambda hwnd: background if hwnd == 123 else False)
    api = SimpleNamespace(CreatePropertyCondition=lambda *_: None, CreateOrCondition=lambda *_: None,
        CreateCacheRequest=lambda: SimpleNamespace(AddProperty=lambda *_: None))
    auto = SimpleNamespace(UIAutomationInitializerInThread=nullcontext, ControlType=kinds,
        PropertyId=SimpleNamespace(ControlTypeProperty=1, NameProperty=2, IsOffscreenProperty=3))
    monkeypatch.setitem(sys.modules, 'uiautomation', auto)
    monkeypatch.setitem(sys.modules, 'uiautomation.uiautomation', SimpleNamespace(
        _AutomationClient=SimpleNamespace(instance=lambda: SimpleNamespace(IUIAutomation=api))))
    value = _WindowsQQModeNativeBackend(lambda: Path('QQMusic.exe'))
    value._roots = lambda _: [root]
    return value


def bar(hidden=False):
    return [('ButtonControl', name, hidden) for name in ('上一曲', '下一曲', '暂停')]


def test_hidden_login_panel_does_not_override_visible_player(monkeypatch):
    value = backend(monkeypatch, bar() + [('TextControl', '点击登录', True)])
    assert value.startup_state() == 'ready'


@pytest.mark.parametrize('name', ['点击登录', '安全验证', '正在登录'])
def test_visible_real_login_or_verification_still_blocks_even_with_player(monkeypatch, name):
    value = backend(monkeypatch, bar() + [('TextControl', name, False)])
    assert value.startup_state() == ('signing-in' if name == '正在登录' else 'login-required')


def test_background_player_remains_readable_without_restoring_or_activating_it(monkeypatch):
    assert backend(monkeypatch, bar(hidden=True), background=True).startup_state() == 'ready'
    assert backend(monkeypatch, bar(hidden=True) + [('TextControl', '点击登录', True)],
                   background=True).startup_state() == 'login-required'


def test_authentication_window_caption_still_blocks_cached_controls(monkeypatch):
    assert backend(monkeypatch, bar(), title='登录').startup_state() == 'login-required'


def test_combo_search_value_and_unrelated_root_caption_never_reach_classifier(monkeypatch):
    original, seen = qqmusic_startup.classify_startup, []
    def classify(controls):
        seen.extend(controls)
        return original(controls)
    monkeypatch.setattr(qqmusic_startup, 'classify_startup', classify)
    value = backend(monkeypatch, [('ButtonControl', '上一曲', False), ('ButtonControl', '下一曲', False),
                                 ('ComboBoxControl', 'PRIVATE-SEARCH-VALUE', False)])
    assert value.startup_state() == 'ready'
    assert ('ComboBoxControl', '') in seen and 'PRIVATE' not in repr(seen)


def test_excessive_partial_ui_tree_cannot_be_mistaken_for_ready(monkeypatch):
    assert backend(monkeypatch, bar() * 51).startup_state() == 'loading'


@pytest.mark.parametrize('minimized,visible,expected', [(True, True, True), (False, False, True), (False, True, False)])
def test_native_window_flags_detect_background_without_showing_or_hiding(monkeypatch, minimized, visible, expected):
    import ctypes
    class Call:
        def __init__(self, value): self.value = value
        def __call__(self, hwnd):
            assert hwnd == 123
            return self.value
    monkeypatch.setattr(music_background, 'sys', SimpleNamespace(platform='win32'))
    monkeypatch.setattr(ctypes, 'WinDLL', lambda *_a, **_k: SimpleNamespace(
        IsIconic=Call(minimized), IsWindowVisible=Call(visible)), raising=False)
    assert music_background.music_window_is_background(123) is expected
