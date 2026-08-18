"""
本模块负责桌面宠物默认配置、用户配置、本地程序/音频路径、尺寸和窗口位置状态的加载与保存。

职责范围：
- 从项目内只读 JSON 读取默认功能设置；
- 从当前用户本地应用数据目录读取上次窗口位置、显示尺寸和非敏感体验设置；
- 持久化“始终置顶/桌面模式”，默认采用不抢焦点的 QQ 宠物式置顶行为；
- 校验窗口、移动、动画和转身节奏的数值范围并忽略未知字段；
- 仅在用户配置目录保存窗口、AI 提供方、陪伴开关和音乐 Provider 成败统计，不保存任何 API 令牌。

Agent 快速定位：
- 配置数据结构位于 PetSettings；
- 合并和校验逻辑位于 load_settings()；
- 持久化入口位于 save_settings()；
- 不应把机器相关的绝对路径写入项目默认配置。

输入为 JSON 文件，输出为 PetSettings 实例。保存操作会创建用户配置目录并原子写入
窗口、AI 提供方与陪伴开关，不会覆盖项目默认配置，也不访问网络。
Lili 使用独立的本地设置目录，同时兼容读取旧“六毛工作搭子”的尺寸与位置。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

from .resources import resource_path
from .local_data import platform_app_data_root


PET_NAME = "六毛"
DEFAULT_OWNER_NICKNAME = "搭子"


def clean_owner_nickname(value: Any) -> str:
    """Return the short social nickname, never a mutable pet identity."""

    clean = str(value or "").replace("\x00", "").strip()[:24]
    # The old default was accidentally rendered as “六毛搭子的六毛”.  Treat
    # it as an unset owner nickname while preserving real historical names.
    return "" if clean in {PET_NAME, "六毛搭子"} else clean


def social_pet_label(owner_nickname: Any) -> str:
    """Build the only social-facing identity used for another user's pet."""

    owner = clean_owner_nickname(owner_nickname) or DEFAULT_OWNER_NICKNAME
    return f"{owner}家的{PET_NAME}"


@dataclass
class PetSettings:
    """保存桌面宠物可配置参数和上次窗口位置。"""

    # Kept as a compatibility field for old settings files and integrations.
    # It is always normalised back to PET_NAME and is never edited by the UI.
    pet_name: str = PET_NAME
    owner_nickname: str = ""
    display_height: int = 160
    movement_interval_ms: int = 16
    movement_step: int = 1
    walk_frame_interval_ms: int = 90
    turn_pause_ms: int = 240
    idle_min_ms: int = 3000
    idle_max_ms: int = 7000
    action_min_ms: int = 3500
    action_max_ms: int = 7000
    inactive_sit_ms: int = 300000
    inactive_sleep_ms: int = 600000
    always_on_top: bool = True
    # Keep the pet's ambient animation alive, but do not make it cross the
    # desktop until the user explicitly enables autonomous walking.
    allow_autonomous_walk: bool = False
    start_x: int | None = None
    start_y: int | None = None
    ai_provider: str = "offline"
    ai_base_url: str = ""
    ai_model: str = ""
    automatic_grumbling: bool = True
    hourly_announcement: bool = False
    app_awareness: bool = True
    voice_enabled: bool = True
    lyric_inspiration_enabled: bool = True
    water_reminder_enabled: bool = False
    stand_reminder_enabled: bool = False
    water_interval_minutes: int = 45
    stand_interval_minutes: int = 60
    # Legacy compatibility only; work never pauses on input silence.\n    auto_pause_on_idle: bool = False
    idle_pause_seconds: int = 600
    # Per-application corrections for automatic idle classification.  Values
    # are deliberately small and local-only: ``rest`` or ``focus``.
    idle_classification_rules: dict[str, str] = field(default_factory=dict)
    music_service: str = "auto"
    music_provider_history: dict[str, dict[str, Any]] = field(default_factory=dict)
    qq_music_path: str = ""
    netease_music_path: str = ""
    kugou_music_path: str = ""
    apple_music_path: str = ""
    spotify_music_path: str = ""
    babuda_audio_path: str = ""
    local_lyrics_path: str = ""
    lyric_interval_minutes: int = 8
    equipped_outfit: str = ""
    today_note_display_mode: str = "pending"
    today_note_mode: str = "detailed"
    today_note_always_on_top: bool = False
    today_note_autoshow: bool = False
    today_note_folded: bool = False
    today_note_hide_completed: bool = False
    # Content-only updates (knowledge/config/assets) are independent from
    # installing a new program version and can be disabled by the user.
    content_updates_enabled: bool = True
    # Program updates are checked automatically, but installation always
    # requires an explicit user confirmation before the verified installer
    # is launched.
    program_updates_enabled: bool = True

    def __setattr__(self, name: str, value: Any) -> None:
        if name == "pet_name":
            value = PET_NAME
        super().__setattr__(name, value)


