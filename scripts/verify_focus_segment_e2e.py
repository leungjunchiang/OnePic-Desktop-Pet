#!/usr/bin/env python3
"""Run one authenticated FocusSegment write/ACK/readback verification.

The fixed 2025 interval is outside current daily/weekly/monthly/yearly views.
Its deterministic ID makes repeated verification an upsert, not extra time.
No access or refresh token is printed or written to the report.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from onepic_desktop_pet.focus_analytics import AccountFocusStore
from onepic_desktop_pet.focus_segments import (
    FocusSegment,
    deterministic_focus_segment_id,
)
from onepic_desktop_pet.social import SocialClient, _session_user_id


BEIJING = timezone(timedelta(hours=8), "Asia/Shanghai")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()

    client = SocialClient(persist_tokens=True)
    user_id = _session_user_id(client)
    if not client.signed_in or not user_id:
        raise RuntimeError("本机当前没有可用的搭子自习室登录会话")

    start = datetime(2025, 9, 11, 3, 21, 0, tzinfo=BEIJING)
    end = start + timedelta(seconds=2)
    device_id = "codex-e2e-canonical-v1"
    session_id = "canonical-e2e-20250911"
    segment_id = deterministic_focus_segment_id(device_id, session_id, start, end)

    with tempfile.TemporaryDirectory(prefix="lili-focus-e2e-") as temporary:
        store = AccountFocusStore(
            path=Path(temporary) / "focus_analytics.json",
            now_provider=lambda: datetime.now(BEIJING),
            persist=True,
            device_id=device_id,
        )
        store.commit_focus_segment(
            FocusSegment(
                segment_id=segment_id,
                session_id=session_id,
                device_id=device_id,
                start_at=start,
                end_at=end,
                task="canonical-focus-e2e",
            ),
            source="e2e_verification",
            reason="e2e_verification",
        )
        upload = store.focus_segments_payload(limit=1)
        response = client.rpc(
            "lili_sync_focus_segments_delta_v2",
            {"p_segments": upload, "p_since": None},
        )
        accepted = {
            str(value)
            for value in (
                response.get("accepted_segment_ids", [])
                if isinstance(response, dict)
                else []
            )
        }
        if segment_id not in accepted:
            raise RuntimeError("服务端未返回该 Segment 的明确 ACK")
        store.acknowledge_focus_segments_upload(upload)
        if store.focus_segments_payload(limit=1):
            raise RuntimeError("ACK 后本地 Segment 仍被判定为待上传")

        fetched = client.rpc(
            "lili_sync_focus_segments_delta_v2",
            {"p_segments": [], "p_since": None},
        )
        remote_rows = fetched.get("segments", []) if isinstance(fetched, dict) else []
        readback = next(
            (
                row for row in remote_rows
                if isinstance(row, dict) and str(row.get("segment_id")) == segment_id
            ),
            None,
        )
        if readback is None:
            raise RuntimeError("重新拉取后未找到刚才写入的 Segment")
        projection = store.period_summary("day", start)

    report = {
        "verified_at": datetime.now(BEIJING).isoformat(),
        "user_id": user_id,
        "segment_id": segment_id,
        "session_id": session_id,
        "device_id": device_id,
        "start_at": start.isoformat(),
        "end_at": end.isoformat(),
        "duration_seconds": 2,
        "request_count": len(upload),
        "server_accepted": True,
        "client_ack_persisted": True,
        "readback_found": True,
        "local_day_projection_seconds": int(projection.get("total_seconds", 0) or 0),
        "current_periods_affected": False,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
