"""Deterministic framing of already Hub-verified untrusted tool material."""


def render_tool_result(response):
    source = response["sources"][0]["source_id"]
    return (f"[untrusted Hub tool {response['tool']}; source {source}]\n{response['text']}")[:6000]


def output_budget(backend, request):
    value = request.get("max_tokens") if backend == "openai" else request.get("options", {}).get("num_predict")
    if type(value) is not int or not 1 <= value <= 128:
        raise ValueError("meet_companion_model_output_budget_invalid")
    return value


def validate_model_result(backend, request, result):
    if not isinstance(result, dict) or result.get("model", request["model"]) != request["model"]:
        raise ValueError("meet_companion_model_result_invalid")
    if backend == "ollama":
        if result.get("done") is not True:
            raise ValueError("meet_companion_model_result_incomplete")
        message = result.get("message")
        incoming, outgoing = result.get("prompt_eval_count"), result.get("eval_count")
    elif backend == "openai":
        choices, usage = result.get("choices"), result.get("usage")
        if (not isinstance(choices, list) or not choices or not isinstance(choices[0], dict)
                or not isinstance(usage, dict)):
            raise ValueError("meet_companion_model_result_invalid")
        message = choices[0].get("message")
        incoming, outgoing = usage.get("prompt_tokens"), usage.get("completion_tokens")
    else:
        raise ValueError("meet_companion_model_backend_invalid")
    if (not isinstance(message, dict) or message.get("role", "assistant") != "assistant"
            or type(incoming) is not int or not 1 <= incoming <= 131_072
            or type(outgoing) is not int or not 1 <= outgoing <= output_budget(backend, request)):
        raise ValueError("meet_companion_model_result_invalid")
    content, calls = message.get("content"), message.get("tool_calls")
    if (content is not None and not isinstance(content, str)) or (calls is not None and not isinstance(calls, list)):
        raise ValueError("meet_companion_model_result_invalid")
    if isinstance(calls, list) and (len(calls) > 4 or any(
        not isinstance(call, dict) or not isinstance(call.get("function"), dict)
        or not isinstance(call["function"].get("name"), str) or not call["function"]["name"]
        or not isinstance(call["function"].get("arguments"), (dict, str)) for call in calls
    )):
        raise ValueError("meet_companion_model_tool_proposal_invalid")
    if not (isinstance(content, str) and content.strip()) and not (isinstance(calls, list) and 1 <= len(calls) <= 4):
        raise ValueError("meet_companion_model_result_empty")
    return result
