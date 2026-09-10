from onepic_desktop_pet.focus_sync_protocol import (
    canonicalize_focus_upload_segments,
    is_valid_delta_cursor,
    validate_focus_delta_response,
)


CURSOR = '{"updated_at":"2026-09-08T08:00:00+00:00","segment_id":"s-1"}'


def _response(**overrides):
    result = {
        "segments": [],
        "full_sync": False,
        "next_cursor": CURSOR,
        "has_more": False,
        "requested_count": 1,
        "accepted_count": 1,
        "accepted_segment_ids": ["s-1"],
    }
    result.update(overrides)
    return result


def test_composite_cursor_is_valid_and_arbitrary_text_is_not():
    assert is_valid_delta_cursor(CURSOR)
    assert is_valid_delta_cursor("2026-09-08T08:00:00+00:00")
    assert not is_valid_delta_cursor("cursor-before")


def test_delta_response_requires_exact_ack_counts_and_ids():
    assert validate_focus_delta_response(_response(), [{"segment_id": "s-1"}])[0]
    assert not validate_focus_delta_response(
        _response(accepted_count=0), [{"segment_id": "s-1"}]
    )[0]
    assert not validate_focus_delta_response(
        _response(accepted_segment_ids=[]), [{"segment_id": "s-1"}]
    )[0]


def test_empty_delta_cannot_advance_the_previous_cursor():
    ok, reason, _accepted = validate_focus_delta_response(
        _response(requested_count=0, accepted_count=0, accepted_segment_ids=[]),
        [],
        cursor_before=CURSOR,
    )
    assert ok is True
    assert reason == ""

    invalid, reason, _accepted = validate_focus_delta_response(
        _response(
            requested_count=0,
            accepted_count=0,
            accepted_segment_ids=[],
            next_cursor='{"updated_at":"2026-09-08T09:00:00+00:00","segment_id":"s-2"}',
        ),
        [],
        cursor_before=CURSOR,
    )
    assert invalid is False
    assert reason == "empty_delta_cursor_advanced"


def _segment_row(segment_id: str, *, end_at: str = "2026-09-08T09:00:00+08:00"):
    return {
        "segment_id": segment_id,
        "session_id": "session-a",
        "device_id": "device-a",
        "start_at": "2026-09-08T08:00:00+08:00",
        "end_at": end_at,
        "completed": False,
        "quality": 50,
        "task": "focus",
        "interruptions": 0,
    }


def test_upload_canonicalization_deduplicates_identical_rows_and_keeps_provenance():
    row = _segment_row("same")
    rows, diagnostics = canonicalize_focus_upload_segments(
        [row, dict(row)],
        source_entries=[
            {"segment_id": "same", "source": "canonical_focus_store"},
            {"segment_id": "same", "source": "wal_recovery"},
        ],
    )

    assert diagnostics["ok"] is True
    assert diagnostics["duplicate_segment_ids"] == ["same"]
    assert diagnostics["conflict_segment_ids"] == []
    assert len(rows) == 1
    assert len({item["segment_id"] for item in rows}) == len(rows)
    assert {item["source"] for item in diagnostics["groups"][0]["records"]} == {
        "canonical_focus_store",
        "wal_recovery",
    }
    assert diagnostics["groups"][0]["records"][0]["hash"] == diagnostics[
        "groups"
    ][0]["records"][1]["hash"]


def test_upload_canonicalization_blocks_same_id_conflicting_rows():
    rows, diagnostics = canonicalize_focus_upload_segments(
        [_segment_row("conflict"), _segment_row("conflict", end_at="2026-09-08T09:01:00+08:00")],
        source_entries=["canonical_focus_store", "pending_upload"],
    )

    assert rows == []
    assert diagnostics["ok"] is False
    assert diagnostics["error"] == "duplicate_focus_segment_id_conflict"
    assert diagnostics["conflict_segment_ids"] == ["conflict"]
    records = diagnostics["groups"][0]["records"]
    assert records[0]["hash"] != records[1]["hash"]
    assert records[0]["payload"] != records[1]["payload"]
