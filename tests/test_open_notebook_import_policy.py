from agent.sources.open_notebook_import_policy import OpenNotebookImportPolicy


def test_default_policy_allows_sources_but_not_chat_sessions():
    policy = OpenNotebookImportPolicy()
    assert policy.evaluate_section("sources").allowed
    assert policy.evaluate_section("notes").allowed
    assert policy.evaluate_section("source_insights").allowed
    chat = policy.evaluate_section("chat_sessions")
    assert not chat.allowed
    assert chat.reason_code == "chat_sessions_import_disabled"


def test_sections_can_be_disabled_individually():
    policy = OpenNotebookImportPolicy(allow_notes=False)
    assert not policy.evaluate_section("notes").allowed
    assert policy.evaluate_section("sources").allowed


def test_unknown_section_is_rejected():
    decision = OpenNotebookImportPolicy().evaluate_section("surprise")
    assert not decision.allowed
    assert decision.reason_code == "unknown_section"


def test_record_with_secret_like_field_is_blocked():
    decision = OpenNotebookImportPolicy().evaluate_record(
        {"title": "ok", "api_key": "value"}, section="sources"
    )
    assert not decision.allowed
    assert decision.reason_code == "secret_like_field_blocked"


def test_record_with_secret_like_value_is_blocked():
    decision = OpenNotebookImportPolicy().evaluate_record(
        {"title": "ok", "full_text": "contains sk-abcdef1234567890 token"}, section="sources"
    )
    assert not decision.allowed
    assert decision.reason_code == "secret_like_value_blocked"


def test_allowed_record_defaults_to_local_only():
    decision = OpenNotebookImportPolicy().evaluate_record(
        {"title": "ok", "full_text": "clean", "metadata": {"reading_status": "done"}},
        section="sources",
    )
    assert decision.allowed
    assert decision.sanitized_metadata["llm_scope"] == "local_only"
    assert decision.sanitized_metadata["reading_status"] == "done"


def test_sharing_approved_record_is_not_forced_local_only():
    decision = OpenNotebookImportPolicy().evaluate_record(
        {"title": "ok", "full_text": "clean", "metadata": {"sharing_approved": True}},
        section="sources",
    )
    assert decision.allowed
    assert "llm_scope" not in decision.sanitized_metadata


def test_metadata_secrets_are_redacted_not_blocking():
    decision = OpenNotebookImportPolicy().evaluate_record(
        {"title": "ok", "full_text": "clean", "metadata": {"token": "abc", "note": "fine"}},
        section="sources",
    )
    assert decision.allowed
    assert decision.sanitized_metadata["token"] == "[REDACTED]"
    assert decision.redacted_fields == 1


def test_chat_session_record_is_blocked_by_default():
    decision = OpenNotebookImportPolicy().evaluate_record({"id": "chat-1"}, section="chat_sessions")
    assert not decision.allowed
    assert decision.reason_code == "chat_sessions_import_disabled"
