"""Small, private library for user-imported alarm sounds.

The library never scans the user's Music folder.  A sound enters Lili only
after the user chooses one file in the native file picker, and the selected
file is copied into Lili's stable per-user data directory.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
import shutil
from typing import Any
from uuid import uuid4

from .local_data import app_data_dir, read_json, write_json_atomic


SUPPORTED_ALARM_EXTENSIONS = {".mp3", ".wav", ".m4a", ".aac"}


@dataclass(frozen=True)
class AlarmSound:
    sound_id: str
    display_name: str
    imported_path: str
    original_filename: str
    duration: float | None = None
    created_at: str = ""

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "AlarmSound | None":
        sound_id = str(value.get("sound_id") or "").strip()
        imported_path = str(value.get("imported_path") or "").strip()
        if not sound_id or not imported_path:
            return None
        try:
            duration = float(value["duration"]) if value.get("duration") is not None else None
        except (TypeError, ValueError):
            duration = None
        return cls(
            sound_id=sound_id[:80],
            display_name=str(value.get("display_name") or value.get("original_filename") or "自定义铃声")[:160],
            imported_path=imported_path,
            original_filename=str(value.get("original_filename") or "")[:240],
            duration=duration,
            created_at=str(value.get("created_at") or ""),
        )


class AlarmSoundLibrary:
    """Persist imported alarm sounds without broad filesystem permissions."""

    def __init__(self, base: str | Path | None = None, *, persist: bool = True) -> None:
        root = Path(base) if base is not None else app_data_dir()
        self.root = root
        self.path = root / "alarm_sounds.json"
        self.directory = root / "alarms"
        self.persist = bool(persist)
        raw = read_json(self.path, [])
        self._items: list[AlarmSound] = []
        if isinstance(raw, list):
            for value in raw:
                if isinstance(value, dict):
                    sound = AlarmSound.from_dict(value)
                    if sound is not None:
                        self._items.append(sound)

    @property
    def items(self) -> tuple[AlarmSound, ...]:
        return tuple(self._items)

    def _save(self) -> None:
        if self.persist:
            write_json_atomic(self.path, [asdict(item) for item in self._items])

    def get(self, sound_id: str | None) -> AlarmSound | None:
        key = str(sound_id or "")
        return next((item for item in self._items if item.sound_id == key), None)

    def display_name(self, sound_id: str | None) -> str:
        key = str(sound_id or "system")
        if key == "system":
            return "系统提示音"
        if key == "default":
            return "六毛默认铃声"
        sound = self.get(key)
        return sound.display_name if sound is not None else "系统提示音（自定义铃声不可用）"

    def resolve_path(self, sound_id: str | None) -> Path | None:
        sound = self.get(sound_id)
        if sound is None:
            return None
        path = Path(sound.imported_path)
        return path if path.is_file() else None

    def import_file(self, source: str | Path, *, display_name: str | None = None) -> AlarmSound:
        source_path = Path(source)
        if source_path.suffix.lower() not in SUPPORTED_ALARM_EXTENSIONS:
            raise ValueError("支持的铃声格式：MP3、WAV、M4A、AAC")
        if not source_path.is_file():
            raise FileNotFoundError(str(source_path))
        self.directory.mkdir(parents=True, exist_ok=True)
        sound_id = uuid4().hex
        target = self.directory / f"{sound_id}{source_path.suffix.lower()}"
        shutil.copy2(source_path, target)
        sound = AlarmSound(
            sound_id=sound_id,
            display_name=(display_name or source_path.stem).strip()[:160] or "自定义铃声",
            imported_path=str(target),
            original_filename=source_path.name[:240],
            created_at=datetime.now().astimezone().isoformat(),
        )
        self._items.append(sound)
        self._save()
        return sound

    def remove(self, sound_id: str) -> bool:
        sound = self.get(sound_id)
        if sound is None:
            return False
        self._items = [item for item in self._items if item.sound_id != str(sound_id)]
        try:
            Path(sound.imported_path).unlink(missing_ok=True)
        except OSError:
            pass
        self._save()
        return True
