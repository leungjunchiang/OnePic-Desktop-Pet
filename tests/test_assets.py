"""
本模块检查桌面宠物派生素材清单、透明通道、连续帧数量和统一画布规格。

测试读取项目内生成的 PNG，不修改素材、不启动 GUI，也不访问网络。
"""

import hashlib
import json
from pathlib import Path

from PIL import Image

from tools.prepare_assets import split_equal_horizontal_sheet, split_horizontal_sheet


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_six_hair_identity_anchor_and_icon_match_approved_assets() -> None:
    """固定已验收六毛角色的站立主帧，并确保程序图标来自同一角色。"""

    idle_frame = PROJECT_ROOT / "assets" / "pet" / "idle" / "idle_01.png"
    pet_icon = PROJECT_ROOT / "assets" / "pet" / "icon.png"
    app_icon = PROJECT_ROOT / "assets" / "icons" / "pet.png"

    assert hashlib.sha256(idle_frame.read_bytes()).hexdigest() == (
        "af3aee460a98d4cf9655c4f854f0d8ef22e9c460a9fce1bb2adad5767d70d8d1"
    )
    assert hashlib.sha256(pet_icon.read_bytes()).hexdigest() == (
        "b2dc71bbcd5d74c3b2f02c7c7f0500480ff56a7a184a1f498ea1a4f88390005a"
    )
    assert app_icon.read_bytes() == pet_icon.read_bytes()


def test_manifest_assets_exist_and_are_transparent() -> None:
    manifest_path = PROJECT_ROOT / "assets" / "pet" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected_size = tuple(manifest["canvas_size"])

    animations = manifest["animations"]
    assert manifest["character_id"] == "six-hair-doll"
    assert manifest["display_name"] == "六毛公仔"
    assert all("user_assets" not in source for source in manifest["sources"])
    assert set(animations) == {
        "idle",
        "wave",
        "walk",
        "happy",
        "sit",
        "sleep",
        "selfie",
        "drag",
        "shy",
        "surprised",
        "annoyed",
        "sleepy",
        "curious",
    }
    assert len(animations["walk"]) == 8
    assert len(animations["selfie"]) == 4
    assert len(animations["idle"]) == 6
    assert len(animations["sit"]) == 5
    assert len(animations["sleep"]) == 5
    assert len(animations["drag"]) == 3
    assert sum(len(paths) for paths in animations.values()) == 38
    for relative_paths in animations.values():
        for relative in relative_paths:
            path = manifest_path.parent / relative
            with Image.open(path) as image:
                assert image.mode == "RGBA"
                assert image.size == expected_size
                assert image.getchannel("A").getextrema()[0] == 0
                assert image.getchannel("A").getbbox() is not None


def test_standing_animation_frames_use_consistent_character_height() -> None:
    manifest_path = PROJECT_ROOT / "assets" / "pet" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    animations = manifest["animations"]
    heights = []
    for state in (
        "idle",
        "wave",
        "happy",
        "shy",
        "surprised",
        "annoyed",
        "sleepy",
        "curious",
        "selfie",
    ):
        for relative in animations[state]:
            with Image.open(manifest_path.parent / relative) as image:
                bbox = image.getchannel("A").getbbox()
                assert bbox is not None
                heights.append(bbox[3] - bbox[1])

    assert max(heights) - min(heights) <= 8


def test_walk_cycle_has_eight_distinct_phases_and_motion_curve() -> None:
    """走路必须有八张不同帧，并配合程序位移曲线产生自然起伏。"""

    manifest_path = PROJECT_ROOT / "assets" / "pet" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    frame_hashes = []
    tops = []
    for relative in manifest["animations"]["walk"]:
        with Image.open(manifest_path.parent / relative) as image:
            rgba = image.convert("RGBA")
            bbox = image.getchannel("A").getbbox()
            assert bbox is not None
            tops.append(bbox[1])
            frame_hashes.append(hashlib.sha256(rgba.tobytes()).hexdigest())

    assert len(set(frame_hashes)) == 8
    assert max(tops) - min(tops) >= 5
    motion_factors = manifest["walk_motion_factors"]
    assert len(motion_factors) == 8
    assert max(motion_factors) - min(motion_factors) >= 1.0


def test_sleep_sheet_is_split_by_transparent_gutters_not_equal_width() -> None:
    """宽度逐渐增加的躺姿必须按透明间隔拆分，不能被等宽边界截头。"""

    source_path = (
        PROJECT_ROOT / "assets" / "generated" / "sit-to-sleep-v2-alpha.png"
    )
    with Image.open(source_path) as source:
        frames = split_horizontal_sheet(source.convert("RGBA"), 5)

    widths = [frame.getchannel("A").getbbox()[2] for frame in frames]
    assert len(frames) == 5
    assert len(set(frame.width for frame in frames)) > 1
    assert widths[-1] > widths[0]
    assert frames[-1].width >= frames[0].width * 1.5


def test_expression_sheet_keeps_symbols_inside_six_equal_cells() -> None:
    """互动表情必须按六个固定单元格拆分，不能把独立漫画符号误判为人物帧。"""

    source_path = (
        PROJECT_ROOT
        / "assets"
        / "generated"
        / "interaction-expressions-v2-alpha.png"
    )
    with Image.open(source_path) as source:
        frames = split_equal_horizontal_sheet(source.convert("RGBA"), 6)

    assert len(frames) == 6
    assert all(frame.getchannel("A").getbbox() is not None for frame in frames)


