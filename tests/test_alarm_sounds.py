"""Tests for the opt-in, copied local alarm sound library."""

from pathlib import Path

import pytest

from onepic_desktop_pet.alarm_sounds import AlarmSoundLibrary


def test_imported_sound_is_copied_to_lili_directory(tmp_path: Path) -> None:
    source = tmp_path / "起床.mp3"
    source.write_bytes(b"audio")
    library = AlarmSoundLibrary(tmp_path / "data")

    sound = library.import_file(source)

    copied = Path(sound.imported_path)
    assert copied.is_file()
    assert copied.parent.name == "alarms"
    assert copied.read_bytes() == b"audio"
    source.unlink()
    assert library.resolve_path(sound.sound_id) == copied


def test_unsupported_sound_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "alarm.ogg"
    source.write_bytes(b"audio")
    library = AlarmSoundLibrary(tmp_path / "data")

    with pytest.raises(ValueError):
        library.import_file(source)


def test_deleted_or_missing_sound_resolves_to_fallback(tmp_path: Path) -> None:
    source = tmp_path / "alarm.wav"
    source.write_bytes(b"audio")
    library = AlarmSoundLibrary(tmp_path / "data")
    sound = library.import_file(source)

    assert library.remove(sound.sound_id)
    assert library.resolve_path(sound.sound_id) is None
    assert library.display_name(sound.sound_id).startswith("系统提示音")

