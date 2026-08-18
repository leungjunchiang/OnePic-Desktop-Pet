"""
本模块测试桌面宠物配置的合并、范围校验、损坏回退和原子保存行为。

测试只使用 pytest 提供的临时目录，不写入真实用户配置目录，不修改项目默认配置，
也不创建 GUI 或网络请求。
"""

import json

from onepic_desktop_pet.config import (
    PET_NAME,
    PetSettings,
    social_pet_label,
    load_settings,
    save_settings,
    user_settings_path,
)


def test_social_owner_label_keeps_pet_identity_fixed() -> None:
    assert PET_NAME == "六毛"
    assert social_pet_label("小梁") == "小梁家的六毛"
    assert social_pet_label("") == "搭子家的六毛"
    settings = PetSettings(pet_name="团团", owner_nickname="小梁")
    assert settings.pet_name == PET_NAME
    assert settings.owner_nickname == "小梁"


def test_load_settings_merges_position_and_user_selected_size(tmp_path) -> None:
    default_path = tmp_path / "default.json"
    override_path = tmp_path / "override.json"
    default_path.write_text(
        json.dumps({"display_height": 280, "movement_step": 3}),
        encoding="utf-8",
    )
    override_path.write_text(
        json.dumps(
            {
                "display_height": 9999,
                "movement_step": 0,
                "start_x": 25,
                "start_y": 40,
                "unknown": 1,
            }
        ),
        encoding="utf-8",
    )

    settings = load_settings(default_path, override_path)

    assert settings.display_height == 360
    assert settings.movement_step == 3
    assert settings.start_x == 25
    assert settings.start_y == 40
    assert not hasattr(settings, "unknown")


def test_broken_user_config_falls_back_to_defaults(tmp_path) -> None:
    default_path = tmp_path / "default.json"
    override_path = tmp_path / "override.json"
    default_path.write_text('{"movement_step": 5}', encoding="utf-8")
    override_path.write_text("not-json", encoding="utf-8")

    assert load_settings(default_path, override_path).movement_step == 5


def test_animation_timing_is_limited_to_safe_ranges(tmp_path) -> None:
    """动画节奏参数过大或过小时应被限制，避免计时器异常。"""

    default_path = tmp_path / "default.json"
    default_path.write_text(
        json.dumps({"walk_frame_interval_ms": 1, "turn_pause_ms": 9999}),
        encoding="utf-8",
    )

    settings = load_settings(default_path, tmp_path / "missing.json")

    assert settings.walk_frame_interval_ms == 50
    assert settings.turn_pause_ms == 1200


def test_default_inactivity_uses_five_and_ten_minutes() -> None:
    """默认无互动阈值应为五分钟坐下、十分钟睡觉。"""

    settings = PetSettings()
    assert settings.inactive_sit_ms == 300000
    assert settings.inactive_sleep_ms == 600000


def test_autonomous_walk_is_off_by_default_and_persistable(tmp_path) -> None:
    settings = load_settings(override_path=tmp_path / "missing.json")
    assert settings.allow_autonomous_walk is False
    settings.allow_autonomous_walk = True
    path = save_settings(settings, tmp_path / "settings.json")
    assert load_settings(override_path=path).allow_autonomous_walk is True


def test_today_note_mode_supports_three_persistent_choices(tmp_path) -> None:
    settings = load_settings(override_path=tmp_path / "missing.json")
    assert settings.today_note_mode == "detailed"
    settings.today_note_mode = "compact"
    path = save_settings(settings, tmp_path / "settings.json")
    assert load_settings(override_path=path).today_note_mode == "compact"

    invalid = tmp_path / "invalid.json"
    invalid.write_text(json.dumps({"today_note_mode": "giant-task-manager"}), encoding="utf-8")
    assert load_settings(override_path=invalid).today_note_mode == "detailed"


def test_content_updates_preference_is_persistent_and_defaults_on(tmp_path) -> None:
    settings = load_settings(override_path=tmp_path / "missing.json")
    assert settings.content_updates_enabled is True
    settings.content_updates_enabled = False
    path = save_settings(settings, tmp_path / "settings.json")
    assert load_settings(override_path=path).content_updates_enabled is False


def test_program_updates_preference_is_persistent_and_defaults_on(tmp_path) -> None:
    settings = load_settings(override_path=tmp_path / "missing.json")
    assert settings.program_updates_enabled is True
    settings.program_updates_enabled = False
    path = save_settings(settings, tmp_path / "settings.json")
    assert load_settings(override_path=path).program_updates_enabled is False


def test_idle_focus_pause_defaults_are_safe_and_persistable(tmp_path) -> None:
    settings = load_settings(override_path=tmp_path / "missing.json")
    assert settings.auto_pause_on_idle is False
    assert settings.idle_pause_seconds == 600
    settings.idle_pause_seconds = 5
    settings.idle_classification_rules = {"WINWORD.EXE": "focus", "bad": "ignore"}
    path = save_settings(settings, tmp_path / "settings.json")
    loaded = load_settings(override_path=path)
    assert loaded.idle_pause_seconds == 300
    assert loaded.idle_classification_rules == {"winword.exe": "focus"}


