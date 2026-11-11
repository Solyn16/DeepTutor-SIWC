"""Exercise the SIWC trust boundaries using signed synthetic identities, never real accounts."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import socket
import time
from urllib.parse import parse_qs, urlencode, urlsplit

from cryptography.hazmat.primitives.asymmetric import rsa
import httpx
import jwt
import pytest

from deeptutor.services.chatgpt_auth import ChatGPTAuthError, ChatGPTAuthService
from deeptutor.services.chatgpt_auth.service import DIRECT_SCOPE, ISSUER, RESOURCE
from deeptutor.services.chatgpt_auth.storage import ChatGPTStore
from deeptutor.services.llm.provider_core import openai_chatgpt_provider as provider_module


@pytest.fixture
def harness(tmp_path, monkeypatch):
    monkeypatch.delenv("DEEPTUTOR_SIWC_CALLBACK_LISTEN_HOST", raising=False)
    monkeypatch.delenv("DEEPTUTOR_SIWC_CALLBACK_PORT", raising=False)
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private.public_key()))
    public["kid"] = "synthetic-key"
    calls = []
    state = {
        "nonce": "nonce",
        "scope": f"openid {DIRECT_SCOPE}",
        "refresh_error": None,
        "claims": {},
        "refreshes": 0,
    }

    def signed(**overrides):
        claims = {
            "iss": ISSUER,
            "aud": "oaiapp_test",
            "sub": "subject-a",
            "iat": int(time.time()),
            "exp": int(time.time()) + 3600,
            "nonce": state["nonce"],
            "email": "synthetic@example.invalid",
        }
        claims.update(state["claims"])
        claims.update(overrides)
        return jwt.encode(claims, private, algorithm="RS256", headers={"kid": public["kid"]})

    def handler(request):
        calls.append(request)
        if request.url.path.endswith("openid-configuration"):
            return httpx.Response(
                200,
                json={
                    "issuer": ISSUER,
                    "jwks_uri": f"{ISSUER}/jwks",
                    "revocation_endpoint": f"{ISSUER}/revoke",
                },
            )
        if request.url.path == "/jwks":
            return httpx.Response(200, json={"keys": [public]})
        if request.url.path.endswith("/token"):
            form = parse_qs(request.content.decode())
            assert form["client_id"] == ["oaiapp_test"]
            assert form["resource"] == [RESOURCE]
            if form["grant_type"] == ["refresh_token"]:
                assert "scope" not in form
                state["refreshes"] += 1
                if state["refresh_error"]:
                    return httpx.Response(400, json={"error": state["refresh_error"]})
            else:
                assert form["code_verifier"] and form["redirect_uri"][0].startswith(
                    "http://127.0.0.1:"
                )
            return httpx.Response(
                200,
                json={
                    "id_token": signed(),
                    "access_token": "synthetic-access",
                    "refresh_token": f"synthetic-refresh-{state['refreshes']}",
                    "scope": state["scope"],
                    "token_type": "Bearer",
                    "expires_in": 3600,
                },
            )
        if request.url.path.endswith("/models"):
            assert request.headers["Authorization"] == "Bearer synthetic-access"
            return httpx.Response(
                200,
                json={
                    "models": [
                        {"slug": "model-b", "display_name": "B", "visibility": "list"},
                        {"slug": "hidden", "visibility": "hide"},
                        {"slug": "model-a", "display_name": "A", "visibility": "list"},
                    ]
                },
            )
        if request.url.path == "/revoke":
            return httpx.Response(200)
        raise AssertionError(str(request.url))

    service = ChatGPTAuthService(
        tmp_path, http=httpx.Client(transport=httpx.MockTransport(handler))
    )
    return service, state, calls, signed


async def login(harness, account=None, **callback):
    service, state, _, _ = harness
    started = await service.start_login(account)
    params = parse_qs(urlsplit(started["authorize_url"]).query)
    state["nonce"] = params["nonce"][0]
    query = {
        "state": params["state"][0],
        "code": "synthetic-code",
        "client_id": "oaiapp_test",
        **callback,
    }
    async with httpx.AsyncClient() as client:
        response = await client.get(params["redirect_uri"][0] + "?" + urlencode(query))
    return response, params


@pytest.mark.asyncio
async def test_registration_and_returning_signin_use_stable_host_and_issued_client(harness):
    service, _, calls, _ = harness
    response, params = await login(harness)
    assert response.status_code == 200
    assert params["client_id"] == ["dynamic_agent_client"]
    assert params["agent_name_hint"] == ["DeepTutor"]
    assert params["code_challenge_method"] == ["S256"]
    assert params["ext_agent_host_id"][0].startswith("urn:uuid:")
    assert service.status()["operation_state"] == "completed"
    account = service.status()["active_id"]
    _, returning = await login(harness, account, client_id="oaiapp_test")
    assert returning["client_id"] == ["oaiapp_test"]
    assert "agent_name_hint" not in returning
    assert returning["ext_agent_host_id"] == params["ext_agent_host_id"]
    assert returning["state"] != params["state"]
    assert "synthetic-access" not in json.dumps(service.status())
    assert "synthetic-refresh" not in json.dumps(service.status())
    assert len([r for r in calls if r.url.path.endswith("/token")]) == 2


@pytest.mark.asyncio
async def test_docker_listener_uses_fixed_port_and_keeps_loopback_redirect(harness, monkeypatch):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    monkeypatch.setenv("DEEPTUTOR_SIWC_CALLBACK_LISTEN_HOST", "0.0.0.0")
    monkeypatch.setenv("DEEPTUTOR_SIWC_CALLBACK_PORT", str(port))
    response, params = await login(harness)
    assert response.status_code == 200
    assert params["redirect_uri"] == [f"http://127.0.0.1:{port}/auth/callback"]
    assert harness[0].status()["operation_state"] == "completed"


@pytest.mark.parametrize(
    "host,port",
    [
        ("0.0.0.0", "0"),
        ("127.0.0.1", "-1"),
        ("127.0.0.1", "65536"),
        ("127.0.0.1", "invalid"),
        ("attacker.invalid", "1468"),
    ],
)
def test_invalid_callback_transport_settings_are_rejected(monkeypatch, host, port):
    from deeptutor.services.chatgpt_auth.service import callback_listener_settings

    monkeypatch.setenv("DEEPTUTOR_SIWC_CALLBACK_LISTEN_HOST", host)
    monkeypatch.setenv("DEEPTUTOR_SIWC_CALLBACK_PORT", port)
    with pytest.raises(ChatGPTAuthError) as exc:
        callback_listener_settings()
    assert exc.value.code == "invalid_callback_config"


@pytest.mark.asyncio
async def test_busy_fixed_callback_port_has_actionable_error(harness, monkeypatch):
    with socket.socket() as occupied:
        occupied.bind(("127.0.0.1", 0))
        occupied.listen()
        monkeypatch.setenv("DEEPTUTOR_SIWC_CALLBACK_PORT", str(occupied.getsockname()[1]))
        with pytest.raises(ChatGPTAuthError) as exc:
            await harness[0].start_login()
        assert exc.value.code == "callback_unavailable"
        assert harness[0].pending is None


@pytest.mark.asyncio
@pytest.mark.parametrize("callback", [{"state": "wrong"}, {"state": "中文"}])
async def test_invalid_state_does_not_consume_or_exchange(harness, callback):
    service, _, calls, _ = harness
    response, _ = await login(harness, **callback)
    assert response.status_code == 400
    assert service.status()["operation_state"] == "waiting"
    assert not any(r.url.path.endswith("/token") for r in calls)
    await service.cancel_login()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "override",
    [
        {"nonce": "wrong"},
        {"iss": "https://attacker.invalid"},
        {"aud": "other-client"},
        {"exp": 1},
        {"sub": ""},
    ],
)
async def test_identity_validation_rejects_invalid_claims(harness, override):
    service, state, _, _ = harness
    state["claims"] = override
    await login(harness)
    assert service.status()["error_code"] == "invalid_identity"
    assert not service.status()["accounts"][0]["connected"]


def test_signature_verification_rejects_forged_identity(harness):
    service, _, _, signed = harness
    original = signed()
    encoded, payload, signature = original.split(".")
    forged = f"{encoded}.{payload}.{('A' if signature[0] != 'A' else 'B') + signature[1:]}"
    with pytest.raises(ChatGPTAuthError, match="verification failed"):
        service._identity(forged, "oaiapp_test", "nonce")


@pytest.mark.asyncio
async def test_declined_plan_keeps_identity_but_blocks_inference(harness):
    service, state, _, _ = harness
    state["scope"] = "openid profile email"
    await login(harness)
    status = service.status()
    assert status["accounts"][0]["connected"]
    assert not status["accounts"][0]["plan_enabled"]
    with pytest.raises(ChatGPTAuthError) as exc:
        service.token()
    assert exc.value.code == "plan_disabled"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "callback, code",
    [
        ({"error": "access_denied"}, "access_denied"),
        ({"client_id": "dynamic_agent_client"}, "missing_client_id"),
    ],
)
async def test_declined_or_incomplete_registration(harness, callback, code):
    service, _, calls, _ = harness
    await login(harness, **callback)
    assert service.status()["error_code"] == code
    assert not any(r.url.path.endswith("/token") for r in calls)


@pytest.mark.asyncio
async def test_client_and_returning_identity_cannot_change(harness):
    service, state, _, _ = harness
    await login(harness)
    account = service.status()["active_id"]
    await login(harness, account, client_id="oaiapp_other")
    assert service.status()["error_code"] == "client_mismatch"
    state["claims"] = {"sub": "another-user"}
    await login(harness, account)
    assert service.status()["error_code"] == "account_mismatch"
    assert service.token(account)["access_token"] == "synthetic-access"


@pytest.mark.asyncio
async def test_refresh_is_serialized_and_rotated_and_logout_preserves_registration(harness):
    service, state, _, _ = harness
    await login(harness)
    account = service.status()["active_id"]
    with service.store.locked() as stored:
        stored["accounts"][account]["expires_at"] = 1
    with ThreadPoolExecutor(max_workers=4) as pool:
        tokens = list(pool.map(lambda _: service.token(account), range(4)))
    assert all(t["access_token"] == "synthetic-access" for t in tokens)
    assert state["refreshes"] == 1
    with service.store.locked() as stored:
        assert stored["accounts"][account]["refresh_token"] == "synthetic-refresh-1"
        host = stored["host_id"]
    result = service.logout(account)
    assert result["revocation_confirmed"]
    with service.store.locked() as stored:
        assert stored["host_id"] == host
        assert stored["accounts"][account]["client_id"] == "oaiapp_test"
        assert "id_token" not in stored["accounts"][account]


@pytest.mark.asyncio
async def test_terminal_refresh_error_clears_tokens(harness):
    service, state, _, _ = harness
    await login(harness)
    with service.store.locked() as stored:
        stored["accounts"][stored["active_id"]]["expires_at"] = 1
    state["refresh_error"] = "invalid_grant"
    with pytest.raises(ChatGPTAuthError):
        service.token()
    assert not service.status()["accounts"][0]["connected"]


@pytest.mark.asyncio
async def test_model_catalog_preserves_account_order_and_does_not_expose_hidden(harness):
    service, _, _, _ = harness
    await login(harness)
    assert [model["slug"] for model in service.models()] == ["model-b", "model-a"]


def test_store_is_restart_safe_and_encrypted_on_windows(tmp_path):
    store = ChatGPTStore(tmp_path)
    with store.locked() as stored:
        stored["accounts"]["a"] = {"access_token": "synthetic-private-token"}
        host = stored["host_id"]
    with ChatGPTStore(tmp_path).locked() as stored:
        assert stored["accounts"]["a"]["access_token"] == "synthetic-private-token"
        assert stored["host_id"] == host
    import os

    if os.name == "nt":
        assert "synthetic-private-token" not in store.path.read_text()


@pytest.mark.parametrize(
    "code,retry",
    [
        ("subscription_sharing_usage_limit_exceeded", False),
        ("subscription_sharing_usage_unavailable", True),
    ],
)
def test_plan_limits_do_not_trigger_automatic_retries(code, retry):
    provider = provider_module.OpenAIChatGPTProvider("model-a")
    error = provider_module.ChatGPTInferenceError(
        {"error": {"code": code}}, 429 if not retry else 503, "req-test"
    )
    assert provider._is_transient_error(str(error)) is retry


@pytest.mark.asyncio
async def test_publish_preserves_other_profiles_and_pins_each_registration(harness, tmp_path):
    from deeptutor.multi_user.model_access import is_owner_bound
    from deeptutor.services.config.model_catalog import ModelCatalogService

    service, state, _, _ = harness
    service.catalog = ModelCatalogService(tmp_path / "catalog.json")
    await login(harness)
    first_id = service.status()["active_id"]
    service.publish_models(first_id)
    state["claims"] = {"sub": "subject-b"}
    await login(harness)
    second_id = service.status()["active_id"]
    assert second_id != first_id
    service.publish_models(second_id)
    profiles = service.catalog.load()["services"]["llm"]["profiles"]
    profiles = [p for p in profiles if p["binding"] == "openai_chatgpt"]
    assert len(profiles) == 2
    assert all(is_owner_bound(p) for p in profiles)
    assert profiles[0]["models"][0]["model"].startswith(f"openai_chatgpt/{first_id}/")
    assert profiles[1]["models"][0]["model"].startswith(f"openai_chatgpt/{second_id}/")
    assert "synthetic-access" not in json.dumps(profiles)


@pytest.mark.asyncio
async def test_signout_waits_for_inference_and_can_resume(harness):
    service, _, _, _ = harness
    await login(harness)
    account = service.status()["active_id"]
    with service.inference_guard():
        with pytest.raises(ChatGPTAuthError) as exc:
            service.logout(account)
        assert exc.value.code == "operation_active"
    assert service.logout(account)["revocation_confirmed"]


@pytest.mark.asyncio
async def test_api_rejects_remote_and_get_mutations_before_resolving_credentials(monkeypatch):
    from fastapi import FastAPI

    from deeptutor.api.routers import settings
    import deeptutor.services.chatgpt_auth as auth

    monkeypatch.setattr(settings, "_require_codex_oauth_actor", lambda: None)
    monkeypatch.setattr(
        auth, "get_chatgpt_auth_service", lambda: pytest.fail("Must not resolve credentials")
    )
    app = FastAPI()
    app.include_router(settings.router, prefix="/api/settings")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=("10.0.0.2", 123)),
        base_url="http://127.0.0.1",
    ) as client:
        response = await client.post("/api/settings/providers/openai-chatgpt/start", json={})
        assert response.status_code == 403
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 123)),
        base_url="http://127.0.0.1",
    ) as client:
        response = await client.get("/api/settings/providers/openai-chatgpt/start")
        assert response.status_code == 405


def test_agentic_and_services_factories_both_build_siwc():
    from deeptutor.runtime.agentic.client import LLMClientConfig, _build_native_provider_adapter
    from deeptutor.services.llm.config import LLMConfig
    from deeptutor.services.llm.provider_factory import build_isolated_provider
    from deeptutor.services.provider_registry import find_by_name

    spec = find_by_name("openai_chatgpt")
    assert spec.is_oauth and spec.default_api_base == RESOURCE
    config = LLMClientConfig(
        binding="openai_chatgpt", model="model-a", api_key="", base_url=RESOURCE
    )
    adapter = _build_native_provider_adapter(config, spec)
    assert isinstance(adapter._provider, provider_module.OpenAIChatGPTProvider)
    provider = build_isolated_provider(
        LLMConfig(
            binding="openai_chatgpt",
            provider_name="openai_chatgpt",
            provider_mode="oauth",
            model="model-a",
            api_key="",
        )
    )
    assert isinstance(provider, provider_module.OpenAIChatGPTProvider)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "terminal, success", [("response.completed", True), ("response.failed", False), (None, False)]
)
async def test_responses_contract_and_terminal_handling(harness, monkeypatch, terminal, success):
    service, _, _, _ = harness
    await login(harness)
    monkeypatch.setattr(provider_module, "get_chatgpt_auth_service", lambda: service)
    actual_async_client = httpx.AsyncClient
    requests = []

    def infer(request):
        requests.append(request)
        payload = json.loads(request.content)
        assert str(request.url) == f"{RESOURCE}/responses"
        assert payload["store"] is False and payload["stream"] is True
        assert payload["tools"][0]["type"] == "namespace"
        assert payload["tools"][0]["name"] == "deeptutor"
        assert payload["instructions"] == "Teach clearly."
        assert "temperature" not in payload and "max_output_tokens" not in payload
        events = [{"type": "response.output_text.delta", "delta": "hello"}]
        if terminal:
            events.append(
                {
                    "type": terminal,
                    "response": {
                        "status": "completed" if success else "failed",
                        "output": [
                            {
                                "type": "message",
                                "content": [{"type": "output_text", "text": "hello"}],
                            }
                        ],
                        "error": None
                        if success
                        else {"code": "subscription_sharing_usage_limit_exceeded"},
                    },
                }
            )
        return httpx.Response(200, text="".join(f"data: {json.dumps(e)}\n\n" for e in events))

    monkeypatch.setattr(
        provider_module.httpx,
        "AsyncClient",
        lambda **kw: actual_async_client(transport=httpx.MockTransport(infer)),
    )
    provider = provider_module.OpenAIChatGPTProvider("model-a")
    deltas = []

    async def delta(text):
        deltas.append(text)

    result = await provider.chat_stream(
        [{"role": "system", "content": "Teach clearly."}, {"role": "user", "content": "Hello"}],
        tools=[
            {"type": "function", "function": {"name": "search", "parameters": {"type": "object"}}}
        ],
        temperature=0.4,
        max_output_tokens=12,
        on_content_delta=delta,
    )
    assert (result.finish_reason == "stop") is success
    assert deltas == ["hello"]
    assert len(requests) == 1
