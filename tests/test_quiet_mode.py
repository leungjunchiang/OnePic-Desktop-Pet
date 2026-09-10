from onepic_desktop_pet.quiet_mode import detect_quiet_mode


def test_quiet_mode_suppresses_meetings_presentations_and_games() -> None:
    assert detect_quiet_mode("Teams.exe", fullscreen=False).reason == "会议中"
    assert detect_quiet_mode("powerpnt.exe", fullscreen=False).blocked
    assert detect_quiet_mode("Steam.exe", fullscreen=False).reason == "游戏中"
    assert not detect_quiet_mode("code.exe", fullscreen=False).blocked


def test_quiet_mode_can_use_fullscreen_geometry_signal() -> None:
    snapshot = detect_quiet_mode("unknown.exe", fullscreen=True)
    assert snapshot.blocked
    assert snapshot.reason == "全屏工作中"
