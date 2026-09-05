from worker.training.prompt_rendering import render_chat_messages


def test_chat_messages_fall_back_when_tokenizer_has_no_template() -> None:
    class TokenizerWithoutTemplate:
        chat_template = None

        def apply_chat_template(self, *_args, **_kwargs):
            raise AssertionError("an unconfigured template must not be called")

    messages = [
        {"role": "user", "content": "Question"},
        {"role": "assistant", "content": "Answer"},
    ]

    assert render_chat_messages(messages, TokenizerWithoutTemplate(), add_generation_prompt=False) == (
        "user: Question\nassistant: Answer"
    )
    assert render_chat_messages(messages[:1], TokenizerWithoutTemplate(), add_generation_prompt=True) == (
        "user: Question\nassistant:"
    )


def test_chat_messages_use_configured_tokenizer_template() -> None:
    class TokenizerWithTemplate:
        chat_template = "configured"

        def apply_chat_template(self, messages, *, tokenize, add_generation_prompt):
            assert messages == [{"role": "user", "content": "Question"}]
            assert tokenize is False
            assert add_generation_prompt is True
            return "templated"

    assert (
        render_chat_messages(
            [{"role": "user", "content": "Question"}],
            TokenizerWithTemplate(),
            add_generation_prompt=True,
        )
        == "templated"
    )
