"""Response contract validation of ModelInvocationService: tool-call and
JSON-schema responses are checked against the request contract."""

from __future__ import annotations

import json
from typing import Any

from agent.services.model_invocation_errors import LLMUnavailableError


class ModelInvocationResponseContractMixin:
    """Reject provider responses that violate the requested tool/schema contract."""

    @classmethod
    def _raise_response_contract_error(
        cls,
        payload: dict[str, Any],
        *,
        error_type: str,
        detail: str,
    ) -> None:
        metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        profile = [dict(item) for item in list(metadata.get("llm_call_profile") or []) if isinstance(item, dict)]
        if profile:
            profile[-1] = {
                **profile[-1],
                "success": False,
                "error_type": error_type,
                "error_message": str(detail or error_type)[:200],
            }
        else:
            profile.append(
                cls._build_llm_call_profile_entry(
                    name="chat_completions",
                    backend="response_validation",
                    provider=None,
                    model=None,
                    success=False,
                    started_at=None,
                    ended_at=None,
                    error_type=error_type,
                    error_message=str(detail or error_type)[:200],
                )
            )
        raise LLMUnavailableError(
            f"llm_{error_type}: {str(detail or error_type)[:200]}",
            llm_call_profile=profile,
            terminal_reason=error_type,
        )

    @classmethod
    def _validate_tool_response(cls, payload: dict[str, Any], tools: list | None) -> None:
        normalized_tools = cls._normalize_openai_tools(tools)
        allowed_tools = {
            item["function"]["name"]: item["function"].get("parameters") or {"type": "object", "properties": {}}
            for item in normalized_tools
            if isinstance(item.get("function"), dict)
        }
        if not allowed_tools:
            return

        _, message = cls._response_message(payload)
        native_calls = message.get("tool_calls")
        if isinstance(native_calls, list) and native_calls:
            for raw_call in native_calls:
                if not isinstance(raw_call, dict):
                    cls._raise_response_contract_error(
                        payload,
                        error_type="tool_args_invalid",
                        detail="tool_call_must_be_object",
                    )
                function = raw_call.get("function")
                if not isinstance(function, dict):
                    cls._raise_response_contract_error(
                        payload,
                        error_type="tool_args_invalid",
                        detail="tool_call_function_missing",
                    )
                tool_name = str(function.get("name") or "").strip()
                if tool_name not in allowed_tools:
                    cls._raise_response_contract_error(
                        payload,
                        error_type="tool_not_allowed",
                        detail="tool_name_not_in_request_contract",
                    )
                raw_args = function.get("arguments", {})
                try:
                    args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                except (TypeError, ValueError):
                    cls._raise_response_contract_error(
                        payload,
                        error_type="tool_args_invalid",
                        detail="tool_arguments_are_not_valid_json",
                    )
                if not isinstance(args, dict):
                    cls._raise_response_contract_error(
                        payload,
                        error_type="tool_args_invalid",
                        detail="tool_arguments_must_be_object",
                    )
                try:
                    import jsonschema

                    jsonschema.validate(instance=args, schema=allowed_tools[tool_name])
                except ImportError:
                    pass
                except Exception:
                    cls._raise_response_contract_error(
                        payload,
                        error_type="tool_args_invalid",
                        detail="tool_arguments_failed_schema_validation",
                    )
            return

        metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        call_profile = [item for item in list(metadata.get("llm_call_profile") or []) if isinstance(item, dict)]
        tool_mode = str((call_profile[-1] if call_profile else {}).get("tool_calling_mode") or "").strip()
        # Native-tool profiles may intentionally answer with structured content,
        # which the strategy normalizer still handles for compatibility.
        if tool_mode not in {"prompt_json", "both"}:
            return

        try:
            selection = json.loads(str(message.get("content") or ""))
        except (TypeError, ValueError):
            cls._raise_response_contract_error(
                payload,
                error_type="tool_args_invalid",
                detail="prompt_json_tool_selection_is_not_valid_json",
            )
        if not isinstance(selection, dict):
            cls._raise_response_contract_error(
                payload,
                error_type="tool_args_invalid",
                detail="prompt_json_tool_selection_must_be_object",
            )
        tool_name = str(selection.get("tool") or "").strip()
        if tool_name not in allowed_tools:
            cls._raise_response_contract_error(
                payload,
                error_type="tool_not_allowed",
                detail="prompt_json_tool_name_not_in_request_contract",
            )
        args = selection.get("args")
        if not isinstance(args, dict):
            cls._raise_response_contract_error(
                payload,
                error_type="tool_args_invalid",
                detail="prompt_json_tool_args_must_be_object",
            )
        try:
            import jsonschema

            jsonschema.validate(instance=args, schema=allowed_tools[tool_name])
        except ImportError:
            pass
        except Exception:
            cls._raise_response_contract_error(
                payload,
                error_type="tool_args_invalid",
                detail="prompt_json_tool_args_failed_schema_validation",
            )

    @classmethod
    def _validate_json_schema_response(
        cls,
        payload: dict[str, Any],
        *,
        json_schema: dict[str, Any],
        allow_format_repair: bool,
    ) -> None:
        _, message = cls._response_message(payload)
        from agent.services.structured_output_service import StructuredOutputService

        structured = StructuredOutputService(max_repair_attempts=1 if allow_format_repair else 0).validate_json(
            str(message.get("content") or ""),
            json_schema,
            allow_format_repair=allow_format_repair,
        )
        if structured.valid:
            return
        issue_codes = [
            str((issue.as_dict() if hasattr(issue, "as_dict") else {}).get("reason_code") or "").strip()
            for issue in list(structured.issues or [])[:4]
        ]
        detail = "schema_validation_failed"
        normalized_codes = [code for code in issue_codes if code]
        if normalized_codes:
            detail = f"{detail}:{','.join(normalized_codes)}"
        cls._raise_response_contract_error(
            payload,
            error_type="schema_validation_failed",
            detail=detail,
        )
