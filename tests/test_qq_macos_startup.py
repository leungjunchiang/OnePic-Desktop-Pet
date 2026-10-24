"""验证 Mac 普通登录字样、隐藏分支与后台窗口不误判，真实认证仍禁止搜索。"""
from types import SimpleNamespace
import pytest

from onepic_desktop_pet.qqmusic_macos import AXNode, NativeAX, QQMusicMacBridge, startup_state, search_fields
from onepic_desktop_pet.music_playback import SongCandidate
from test_qqmusic_macos import Backend, region, static
from test_qq_macos_recovery import app_bundle


def ready(*nodes):
    return AXNode('AXWindow', children=[AXNode('AXSearchField', '搜索'), region(), *nodes])


def result_row():
    return AXNode('AXRow', children=[static('光'), static('陈楚生'), AXNode('AXButton', '播放歌曲')])


def test_generic_login_text_with_complete_player_is_not_account_logout():
    assert startup_state(ready(static('登录'))) == 'ready'
    assert startup_state(AXNode('AXWindow', children=[static('登录')])) == 'login-required'


@pytest.mark.parametrize('label', ['点击登录', '安全验证', '请先登录'])
def test_explicit_login_markers_still_block_a_cached_player(label):
    assert startup_state(ready(static(label))) == 'login-required'


def test_real_login_button_or_login_window_still_blocks():
    assert startup_state(ready(AXNode('AXButton', '登录'))) == 'login-required'
    tree = ready()
    tree.text = '登录'
    assert startup_state(tree) == 'login-required'


def test_hidden_old_login_tree_and_duplicate_search_do_not_block():
    hidden = AXNode('AXGroup', hidden=True, children=[static('点击登录'), AXNode('AXSearchField', '搜索')])
    tree = ready(hidden)
    assert startup_state(tree) == 'ready' and len(search_fields(tree)) == 1


def test_background_window_still_exposes_player_without_activation():
    tree = ready()
    tree.hidden = True
    assert startup_state(tree) == 'ready'


def test_native_ax_hidden_branch_is_pruned_without_reading_children():
    root = {'AXRole': 'AXApplication', 'AXWindows': [{'AXRole': 'AXWindow', 'AXChildren': [
        {'AXRole': 'AXGroup', 'AXHidden': True, 'AXChildren': 'must not be read'},
        {'AXRole': 'AXSearchField', 'AXTitle': '搜索'}]}]}
    backend = NativeAX.__new__(NativeAX)
    backend.cancelled = lambda: False
    def attribute(element, name, *_):
        if element.get('AXHidden') is True and name == 'AXChildren':
            pytest.fail('Hidden branch must not be queried')
        return 0, element.get(name)
    backend.ax = SimpleNamespace(
        AXUIElementCopyMultipleAttributeValues=lambda element, names, *_: (0, [element.get(name) for name in names]),
        AXUIElementCopyAttributeValue=attribute)
    tree = backend.tree(root)
    assert len(search_fields(tree)) == 1 and startup_state(tree) == 'ready'


def test_generic_text_does_not_force_login_actions_before_exact_search(tmp_path):
    backend = Backend(ready(static('登录'), result_row()))
    now = [0.0]
    bridge = QQMusicMacBridge(backend=backend, clock=lambda: now[0], sleep=lambda n: now.__setitem__(0, now[0]+n))
    bridge.play_candidate(app_bundle(tmp_path), SongCandidate('qq', '光', '陈楚生', identifier='1'))
    assert backend.actions == [('search', '陈楚生 光'), ('搜索', 'AXConfirm'), ('播放歌曲', 'AXPress')]
    assert not any(action[0] == '登录' for action in backend.actions)


def test_one_transient_login_observation_can_recover_without_login_actions(tmp_path):
    trees = iter([ready(static('点击登录')), ready(result_row())])
    backend = Backend(ready(result_row()))
    backend.tree = lambda _: next(trees, backend.root)
    now = [0.0]
    bridge = QQMusicMacBridge(backend=backend, clock=lambda: now[0], sleep=lambda n: now.__setitem__(0, now[0]+n))
    bridge.play_candidate(app_bundle(tmp_path), SongCandidate('qq', '光', '陈楚生', identifier='1'))
    assert backend.actions[0] == ('search', '陈楚生 光') and len(backend.actions) == 3
