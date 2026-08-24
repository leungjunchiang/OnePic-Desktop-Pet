"""
本模块提供“一图桌宠”原图登记和两个人工确认门禁的命令行工具。

职责范围：
- 登记用户最初上传的原图，保留私有原始副本，并生成全分辨率自拍 PNG；
- 登记首张标准角色候选、生成风格和人物特色说明，等待用户在图形窗口中明确确认；
- 根据私有宠物清单生成走路 GIF 预览，等待用户确认动作自然度；
- 查询当前流程状态，并为动作生成或打包执行门禁检查。

Agent 快速定位：
- 原图登记位于 register_source()；
- 标准角色候选和确认位于 register_character_candidate()、review_character_candidate()；
- 走路预览和确认位于 create_walk_review()、approve_walk()；
- 命令行入口位于 main()。

输入为用户本地图片和私有素材清单，输出只写入 Git 忽略的 `user_assets/`。
工具不访问网络、不修改公开 `assets/`，覆盖已有自拍或流程状态前会先写入私有备份目录。

使用示例：
    py tools/onepic_workflow.py init --source user_assets/image.png
    py tools/onepic_workflow.py character-candidate --image candidate.png --feature 长辫子
    py tools/onepic_workflow.py approve-character
    py tools/onepic_workflow.py walk-review
    py tools/onepic_workflow.py approve-walk --yes
    py tools/onepic_workflow.py status
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageOps

from onepic_desktop_pet.workflow import (
    WorkflowError,
    character_is_approved,
    load_workflow,
    now_utc,
    require_character_approved,
    require_custom_pet_approved,
    save_workflow,
    walk_is_approved,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
USER_ROOT = PROJECT_ROOT / "user_assets"
STATE_PATH = USER_ROOT / "workflow.json"


def file_sha256(path: Path) -> str:
    """计算文件哈希，便于确认自拍照片确实来自登记原图。"""

    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _relative(path: Path) -> str:
    """返回相对于项目根目录的正斜杠路径。"""

    return path.resolve().relative_to(PROJECT_ROOT).as_posix()


def _backup_if_present(path: Path) -> None:
    """覆盖私有文件前按时间保存可恢复副本。"""

    if not path.is_file():
        return
    backup_root = USER_ROOT / "backups"
    backup_root.mkdir(parents=True, exist_ok=True)
    # Keep private backup paths short enough for Windows temporary roots.
    # The full ISO timestamp could push an otherwise valid path over the
    # legacy MAX_PATH limit during tests or portable builds.
    stamp = now_utc().replace("+00:00", "Z").replace("-", "").replace(":", "")
    shutil.copy2(path, backup_root / f"{path.stem}-{stamp}{path.suffix}")


def _open_valid_image(path: Path) -> Image.Image:
    """读取、纠正 EXIF 方向并返回与原图同分辨率的像素内容。"""

    try:
        with Image.open(path) as source:
            source.verify()
        with Image.open(path) as source:
            return ImageOps.exif_transpose(source).convert("RGBA")
    except (OSError, ValueError) as exc:
        raise WorkflowError(f"无法读取输入图片：{path}：{exc}") from exc


def register_source(source_path: Path) -> dict[str, Any]:
    """登记原图，同时保存原始文件副本和全分辨率自拍 PNG。"""

    source = source_path.resolve()
    if not source.is_file():
        raise FileNotFoundError(f"找不到最初上传的原图：{source}")
    image = _open_valid_image(source)
    source_root = USER_ROOT / "source"
    source_root.mkdir(parents=True, exist_ok=True)
    suffix = source.suffix.lower() or ".img"
    original_copy = source_root / f"original{suffix}"
    selfie_path = USER_ROOT / "selfie.png"
    _backup_if_present(original_copy)
    _backup_if_present(selfie_path)
    _backup_if_present(STATE_PATH)
    if source != original_copy.resolve():
        shutil.copy2(source, original_copy)
    image.save(selfie_path, "PNG", optimize=True)
    state: dict[str, Any] = {
        "version": 1,
        "created_at": now_utc(),
        "source": {
            "original": _relative(original_copy),
            "original_sha256": file_sha256(original_copy),
            "selfie": _relative(selfie_path),
            "selfie_pixel_size": list(image.size),
        },
        "character": {
            "status": "not_submitted",
            "candidate": None,
            "identity_features": [],
        },
        "walk": {"status": "not_generated", "preview": None},
    }
    save_workflow(state, STATE_PATH)
    return state


def register_character_candidate(
    image_path: Path,
    features: list[str],
    style: str = "preserve_original",
) -> dict[str, Any]:
    """保存首张标准角色候选，并将流程停在等待用户确认状态。"""

    state = load_workflow(STATE_PATH)
    if not state.get("source"):
        raise WorkflowError("请先使用 init 命令登记最初上传的原图。")
    image = _open_valid_image(image_path.resolve())
    review_root = USER_ROOT / "review"
    review_root.mkdir(parents=True, exist_ok=True)
    candidate = review_root / "standard-character-candidate.png"
    _backup_if_present(candidate)
    image.save(candidate, "PNG", optimize=True)
    state["character"] = {
        "status": "awaiting_user_confirmation",
        "candidate": _relative(candidate),
        "candidate_sha256": file_sha256(candidate),
        "style": style,
        "identity_features": [item.strip() for item in features if item.strip()],
        "submitted_at": now_utc(),
    }
    state["walk"] = {"status": "not_generated", "preview": None}
    save_workflow(state, STATE_PATH)
    return state


def approve_character(
    confirmed: bool,
    confirmation_method: str = "direct_user_review",
) -> dict[str, Any]:
    """在用户看过候选并确认后锁定标准角色及其文件哈希。"""

    if not confirmed:
        raise WorkflowError("必须由用户查看标准角色候选图并明确确认。")
    state = load_workflow(STATE_PATH)
    character = state.get("character", {})
    candidate = character.get("candidate")
    if character.get("status") != "awaiting_user_confirmation" or not candidate:
        raise WorkflowError("当前没有等待确认的标准角色候选图。")
    candidate_path = PROJECT_ROOT / candidate
    candidate_hash = character.get("candidate_sha256")
    if not candidate_path.is_file() or file_sha256(candidate_path) != candidate_hash:
        raise WorkflowError("候选图在提交后发生变化，请重新登记并确认。")
    approved = USER_ROOT / "approved-character.png"
    _backup_if_present(approved)
    shutil.copy2(PROJECT_ROOT / candidate, approved)
    character.update(
        {
            "status": "approved",
            "approved_image": _relative(approved),
            "approved_candidate_sha256": candidate_hash,
            "user_confirmed": True,
            "confirmation_method": confirmation_method,
            "approved_at": now_utc(),
        }
    )
    state["character"] = character
    save_workflow(state, STATE_PATH)
    return state


def reject_character(reason: str = "用户要求重新生成") -> dict[str, Any]:
    """撤销当前角色确认并保留旧文件，避免错误素材继续生成动作。"""

    state = load_workflow(STATE_PATH)
    if not state.get("source"):
        raise WorkflowError("尚未登记原图，无法重置标准角色。")
    _backup_if_present(STATE_PATH)
    character = state.get("character", {})
    character.update(
        {
            "status": "rejected",
            "user_confirmed": False,
            "rejected_reason": reason.strip() or "用户要求重新生成",
            "rejected_at": now_utc(),
        }
    )
    character.pop("approved_candidate_sha256", None)
    character.pop("approved_at", None)
    state["character"] = character
    state["walk"] = {"status": "not_generated", "preview": None}
    save_workflow(state, STATE_PATH)
    return state


def review_character_candidate() -> int:
    """显示标准角色候选图；返回 1 表示确认、2 表示要求重做、0 表示关闭。"""

    state = load_workflow(STATE_PATH)
    character = state.get("character", {})
    candidate = character.get("candidate")
    if character.get("status") != "awaiting_user_confirmation" or not candidate:
        raise WorkflowError("当前没有等待确认的标准角色候选图。")
    candidate_path = PROJECT_ROOT / candidate
    if not candidate_path.is_file():
        raise WorkflowError(f"标准角色候选图不存在：{candidate_path}")

    from PySide6.QtCore import Qt
    from PySide6.QtGui import QPixmap
    from PySide6.QtWidgets import (
        QApplication,
        QDialog,
        QHBoxLayout,
        QLabel,
        QPushButton,
        QVBoxLayout,
    )

    app = QApplication.instance() or QApplication([])
    dialog = QDialog()
    dialog.setWindowTitle("确认标准角色 · OnePic Desktop Pet")
    dialog.setMinimumWidth(520)
    layout = QVBoxLayout(dialog)
    title = QLabel("请确认：这张图是否保留了角色的辨识度和原有气质？")
    title.setWordWrap(True)
    layout.addWidget(title)
    image_label = QLabel()
    image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    image_label.setPixmap(
        QPixmap(str(candidate_path)).scaled(
            460,
            560,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
    )
    layout.addWidget(image_label)
    style_labels = {
        "preserve_original": "保留原画风",
        "light_chibi": "轻度 Q 版",
        "full_chibi": "完整 Q 版",
    }
    details = QLabel(
        "生成风格："
        + style_labels.get(character.get("style"), character.get("style", "未记录"))
        + "\n身份特征："
        + "、".join(character.get("identity_features", []))
    )
    details.setWordWrap(True)
    layout.addWidget(details)
    buttons = QHBoxLayout()
    redo_button = QPushButton("不符合，重新生成")
    confirm_button = QPushButton("符合，这就是我要的角色")
    confirm_button.setDefault(True)
    redo_button.clicked.connect(lambda: dialog.done(2))
    confirm_button.clicked.connect(dialog.accept)
    buttons.addWidget(redo_button)
    buttons.addWidget(confirm_button)
    layout.addLayout(buttons)
    result = dialog.exec()
    app.processEvents()
    return int(result)


def _review_frame(frame: Image.Image) -> Image.Image:
    """把透明走路帧放到带地面的浅色背景，便于识别滑步。"""

    rgba = frame.convert("RGBA")
    canvas = Image.new("RGBA", rgba.size, (247, 247, 244, 255))
    draw = ImageDraw.Draw(canvas)
    ground_y = rgba.height - 15
    draw.line((20, ground_y, rgba.width - 20, ground_y), fill=(190, 166, 150, 255), width=2)
    canvas.alpha_composite(rgba)
    return canvas.convert("RGB")


def create_walk_review(manifest_path: Path) -> dict[str, Any]:
    """读取八帧走路素材并生成必须由用户查看的 GIF 预览。"""

    state = require_character_approved(STATE_PATH)
    manifest_file = manifest_path.resolve()
    if not manifest_file.is_file():
        raise FileNotFoundError(f"找不到私有宠物清单：{manifest_file}")
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    walk_paths = manifest.get("animations", {}).get("walk", [])
    if len(walk_paths) < 8:
        raise WorkflowError(f"走路预览至少需要 8 帧，当前只有 {len(walk_paths)} 帧。")
    frames: list[Image.Image] = []
    for relative in walk_paths:
        path = manifest_file.parent / relative
        if not path.is_file():
            raise FileNotFoundError(f"走路清单引用了缺失文件：{path}")
        with Image.open(path) as source:
            frames.append(_review_frame(source))
    review_root = USER_ROOT / "review"
    review_root.mkdir(parents=True, exist_ok=True)
    preview = review_root / "walk-preview.gif"
    _backup_if_present(preview)
    frames[0].save(
        preview,
        save_all=True,
        append_images=frames[1:],
        duration=90,
        loop=0,
        optimize=False,
    )
    state["walk"] = {
        "status": "awaiting_user_confirmation",
        "preview": _relative(preview),
        "frame_count": len(frames),
        "submitted_at": now_utc(),
        "review_checks": [
            "左右脚确实交替",
            "落脚、下压、迈步、抬升四阶段完整",
            "身体和头发存在自然起伏或滞后",
            "连续循环没有平移、滑步和跳帧",
        ],
    }
    save_workflow(state, STATE_PATH)
    return state


def approve_walk(confirmed: bool) -> dict[str, Any]:
    """在用户看过 GIF 后显式确认走路自然度。"""

    if not confirmed:
        raise WorkflowError("必须传入 --yes，且只能在用户查看走路 GIF 后执行。")
    state = require_character_approved(STATE_PATH)
    walk = state.get("walk", {})
    if walk.get("status") != "awaiting_user_confirmation":
        raise WorkflowError("当前没有等待确认的走路动态预览。")
    preview = walk.get("preview")
    if not preview or not (PROJECT_ROOT / preview).is_file():
        raise WorkflowError("走路动态预览不存在，不能确认。")
    walk.update({"status": "approved", "approved_at": now_utc()})
    state["walk"] = walk
    save_workflow(state, STATE_PATH)
    return state


def print_status(state: dict[str, Any]) -> None:
    """打印适合用户和 Agent 阅读的简短状态。"""

    print(f"原图：{'已登记' if state.get('source') else '未登记'}")
    print(f"标准角色：{state.get('character', {}).get('status', '未开始')}")
    print(f"走路预览：{state.get('walk', {}).get('status', '未开始')}")
    print(f"允许批量生成动作：{'是' if character_is_approved(state) else '否'}")
    print(
        "允许加载和打包本地桌宠："
        f"{'是' if character_is_approved(state) and walk_is_approved(state) else '否'}"
    )


def parse_args() -> argparse.Namespace:
    """解析子命令参数。"""

    parser = argparse.ArgumentParser(description="管理一图桌宠的人工确认门禁")
    commands = parser.add_subparsers(dest="command", required=True)
    initialize = commands.add_parser("init", help="登记原图并自动设置自拍照片")
    initialize.add_argument("--source", type=Path, required=True)
    candidate = commands.add_parser("character-candidate", help="登记首张标准角色候选")
    candidate.add_argument("--image", type=Path, required=True)
    candidate.add_argument("--feature", action="append", default=[])
    candidate.add_argument(
        "--style",
        choices=("preserve_original", "light_chibi", "full_chibi"),
        default="preserve_original",
    )
    commands.add_parser("approve-character", help="打开窗口确认标准角色")
    reset = commands.add_parser("reset-character", help="撤销错误角色并等待重新生成")
    reset.add_argument("--reason", default="用户要求重新生成")
    review = commands.add_parser("walk-review", help="生成八帧走路动态预览")
    review.add_argument(
        "--manifest",
        type=Path,
        default=USER_ROOT / "pet" / "manifest.json",
    )
    approve_motion = commands.add_parser("approve-walk", help="确认走路动态预览")
    approve_motion.add_argument("--yes", action="store_true")
    commands.add_parser("check-actions", help="检查是否允许批量生成动作")
    commands.add_parser("check-package", help="检查是否允许加载或打包私有桌宠")
    commands.add_parser("status", help="显示当前制作流程状态")
    return parser.parse_args()


def main() -> int:
    """执行指定流程命令并返回进程退出码。"""

    args = parse_args()
    try:
        if args.command == "init":
            state = register_source(args.source)
        elif args.command == "character-candidate":
            state = register_character_candidate(args.image, args.feature, args.style)
        elif args.command == "approve-character":
            review_result = review_character_candidate()
            if review_result == 1:
                state = approve_character(True, "gui_user_review")
            elif review_result == 2:
                state = reject_character("用户在确认窗口中要求重新生成")
            else:
                raise WorkflowError("确认窗口已关闭，标准角色仍保持待确认状态。")
        elif args.command == "reset-character":
            state = reject_character(args.reason)
        elif args.command == "walk-review":
            state = create_walk_review(args.manifest)
        elif args.command == "approve-walk":
            state = approve_walk(args.yes)
        elif args.command == "check-actions":
            state = require_character_approved(STATE_PATH)
        elif args.command == "check-package":
            state = require_custom_pet_approved(STATE_PATH)
        else:
            state = load_workflow(STATE_PATH)
        print_status(state)
        return 0
    except (FileNotFoundError, WorkflowError, json.JSONDecodeError) as exc:
        print(f"流程未通过：{exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

