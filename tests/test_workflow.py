"""
本模块测试一图桌宠的原图登记、标准角色确认和走路确认门禁。

测试只在 pytest 临时目录创建原图、候选图、走路帧和 workflow JSON，
不读取或覆盖真实 `user_assets/`，不启动 GUI，也不访问网络。
"""

from pathlib import Path

from PIL import Image

from onepic_desktop_pet.workflow import (
    WorkflowError,
    custom_pet_is_approved,
    require_character_approved,
)
from tools import onepic_workflow


def _configure_private_root(monkeypatch, project_root: Path) -> Path:
    """让命令工具只操作测试临时目录。"""

    user_root = project_root / "user_assets"
    monkeypatch.setattr(onepic_workflow, "PROJECT_ROOT", project_root)
    monkeypatch.setattr(onepic_workflow, "USER_ROOT", user_root)
    monkeypatch.setattr(onepic_workflow, "STATE_PATH", user_root / "workflow.json")
    return user_root


def _save_image(path: Path, color: tuple[int, int, int, int]) -> None:
    """创建测试使用的 RGBA 图片。"""

    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", (48, 64), color).save(path)


def test_source_registration_preserves_original_and_sets_selfie(
    tmp_path,
    monkeypatch,
) -> None:
    """登记原图后应保留原始副本并生成同分辨率自拍 PNG。"""

    user_root = _configure_private_root(monkeypatch, tmp_path)
    source = tmp_path / "upload.jpg"
    Image.new("RGB", (96, 128), (220, 210, 200)).save(source, quality=95)

    state = onepic_workflow.register_source(source)

    assert (user_root / "source" / "original.jpg").read_bytes() == source.read_bytes()
    with Image.open(user_root / "selfie.png") as selfie:
        assert selfie.size == (96, 128)
    assert state["character"]["status"] == "not_submitted"
    assert state["walk"]["status"] == "not_generated"


def test_character_and_walk_require_explicit_user_approval(
    tmp_path,
    monkeypatch,
) -> None:
    """没有两个显式确认时，动作生成和私有桌宠加载都必须被阻止。"""

    user_root = _configure_private_root(monkeypatch, tmp_path)
    source = tmp_path / "source.png"
    candidate = tmp_path / "candidate.png"
    _save_image(source, (240, 220, 210, 255))
    _save_image(candidate, (120, 80, 60, 255))
    onepic_workflow.register_source(source)
    state = onepic_workflow.register_character_candidate(
        candidate,
        ["长辫子", "白色 V 领"],
        "preserve_original",
    )
    assert state["character"]["style"] == "preserve_original"
    assert state["character"]["candidate_sha256"]

    try:
        require_character_approved(user_root / "workflow.json")
    except WorkflowError:
        pass
    else:
        raise AssertionError("未确认标准角色时不应允许生成动作")

    onepic_workflow.approve_character(True)
    assert not custom_pet_is_approved(user_root / "workflow.json")

    pet_root = user_root / "pet"
    walk_paths = []
    for index in range(8):
        relative = f"walk/walk_{index + 1:02d}.png"
        _save_image(pet_root / relative, (40 + index * 10, 80, 120, 255))
        walk_paths.append(relative)
    manifest = pet_root / "manifest.json"
    manifest.write_text(
        '{"animations": {"walk": '
        + str(walk_paths).replace("'", '"')
        + "}}",
        encoding="utf-8",
    )

    state = onepic_workflow.create_walk_review(manifest)
    assert state["walk"]["status"] == "awaiting_user_confirmation"
    assert (user_root / "review" / "walk-preview.gif").is_file()
    assert not custom_pet_is_approved(user_root / "workflow.json")

    onepic_workflow.approve_walk(True)
    assert custom_pet_is_approved(user_root / "workflow.json")


def test_changed_candidate_and_rejected_character_cannot_pass_gate(
    tmp_path,
    monkeypatch,
) -> None:
    """候选被替换或用户要求重做后，旧审批不得继续生效。"""

    user_root = _configure_private_root(monkeypatch, tmp_path)
    source = tmp_path / "source.png"
    candidate = tmp_path / "candidate.png"
    _save_image(source, (230, 220, 210, 255))
    _save_image(candidate, (100, 90, 80, 255))
    onepic_workflow.register_source(source)
    state = onepic_workflow.register_character_candidate(candidate, ["长侧辫"])
    stored_candidate = tmp_path / state["character"]["candidate"]
    _save_image(stored_candidate, (20, 30, 40, 255))

    try:
        onepic_workflow.approve_character(True)
    except WorkflowError:
        pass
    else:
        raise AssertionError("提交后被替换的候选图不应允许确认")

    onepic_workflow.register_character_candidate(candidate, ["长侧辫"])
    onepic_workflow.approve_character(True)
    assert custom_pet_is_approved(user_root / "workflow.json") is False
    state = onepic_workflow.reject_character("面部特征不准确")
    assert state["character"]["status"] == "rejected"
    assert state["character"]["user_confirmed"] is False
    assert state["walk"]["status"] == "not_generated"