def test_seated_sleep_starts_at_same_height_as_final_sit_frame() -> None:
    """坐姿入睡首帧必须与坐下末帧同高，避免状态切换时人物突然放大。"""

    paths = (
        PROJECT_ROOT / "assets" / "pet" / "sit" / "sit_05.png",
        PROJECT_ROOT / "assets" / "pet" / "sleep" / "sleep_01.png",
    )
    heights = []
    for path in paths:
        with Image.open(path) as image:
            bbox = image.getchannel("A").getbbox()
            assert bbox is not None
            heights.append(bbox[3] - bbox[1])

    assert heights == [316, 316]


def test_six_hair_sit_transition_lowers_gradually() -> None:
    """六毛公仔的坐下过渡必须逐步降低，不能突然跳到坐姿。"""

    manifest_path = PROJECT_ROOT / "assets" / "pet" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    heights = []
    for index in (3, 4, 5):
        path = PROJECT_ROOT / "assets" / "pet" / "sit" / f"sit_{index:02d}.png"
        with Image.open(path) as image:
            bbox = image.getchannel("A").getbbox()
            assert bbox is not None
            heights.append(bbox[3] - bbox[1])

    assert heights[0] > heights[1] > heights[2]


def test_six_hair_sleep_transition_lowers_gradually() -> None:
    """六毛公仔入睡前三帧的高度必须随侧卧逐步降低。"""

    manifest_path = PROJECT_ROOT / "assets" / "pet" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    heights = []
    for index in (1, 2, 3):
        path = PROJECT_ROOT / "assets" / "pet" / "sleep" / f"sleep_{index:02d}.png"
        with Image.open(path) as image:
            bbox = image.getchannel("A").getbbox()
            assert bbox is not None
            heights.append(bbox[3] - bbox[1])

    assert heights[0] > heights[1] > heights[2]


def test_daily_action_library_is_transparent_consistent_and_uncropped() -> None:
    """46 张完整动作必须使用统一透明画布，并在四周保留安全边距。"""

    action_dir = PROJECT_ROOT / "assets" / "pet" / "daily-actions"
    paths = sorted(action_dir.glob("*.png"))

    assert len(paths) == 46
    for path in paths:
        with Image.open(path) as image:
            rgba = image.convert("RGBA")
            alpha = rgba.getchannel("A")
            bbox = alpha.getbbox()
            assert rgba.size == (1024, 1024), path.name
            assert rgba.mode == "RGBA", path.name
            assert alpha.getextrema()[0] == 0, path.name
            assert bbox is not None, path.name
            assert bbox[0] >= 34 and bbox[1] >= 34, path.name
            assert bbox[2] <= 990 and bbox[3] <= 990, path.name


def test_night_limited_sprite_is_transparent_and_normalized() -> None:
    """夜间限定素材必须是完整透明画布，不能带生成图的背景方块。"""

    path = PROJECT_ROOT / "assets" / "pet" / "night-limited" / "00-night-study-clean.png"
    with Image.open(path) as image:
        rgba = image.convert("RGBA")
        alpha = rgba.getchannel("A")
        bbox = alpha.getbbox()
        assert rgba.size == (1024, 1024)
        assert rgba.mode == "RGBA"
        assert alpha.getextrema()[0] == 0
        assert bbox is not None
        assert bbox[0] >= 34 and bbox[1] >= 34
        assert bbox[2] <= 990 and bbox[3] <= 990
        # The source PNG had an opaque generated checkerboard behind the
        # scene.  These points were background and must remain transparent.
        assert alpha.getpixel((64, 79)) == 0
        assert alpha.getpixel((500, 100)) == 0
        assert sum(
            1
            for red, green, blue, value in rgba.getdata()
            if value >= 250 and red >= 245 and green >= 245 and blue >= 245
        ) < 10_000


def test_hourly_outfit_library_is_transparent_consistent_and_uncropped() -> None:
    """1–12 小时娃衣必须完整、高清、透明且不贴边。"""

    outfit_dir = PROJECT_ROOT / "assets" / "pet" / "hourly-outfits"
    paths = sorted(outfit_dir.glob("*.png"))
    assert [path.name for path in paths] == [f"{hour:02d}-hour.png" for hour in range(1, 13)]
    for path in paths:
        with Image.open(path) as image:
            rgba = image.convert("RGBA")
            alpha = rgba.getchannel("A")
            bbox = alpha.getbbox()
            assert rgba.size == (1024, 1024), path.name
            assert alpha.getextrema()[0] == 0, path.name
            assert bbox is not None, path.name
            assert bbox[0] >= 34 and bbox[1] >= 34, path.name
            assert bbox[2] <= 990 and bbox[3] <= 990, path.name


def test_three_day_login_outfit_is_complete_transparent_and_uncropped() -> None:
    """登录奖励素材必须是完整 PNG，不能被截断成半套娃衣。"""

    path = PROJECT_ROOT / "assets" / "pet" / "login-rewards" / "3-day-login.png"
    with Image.open(path) as image:
        rgba = image.convert("RGBA")
        alpha = rgba.getchannel("A")
        assert rgba.size == (1254, 1254)
        assert alpha.getextrema()[0] == 0
        bbox = alpha.getbbox()
        assert bbox is not None
        assert bbox[0] >= 34 and bbox[1] >= 20
        assert bbox[2] <= 1220 and bbox[3] <= 1234
