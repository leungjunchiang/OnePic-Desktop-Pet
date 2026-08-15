"""Build the small content manifest attached to a GitHub Release."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote


CONTENT_ROOTS = ("assets", "config", "resources")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_manifest(
    root: Path,
    *,
    version: str,
    ref: str,
    repository: str,
    min_app_version: str | None = None,
) -> dict[str, object]:
    files: list[dict[str, object]] = []
    safe_ref = quote(ref, safe="/")
    for directory in CONTENT_ROOTS:
        base = root / directory
        if not base.is_dir():
            continue
        for path in sorted(item for item in base.rglob("*") if item.is_file()):
            relative = path.relative_to(root).as_posix()
            files.append(
                {
                    "path": relative,
                    "url": f"https://raw.githubusercontent.com/{repository}/{safe_ref}/{relative}",
                    "sha256": sha256(path),
                    "size": path.stat().st_size,
                }
            )
    return {
        "schema_version": 1,
        "content_version": version,
        # A content version is independent from the program version.  The
        # release workflow supplies the first updater-capable app version;
        # keeping the default open makes the helper safe for content-only
        # packs created outside the release workflow.
        "min_app_version": min_app_version or "0.0.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "files": files,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--version", required=True)
    parser.add_argument("--ref", required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--min-app-version", default=None)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    payload = build_manifest(
        args.root.resolve(),
        version=args.version,
        ref=args.ref,
        repository=args.repository,
        min_app_version=args.min_app_version,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