def test_save_settings_writes_json(tmp_path) -> None:
    path = tmp_path / "nested" / "settings.json"
    saved = save_settings(
        PetSettings(
            start_x=12,
            start_y=34,
            always_on_top=False,
            qq_music_path=r"C:\Music\QQMusic.exe",
            kugou_music_path=r"C:\Music\KuGou.exe",
            apple_music_path=r"C:\Music\AppleMusic.exe",
            spotify_music_path=r"C:\Music\Spotify.exe",
            babuda_audio_path=r"C:\Private\babuda-1.wav",
            local_lyrics_path=r"C:\Private\lyrics.txt",
            lyric_interval_minutes=6,
            music_provider_history={
                "qq": {"success_count": 3, "consecutive_failures": 0}
            },
        ),
        path,
    )

    data = json.loads(saved.read_text(encoding="utf-8"))
    assert data["start_x"] == 12
    assert data["start_y"] == 34
    assert data["always_on_top"] is False
    assert data["display_height"] == 160
    assert data["ai_provider"] == "offline"
    assert data["automatic_grumbling"] is True
    assert data["hourly_announcement"] is False
    assert data["qq_music_path"].endswith("QQMusic.exe")
    assert data["kugou_music_path"].endswith("KuGou.exe")
    assert data["apple_music_path"].endswith("AppleMusic.exe")
    assert data["spotify_music_path"].endswith("Spotify.exe")
    assert data["babuda_audio_path"].endswith("babuda-1.wav")
    assert data["local_lyrics_path"].endswith("lyrics.txt")
    assert data["lyric_interval_minutes"] == 6
    assert data["music_service"] == "auto"
    assert data["music_provider_history"]["qq"]["success_count"] == 3
    assert not any("token" in key or "key" in key for key in data)
    assert not path.with_suffix(".json.tmp").exists()


def test_local_path_and_lyric_interval_validation(tmp_path) -> None:
    default_path = tmp_path / "default.json"
    default_path.write_text("{}", encoding="utf-8")
    override_path = tmp_path / "override.json"
    override_path.write_text(
        json.dumps({"qq_music_path": "  demo.exe\u0000 ", "lyric_interval_minutes": 1}),
        encoding="utf-8",
    )

    settings = load_settings(default_path, override_path)

    assert settings.qq_music_path == "demo.exe"
    assert settings.lyric_interval_minutes == 2


def test_legacy_pet_name_is_migrated_to_owner_nickname(tmp_path) -> None:
    default_path = tmp_path / "default.json"
    override_path = tmp_path / "override.json"
    default_path.write_text("{}", encoding="utf-8")
    override_path.write_text(
        json.dumps({"pet_name": "  团团\u0000 "}),
        encoding="utf-8",
    )

    settings = load_settings(default_path, override_path)

    assert settings.pet_name == "六毛"
    assert settings.owner_nickname == "团团"
    path = save_settings(settings, tmp_path / "settings.json")
    loaded = load_settings(override_path=path)
    assert loaded.pet_name == "六毛"
    assert loaded.owner_nickname == "团团"


def test_empty_legacy_pet_name_keeps_fixed_identity_and_empty_owner(tmp_path) -> None:
    path = tmp_path / "override.json"
    path.write_text(json.dumps({"pet_name": "\u0000  "}), encoding="utf-8")

    settings = load_settings(override_path=path)
    assert settings.pet_name == "六毛"
    assert settings.owner_nickname == ""


def test_music_provider_history_is_safely_validated(tmp_path) -> None:
    default_path = tmp_path / "default.json"
    override_path = tmp_path / "override.json"
    default_path.write_text("{}", encoding="utf-8")
    override_path.write_text(
        json.dumps(
            {
                "music_service": "auto",
                "music_provider_history": {
                    "qq": {
                        "success_count": "4",
                        "failure_count": "broken",
                        "consecutive_failures": 999,
                        "last_error": "X" * 200,
                    },
                    "unknown": {"success_count": 20},
                },
            }
        ),
        encoding="utf-8",
    )

    settings = load_settings(default_path, override_path)

    assert settings.music_service == "auto"
    assert settings.music_provider_history["qq"]["success_count"] == 4
    assert settings.music_provider_history["qq"]["failure_count"] == 0
    assert settings.music_provider_history["qq"]["consecutive_failures"] == 100
    assert len(settings.music_provider_history["qq"]["last_error"]) == 80
    assert "unknown" not in settings.music_provider_history


def test_workmate_uses_independent_settings_directory(monkeypatch, tmp_path) -> None:
    """新版应避开旧桌宠保存的 220 像素尺寸，首次启动采用 160。"""

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

    assert user_settings_path() == tmp_path / "Lili" / "settings.json"
    assert PetSettings().display_height == 160


def test_window_mode_is_loaded_from_user_settings(tmp_path) -> None:
    """“始终置顶/桌面模式”必须跨重启保留。"""

    default_path = tmp_path / "default.json"
    override_path = tmp_path / "override.json"
    default_path.write_text('{"always_on_top": true}', encoding="utf-8")
    override_path.write_text('{"always_on_top": false}', encoding="utf-8")

    assert load_settings(default_path, override_path).always_on_top is False
