"""
本模块管理“一图桌宠”制作流程中的人工确认状态。

职责范围：
- 读取和原子保存 `user_assets/workflow.json`；
- 记录原图、风格选择、标准角色候选、角色确认、走路预览和走路确认状态；
- 为动作生成、运行自定义宠物和打包提供强制门禁；
- 不负责生成图片、不修改公开演示素材，也不访问网络。

Agent 快速定位：
- 状态读取和写入位于 load_workflow()、save_workflow()；
- 角色与走路确认判断位于 character_is_approved()、walk_is_approved()；
- 自定义宠物总门禁位于 custom_pet_is_approved()；
- 需要阻止后续步骤时调用 require_character_approved() 或 require_custom_pet_approved()。

输入为私有 workflow JSON 和可选的项目根目录，输出为状态字典或门禁判断结果。
写入只发生在 Git 忽略的 `user_assets/` 中，并采用临时文件替换，避免部分写入。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .resources import resource_root


class WorkflowError(RuntimeError):
    """表示制作流程缺少前置确认或状态文件损坏。"""


def now_utc() -> str:
    """返回适合写入 JSON 的 UTC 时间。"""

    return datetime.now(UTC).isoformat(timespec="seconds")


def workflow_path(project_root: Path | None = None) -> Path:
    """返回当前项目的私有制作流程状态文件。"""

    root = project_root or resource_root()
    return root / "user_assets" / "workflow.json"


def load_workflow(path: Path | None = None) -> dict[str, Any]:
    """读取流程状态；文件不存在时返回空字典，格式错误时拒绝继续。"""

    target = path or workflow_path()
    if not target.is_file():
        return {}
    try:
        value = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WorkflowError(f"无法读取制作流程状态：{target}：{exc}") from exc
    if not isinstance(value, dict):
        raise WorkflowError(f"制作流程状态必须是 JSON 对象：{target}")
    return value


def save_workflow(state: dict[str, Any], path: Path | None = None) -> Path:
    """原子保存私有流程状态并返回目标路径。"""

    target = path or workflow_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(target)
    return target


def character_is_approved(state: dict[str, Any]) -> bool:
    """校验标准角色是否由用户确认，且确认后候选内容没有变化。"""

    character = state.get("character", {})
    candidate_hash = character.get("candidate_sha256")
    return bool(
        character.get("status") == "approved"
        and character.get("user_confirmed") is True
        and candidate_hash
        and character.get("approved_candidate_sha256") == candidate_hash
    )


def walk_is_approved(state: dict[str, Any]) -> bool:
    """返回用户是否已经查看并确认走路动态预览。"""

    return state.get("walk", {}).get("status") == "approved"


def custom_pet_is_approved(path: Path | None = None) -> bool:
    """仅在角色与走路都确认后允许应用加载私有桌宠。"""

    state = load_workflow(path)
    return character_is_approved(state) and walk_is_approved(state)


def require_character_approved(path: Path | None = None) -> dict[str, Any]:
    """要求首张标准角色已确认，否则抛出可读错误。"""

    state = load_workflow(path)
    if not character_is_approved(state):
        raise WorkflowError(
            "尚未由用户确认首张标准角色形象；禁止继续批量生成动作。"
        )
    return state


def require_custom_pet_approved(path: Path | None = None) -> dict[str, Any]:
    """要求角色和走路预览都已确认，否则阻止加载或打包私有桌宠。"""

    state = require_character_approved(path)
    if not walk_is_approved(state):
        raise WorkflowError("尚未确认走路动态预览；禁止使用或打包私有桌宠。")
    return state
