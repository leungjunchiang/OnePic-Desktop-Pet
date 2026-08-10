"""验证用户本地歌词和音频变体只按受限路径读取，不访问网络。"""

from onepic_desktop_pet.local_content import find_audio_variants, load_local_lines


def test_local_lines_support_utf8_deduplicate_and_limit(tmp_path) -> None:
    path = tmp_path / "lyrics.txt"
    path.write_text("第一句\n\n第一句\n第二句\n第三句\n", encoding="utf-8-sig")

    assert load_local_lines(str(path), limit=2) == ("第一句", "第二句")
    assert load_local_lines(str(tmp_path / "missing.txt")) == ()


def test_babuda_variants_keep_selected_first_and_find_siblings(tmp_path) -> None:
    first = tmp_path / "babuda-1.wav"
    second = tmp_path / "babuda-2.wav"
    unrelated = tmp_path / "other.wav"
    for path in (first, second, unrelated):
        path.write_bytes(b"RIFF")

    variants = find_audio_variants(str(second))

    assert variants[0] == second
    assert first in variants
    assert unrelated not in variants