def user_settings_path() -> Path:
    """返回当前用户可写的设置文件路径。"""

    return platform_app_data_root() / "Lili" / "settings.json"


def legacy_settings_path() -> Path:
    """返回六毛工作搭子旧版本的设置路径，供一次性兼容读取。"""

    return platform_app_data_root() / "SixHairWorkmate" / "settings.json"


def _read_json(path: Path) -> dict[str, Any]:
    """读取 JSON 对象；文件不存在时返回空对象。"""

    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"无法读取配置文件 {path}：{exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"配置文件必须包含 JSON 对象：{path}")
    return value


def _validated(data: dict[str, Any]) -> PetSettings:
    """过滤未知字段并对关键数值执行安全范围校验。"""

    allowed = {field.name for field in fields(PetSettings)}
    clean = {key: value for key, value in data.items() if key in allowed}
    settings = PetSettings(**clean)
    settings.display_height = min(360, max(100, int(settings.display_height)))
    settings.movement_interval_ms = min(
        100,
        max(16, int(settings.movement_interval_ms)),
    )
    settings.movement_step = min(12, max(1, int(settings.movement_step)))
    settings.walk_frame_interval_ms = min(
        500,
        max(50, int(settings.walk_frame_interval_ms)),
    )
    settings.turn_pause_ms = min(1200, max(0, int(settings.turn_pause_ms)))
    settings.idle_min_ms = max(500, int(settings.idle_min_ms))
    settings.idle_max_ms = max(settings.idle_min_ms, int(settings.idle_max_ms))
    settings.action_min_ms = max(1000, int(settings.action_min_ms))
    settings.action_max_ms = max(
        settings.action_min_ms,
        int(settings.action_max_ms),
    )
    settings.inactive_sit_ms = max(5000, int(settings.inactive_sit_ms))
    settings.inactive_sleep_ms = max(
        settings.inactive_sit_ms + 5000,
        int(settings.inactive_sleep_ms),
    )
    legacy_name = settings.pet_name
    settings.owner_nickname = clean_owner_nickname(
        settings.owner_nickname or (legacy_name if legacy_name != PET_NAME else "")
    )
    settings.pet_name = PET_NAME
    settings.always_on_top = bool(settings.always_on_top)
    settings.allow_autonomous_walk = bool(settings.allow_autonomous_walk)
    if settings.ai_provider not in {"offline", "codex", "claude", "deepseek", "kimi", "custom"}:
        settings.ai_provider = "offline"
    settings.ai_base_url = str(settings.ai_base_url).strip()[:500]
    settings.ai_model = str(settings.ai_model).strip()[:120]
    settings.automatic_grumbling = bool(settings.automatic_grumbling)
    settings.hourly_announcement = bool(settings.hourly_announcement)
    settings.app_awareness = bool(settings.app_awareness)
    settings.voice_enabled = bool(settings.voice_enabled)
    settings.lyric_inspiration_enabled = bool(settings.lyric_inspiration_enabled)
    settings.water_reminder_enabled = bool(settings.water_reminder_enabled)
    settings.stand_reminder_enabled = bool(settings.stand_reminder_enabled)
    settings.water_interval_minutes = min(240, max(10, int(settings.water_interval_minutes)))
    settings.stand_interval_minutes = min(240, max(10, int(settings.stand_interval_minutes)))
    # Keep legacy fields readable for old settings files, but never enable
    # heuristic input-idle pausing again. Only an explicit user action (or a
    # verified system sleep event) may stop a focus timer.
    settings.auto_pause_on_idle = False
    settings.idle_pause_seconds = min(7200, max(300, int(settings.idle_pause_seconds)))
    rules: dict[str, str] = {}
    if isinstance(settings.idle_classification_rules, dict):
        for raw_key, raw_value in settings.idle_classification_rules.items():
            key = str(raw_key).replace("\x00", "").strip().casefold()[:160]
            value = str(raw_value).strip().casefold()
            if key and value in {"rest", "focus"}:
                rules[key] = value
    settings.idle_classification_rules = rules
    if settings.music_service not in {"auto", "qq", "netease", "kugou", "apple", "spotify"}:
        settings.music_service = "auto"
    def safe_int(value: Any, default: int = 0) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    def safe_float(value: Any, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    history: dict[str, dict[str, Any]] = {}
    if isinstance(settings.music_provider_history, dict):
        for provider, raw in settings.music_provider_history.items():
            if provider not in {"qq", "netease", "kugou", "apple", "spotify"} or not isinstance(raw, dict):
                continue
            history[provider] = {
                "success_count": max(0, min(10_000, safe_int(raw.get("success_count", 0)))),
                "failure_count": max(0, min(10_000, safe_int(raw.get("failure_count", 0)))),
                "consecutive_failures": max(0, min(100, safe_int(raw.get("consecutive_failures", 0)))),
                "last_success_at": max(0.0, safe_float(raw.get("last_success_at", 0.0))),
                "last_failure_at": max(0.0, safe_float(raw.get("last_failure_at", 0.0))),
                "last_error": str(raw.get("last_error", ""))[:80],
            }
    settings.music_provider_history = history
    settings.qq_music_path = str(settings.qq_music_path).replace("\x00", "").strip()[:1200]
    settings.netease_music_path = str(settings.netease_music_path).replace("\x00", "").strip()[:1200]
    settings.kugou_music_path = str(settings.kugou_music_path).replace("\x00", "").strip()[:1200]
    settings.apple_music_path = str(settings.apple_music_path).replace("\x00", "").strip()[:1200]
    settings.spotify_music_path = str(settings.spotify_music_path).replace("\x00", "").strip()[:1200]
    settings.babuda_audio_path = str(settings.babuda_audio_path).replace("\x00", "").strip()[:1200]
    settings.local_lyrics_path = str(settings.local_lyrics_path).replace("\x00", "").strip()[:1200]
    settings.lyric_interval_minutes = min(120, max(2, int(settings.lyric_interval_minutes)))
    settings.equipped_outfit = str(settings.equipped_outfit)[:60]
    if settings.today_note_display_mode not in {"always", "pending", "hidden"}:
        settings.today_note_display_mode = "pending"
    if settings.today_note_mode not in {"detailed", "compact", "hidden"}:
        settings.today_note_mode = "detailed"
    settings.today_note_always_on_top = bool(settings.today_note_always_on_top)
    settings.today_note_autoshow = bool(settings.today_note_autoshow)
    settings.today_note_folded = bool(settings.today_note_folded)
    settings.today_note_hide_completed = bool(settings.today_note_hide_completed)
    settings.content_updates_enabled = bool(settings.content_updates_enabled)
    settings.program_updates_enabled = bool(settings.program_updates_enabled)
    return settings


def load_settings(
    default_path: Path | None = None,
    override_path: Path | None = None,
) -> PetSettings:
    """合并默认与用户配置；损坏的用户配置回退为默认配置。"""

    default_file = default_path or resource_path("config/settings.json")
    user_file = override_path or user_settings_path()
    if override_path is None and not user_file.exists() and legacy_settings_path().exists():
        user_file = legacy_settings_path()
    base = _read_json(default_file)
    try:
        override = _read_json(user_file)
    except ValueError:
        override = {}
    base.update(
        {
            key: value
            for key, value in override.items()
            if key
            in {
                "pet_name",
                "owner_nickname",
                "display_height",
                "start_x",
                "start_y",
                "always_on_top",
                "allow_autonomous_walk",
                "ai_provider",
                "ai_base_url",
                "ai_model",
                "automatic_grumbling",
                "hourly_announcement",
                "app_awareness",
                "voice_enabled",
                "lyric_inspiration_enabled",
                "water_reminder_enabled",
                "stand_reminder_enabled",
                "water_interval_minutes",
                "stand_interval_minutes",
                "auto_pause_on_idle",
                "idle_pause_seconds",
                "idle_classification_rules",
                "music_service",
                "music_provider_history",
                "qq_music_path",
                "netease_music_path",
                "kugou_music_path",
                "apple_music_path",
                "spotify_music_path",
                "babuda_audio_path",
                "local_lyrics_path",
                "lyric_interval_minutes",
                "equipped_outfit",
                "today_note_display_mode",
                "today_note_mode",
                "today_note_always_on_top",
                "today_note_autoshow",
                "today_note_folded",
                "today_note_hide_completed",
                "content_updates_enabled",
                "program_updates_enabled",
            }
        }
    )
    # Older builds used pet_name/name/display_name/nickname for the editable
    # value.  Preserve that value as the new owner nickname, but never let it
    # alter the fixed pet identity.
    if not str(base.get("owner_nickname") or "").strip():
        for legacy_key in ("nickname", "display_name", "name", "pet_name"):
            legacy_value = str(override.get(legacy_key) or "").strip()
            if legacy_value and legacy_value != PET_NAME:
                base["owner_nickname"] = legacy_value
                break
    return _validated(base)


def save_settings(settings: PetSettings, path: Path | None = None) -> Path:
    """将设置原子写入用户目录并返回最终路径。"""

    target = path or user_settings_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".json.tmp")
    state = {
        "pet_name": PET_NAME,
        "owner_nickname": clean_owner_nickname(settings.owner_nickname),
        "display_height": settings.display_height,
        "start_x": settings.start_x,
        "start_y": settings.start_y,
        "always_on_top": settings.always_on_top,
        "allow_autonomous_walk": settings.allow_autonomous_walk,
        "ai_provider": settings.ai_provider,
        "ai_base_url": settings.ai_base_url,
        "ai_model": settings.ai_model,
        "automatic_grumbling": settings.automatic_grumbling,
        "hourly_announcement": settings.hourly_announcement,
        "app_awareness": settings.app_awareness,
        "voice_enabled": settings.voice_enabled,
        "lyric_inspiration_enabled": settings.lyric_inspiration_enabled,
        "water_reminder_enabled": settings.water_reminder_enabled,
        "stand_reminder_enabled": settings.stand_reminder_enabled,
        "water_interval_minutes": settings.water_interval_minutes,
        "stand_interval_minutes": settings.stand_interval_minutes,
        "auto_pause_on_idle": settings.auto_pause_on_idle,
        "idle_pause_seconds": settings.idle_pause_seconds,
        "idle_classification_rules": settings.idle_classification_rules,
        "music_service": settings.music_service,
        "music_provider_history": settings.music_provider_history,
        "qq_music_path": settings.qq_music_path,
        "netease_music_path": settings.netease_music_path,
        "kugou_music_path": settings.kugou_music_path,
        "apple_music_path": settings.apple_music_path,
        "spotify_music_path": settings.spotify_music_path,
        "babuda_audio_path": settings.babuda_audio_path,
        "local_lyrics_path": settings.local_lyrics_path,
        "lyric_interval_minutes": settings.lyric_interval_minutes,
        "equipped_outfit": settings.equipped_outfit,
        "today_note_display_mode": settings.today_note_display_mode,
        "today_note_mode": settings.today_note_mode,
        "today_note_always_on_top": settings.today_note_always_on_top,
        "today_note_autoshow": settings.today_note_autoshow,
        "today_note_folded": settings.today_note_folded,
        "today_note_hide_completed": settings.today_note_hide_completed,
        "content_updates_enabled": settings.content_updates_enabled,
        "program_updates_enabled": settings.program_updates_enabled,
    }
    temporary.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(target)
    return target
