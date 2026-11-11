"""Official SIWC inference: public Responses API, always streamed and stateless."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from deeptutor.services.chatgpt_auth import ChatGPTAuthError, get_chatgpt_auth_service
from deeptutor.services.chatgpt_auth.service import RESOURCE
from deeptutor.services.llm.exceptions import LLMProviderTransportError

from .base import LLMProvider, LLMResponse
from .openai_responses import (
    convert_messages,
    convert_tool_choice,
    convert_tools,
    iter_sse,
    parse_response_output,
)


class ChatGPTInferenceError(RuntimeError):
    """Retain upstream diagnostics without placing bearer credentials in errors."""

    def __init__(self, body: dict, status: int, request_id: str | None) -> None:
        if not isinstance(body, dict):
            body = {"detail": body}
        self.body, self.status_code, self.request_id = body, status, request_id
        error = body.get("error") or body.get("response", {}).get("error") or {}
        self.code = (
            error.get("code", "response_failed") if isinstance(error, dict) else "response_failed"
        )
        guidance = {
            "subscription_sharing_usage_limit_exceeded": "Manage app usage at https://chatgpt.com/settings/usage before trying again.",
            "subscription_sharing_user_not_eligible": "ChatGPT plan usage is unavailable for this account or workspace.",
            "subscription_sharing_unsupported_capability": "This request includes a capability unsupported by ChatGPT plan usage.",
        }.get(self.code, "")
        super().__init__(
            f"ChatGPT request failed (HTTP {status}, {self.code}). {guidance} Request ID: {request_id or 'unavailable'}"
        )


class OpenAIChatGPTProvider(LLMProvider):
    def __init__(self, default_model: str) -> None:
        super().__init__()
        self.default_model = default_model

    def get_default_model(self) -> str:
        return self.default_model

    @classmethod
    def _is_transient_error(
        cls,
        content: str | None,
        *,
        status_code: int | None = None,
        exception: Exception | None = None,
    ) -> bool:
        if "subscription_sharing_usage_limit_exceeded" in (content or ""):
            return False
        return super()._is_transient_error(content, status_code=status_code, exception=exception)

    async def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        model: str | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        return await self.chat_stream(messages, tools=tools, model=model, **kwargs)

    async def chat_stream(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        model: str | None = None,
        reasoning_effort: str | None = None,
        tool_choice: str | dict | None = None,
        on_content_delta: Callable[[str], Awaitable[None]] | None = None,
        on_reasoning_delta: Callable[[str], Awaitable[None]] | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        selected = model or self.default_model
        registration_id = None
        if selected.startswith("openai_chatgpt/"):
            _, registration_id, selected = selected.split("/", 2)
        instructions, items = convert_messages(messages)
        for item in items:
            if item.get("type") in {"function_call", "function_call_output"}:
                item["namespace"] = "deeptutor"
        body = {"model": selected, "input": items, "store": False, "stream": True}
        body["include"] = ["reasoning.encrypted_content"]
        response_format = kwargs.get("response_format")
        if isinstance(response_format, dict):
            if response_format.get("type") == "json_schema":
                body["text"] = {"format": {"type": "json_schema", **response_format["json_schema"]}}
            else:
                body["text"] = {"format": response_format}
        if instructions:
            body["instructions"] = instructions
        if tools:
            functions = convert_tools(tools)
            for tool in functions:
                if tool.get("type") == "function":
                    tool.setdefault("strict", False)
            body["tools"] = [
                {
                    "type": "namespace",
                    "name": "deeptutor",
                    "description": "DeepTutor learning and workspace tools.",
                    "tools": functions,
                }
            ]
        if tool_choice is not None:
            body["tool_choice"] = convert_tool_choice(tool_choice)
            if isinstance(body["tool_choice"], dict):
                body["tool_choice"]["namespace"] = "deeptutor"
        if reasoning_effort:
            body["reasoning"] = {"effort": reasoning_effort}
        # Do not forward arbitrary chat kwargs: SIWC has a narrower capability contract.
        try:
            service = get_chatgpt_auth_service()
            with service.inference_guard():
                token = await asyncio.to_thread(get_chatgpt_auth_service().token, registration_id)
                async with httpx.AsyncClient(
                    timeout=httpx.Timeout(300, connect=30), follow_redirects=False
                ) as client:
                    async with client.stream(
                        "POST",
                        f"{RESOURCE}/responses",
                        json=body,
                        headers={
                            "Authorization": f"Bearer {token['access_token']}",
                            "Accept": "text/event-stream",
                        },
                    ) as response:
                        request_id = response.headers.get("x-request-id") or response.headers.get(
                            "openai-request-id"
                        )
                        if not response.is_success:
                            await response.aread()
                            try:
                                error_body = response.json()
                            except ValueError:
                                error_body = {"detail": "Non-JSON OpenAI response"}
                            raise ChatGPTInferenceError(
                                error_body, response.status_code, request_id
                            )
                        async for event in iter_sse(response):
                            kind = event.get("type")
                            if kind == "response.output_text.delta" and on_content_delta:
                                await on_content_delta(event.get("delta", ""))
                            elif (
                                kind == "response.reasoning_summary_text.delta"
                                and on_reasoning_delta
                            ):
                                await on_reasoning_delta(event.get("delta", ""))
                            elif kind in {"error", "response.failed"}:
                                raise ChatGPTInferenceError(event, response.status_code, request_id)
                            elif kind == "response.incomplete":
                                return parse_response_output(event["response"])
                            elif kind == "response.completed":
                                return parse_response_output(event["response"])
                        raise ChatGPTInferenceError(
                            {"error": {"code": "stream_interrupted"}},
                            response.status_code,
                            request_id,
                        )
        except ChatGPTInferenceError as exc:
            return LLMResponse(
                content=str(exc),
                finish_reason="error",
                provider_specific_fields={
                    "openai_error": exc.body,
                    "http_status": exc.status_code,
                    "request_id": exc.request_id,
                    "error_code": exc.code,
                },
            )
        except ChatGPTAuthError as exc:
            return LLMResponse(content=str(exc), finish_reason="error")
        except httpx.HTTPError:
            raise LLMProviderTransportError("ChatGPT transport request failed.") from None
