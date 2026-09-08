from onepic_desktop_pet.focus_sync_protocol import (
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
