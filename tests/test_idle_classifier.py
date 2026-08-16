from onepic_desktop_pet.idle_classifier import IdleEvidence, classify_idle


def test_locked_or_sleeping_is_high_confidence_rest():
    result = classify_idle(IdleEvidence(app_name="winword.exe", locked=True))
    assert result.decision == "rest"
    assert result.confidence >= 0.95


def test_work_application_is_focus_without_a_popup():
    result = classify_idle(IdleEvidence(app_name="WINWORD.EXE", app_category="office"))
    assert result.decision == "focus"
    assert result.confidence >= 0.75


def test_music_defaults_to_rest():
    result = classify_idle(IdleEvidence(app_name="spotify.exe", app_category="music", media_playing=True))
    assert result.decision == "rest"
    assert result.confidence >= 0.9


def test_unknown_context_stays_low_confidence_and_defaults_to_rest():
    result = classify_idle(IdleEvidence(app_name="unknown.exe"))
    assert result.decision == "rest"
    assert result.confidence < 0.75


def test_saved_application_rule_overrides_detection():
    result = classify_idle(
        IdleEvidence(app_name="reader.exe", app_category="reading", user_rule="rest")
    )
    assert result.decision == "rest"
    assert result.confidence == 0.99

