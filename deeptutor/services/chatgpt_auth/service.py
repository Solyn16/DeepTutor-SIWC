"""SIWC dynamic public-client registration, verified identity and renewable sessions.

Contract: https://developers.openai.com/siwc/token-sharing-open-source/sign-in
All credentials stay in the owner's backend storage, separate from Codex login.
"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from dataclasses import dataclass, field
import hashlib
import os
from pathlib import Path
import secrets
import threading
import time
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit
import uuid

import httpx
import jwt

from deeptutor.services.codex_auth.oauth import generate_pkce, oauth_state_matches

from .storage import ChatGPTStore

ISSUER = "https://auth.openai.com"
RESOURCE = "https://api.openai.com/v1"
SCOPES = "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct"
DIRECT_SCOPE = "chatgpt.tokens.use.direct"
TERMINAL_REFRESH_ERRORS = {
    "invalid_grant",
    "invalid_refresh_token",
    "token_expired",
    "refresh_token_expired",
    "refresh_token_invalidated",
    "refresh_token_reused",
}


def callback_listener_settings() -> tuple[str, int]:
    """Docker binds inside its network namespace; the redirect still uses loopback."""
    host = os.environ.get("DEEPTUTOR_SIWC_CALLBACK_LISTEN_HOST", "127.0.0.1")
    try:
        port = int(os.environ.get("DEEPTUTOR_SIWC_CALLBACK_PORT", "0"))
    except ValueError:
        port = -1
    if (
        host not in {"127.0.0.1", "0.0.0.0"}
        or not 0 <= port <= 65535
        or (host == "0.0.0.0" and port == 0)
    ):
        raise ChatGPTAuthError(
            "invalid_callback_config",
            "Use a valid fixed SIWC callback port when publishing a Docker callback listener.",
            500,
        )
    return host, port


class ChatGPTAuthError(RuntimeError):
    def __init__(self, code: str, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.code, self.status = code, status


@dataclass(repr=False)
class LoginAttempt:
    state: str
    nonce: str
    verifier: str
    registration_id: str
    client_id: str
    subject: str | None
    redirect_uri: str
    deadline: float
    server: asyncio.Server | None = None
    timer: asyncio.Task | None = field(default=None)


class ChatGPTAuthService:
    def __init__(
        self, secrets_root: Path, catalog: Any = None, http: httpx.Client | None = None
    ) -> None:
        self.store = ChatGPTStore(secrets_root)
        self.catalog = catalog
        self.http = http
        self.pending: LoginAttempt | None = None
        self.operation_state = "idle"
        self.error_code: str | None = None
        self._login_lock = asyncio.Lock()
        self._activity_lock = threading.Lock()
        self._inferences = 0
        self._signing_out = False

    @contextmanager
    def inference_guard(self):
        with self._activity_lock:
            if self._signing_out:
                raise ChatGPTAuthError(
                    "signing_out", "ChatGPT is signing out. Try again afterward.", 409
                )
            self._inferences += 1
        try:
            yield
        finally:
            with self._activity_lock:
                self._inferences -= 1

    def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        try:
            if self.http is not None:
                response = self.http.request(method, url, **kwargs)
            else:
                with httpx.Client(timeout=30, follow_redirects=False) as client:
                    response = client.request(method, url, **kwargs)
        except httpx.HTTPError:
            raise ChatGPTAuthError(
                "network_error", "Cannot reach OpenAI. Try again later.", 503
            ) from None
        if not response.is_success:
            # Never expose token-endpoint bodies, request URLs or credentials.
            code = "openai_error"
            try:
                error = response.json().get("error")
                candidate = error.get("code") if isinstance(error, dict) else error
                if isinstance(candidate, str) and candidate.replace("_", "").isalnum():
                    code = candidate[:100]
            except (ValueError, AttributeError):
                pass
            raise ChatGPTAuthError(
                code,
                f"OpenAI request failed (HTTP {response.status_code}, {code}).",
                response.status_code,
            )
        return response

    def _metadata(self) -> dict:
        metadata = self._request("GET", f"{ISSUER}/.well-known/openid-configuration").json()
        if metadata.get("issuer") != ISSUER:
            raise ChatGPTAuthError("invalid_issuer", "OpenAI discovery returned an invalid issuer.")
        # Discovery cannot redirect credentials or JWT key lookup off the trusted issuer.
        for key in ("jwks_uri", "revocation_endpoint"):
            endpoint = urlsplit(metadata.get(key, ""))
            if (endpoint.scheme, endpoint.hostname, endpoint.port) != (
                "https",
                "auth.openai.com",
                None,
            ):
                raise ChatGPTAuthError(
                    "invalid_discovery", "OpenAI discovery returned an invalid endpoint."
                )
        return metadata

    def _identity(self, token: str, client_id: str, nonce: str | None = None) -> dict:
        try:
            metadata = self._metadata()
            jwks = self._request("GET", metadata["jwks_uri"]).json()
            header = jwt.get_unverified_header(token)
            if header.get("alg") not in {"RS256", "ES256"}:
                raise ValueError
            key_data = next(key for key in jwks["keys"] if key.get("kid") == header.get("kid"))
            key = jwt.PyJWK.from_dict(key_data, algorithm=header["alg"])
            claims = jwt.decode(
                token,
                key.key,
                algorithms=[header["alg"]],
                audience=client_id,
                issuer=ISSUER,
                leeway=5,
                options={"require": ["iss", "aud", "sub", "exp", "iat"]},
            )
            if not isinstance(claims["sub"], str) or not claims["sub"]:
                raise ValueError
            if nonce is not None and claims.get("nonce") != nonce:
                raise ValueError
            if claims.get("azp", client_id) != client_id:
                raise ValueError
            return claims
        except (jwt.PyJWTError, ValueError, KeyError, StopIteration, TypeError):
            raise ChatGPTAuthError(
                "invalid_identity", "OpenAI identity verification failed.", 401
            ) from None

    @staticmethod
    def _tokens(payload: dict, previous: dict) -> dict:
        scopes = payload.get("scope", " ".join(previous.get("scopes", [])))
        expires = payload.get("expires_in", 3600)
        if not isinstance(scopes, str) or type(expires) is not int or expires <= 0:
            raise ChatGPTAuthError(
                "invalid_token_response", "OpenAI returned invalid token metadata."
            )
        result = {**previous, "scopes": scopes.split(), "expires_at": int(time.time()) + expires}
        for name in ("access_token", "refresh_token", "id_token"):
            if name in payload:
                if not isinstance(payload[name], str) or not payload[name]:
                    raise ChatGPTAuthError(
                        "invalid_token_response", "OpenAI returned an invalid token."
                    )
                result[name] = payload[name]
        if payload.get("token_type", "Bearer").lower() != "bearer":
            raise ChatGPTAuthError(
                "invalid_token_response", "OpenAI returned an unsupported token type."
            )
        if DIRECT_SCOPE in result["scopes"] and not result.get("access_token"):
            raise ChatGPTAuthError("invalid_token_response", "OpenAI returned no access token.")
        return result

    def status(self) -> dict:
        with self.store.locked() as stored:
            accounts = [
                {
                    "id": key,
                    "label": account.get("email") or f"Account {index + 1}",
                    "client_id": account["client_id"],
                    "connected": bool(account.get("id_token")),
                    "plan_enabled": bool(account.get("access_token"))
                    and DIRECT_SCOPE in account.get("scopes", []),
                }
                for index, (key, account) in enumerate(stored["accounts"].items())
            ]
            return {
                "accounts": accounts,
                "active_id": stored["active_id"],
                "operation_state": self.operation_state,
                "error_code": self.error_code,
            }

    async def start_login(self, registration_id: str | None = None) -> dict:
        async with self._login_lock:
            if self.pending:
                raise ChatGPTAuthError(
                    "login_in_progress", "Complete or cancel the current sign-in.", 409
                )
            with self.store.locked() as stored:
                account = stored["accounts"].get(registration_id) if registration_id else None
                if registration_id and account is None:
                    raise ChatGPTAuthError(
                        "unknown_account", "Choose a saved ChatGPT account.", 404
                    )
                host_id = stored["host_id"]
            pkce = generate_pkce()
            attempt = LoginAttempt(
                secrets.token_urlsafe(32),
                secrets.token_urlsafe(32),
                pkce.verifier,
                registration_id or uuid.uuid4().hex,
                account["client_id"] if account else "dynamic_agent_client",
                account.get("subject") if account else None,
                "",
                time.time() + 600,
            )

            async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
                message, status = "Invalid sign-in callback.", "400 Bad Request"
                try:
                    raw = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 3)
                    method, target, _ = raw.split(b"\r\n", 1)[0].decode("ascii").split(" ", 2)
                    parsed = urlsplit(target)
                    query = parse_qs(parsed.query, keep_blank_values=True)
                    if (
                        method == "GET"
                        and parsed.path == "/auth/callback"
                        and all(len(value) == 1 for value in query.values())
                        and oauth_state_matches(query.get("state", [""])[0], attempt.state)
                    ):
                        await self._finish(attempt, {key: value[0] for key, value in query.items()})
                        status, message = (
                            "200 OK",
                            "Sign-in processed. Return to DeepTutor for the result.",
                        )
                except (
                    ValueError,
                    UnicodeError,
                    asyncio.IncompleteReadError,
                    asyncio.LimitOverrunError,
                    TimeoutError,
                    ChatGPTAuthError,
                ):
                    pass
                finally:
                    body = (
                        f"<!doctype html><title>DeepTutor ChatGPT</title><p>{message}</p>".encode()
                    )
                    writer.write(
                        (
                            f"HTTP/1.1 {status}\r\nContent-Type: text/html; charset=utf-8\r\n"
                            f"Content-Length: {len(body)}\r\nCache-Control: no-store\r\n"
                            "Referrer-Policy: no-referrer\r\nConnection: close\r\n\r\n"
                        ).encode()
                        + body
                    )
                    try:
                        await writer.drain()
                    finally:
                        writer.close()
                        await writer.wait_closed()

            # The browser always receives an IPv4 loopback URI. Docker can bind a
            # fixed port internally and publish that port on the host's loopback.
            listen_host, listen_port = callback_listener_settings()
            try:
                attempt.server = await asyncio.start_server(handle, listen_host, listen_port)
            except OSError:
                raise ChatGPTAuthError(
                    "callback_unavailable",
                    "The SIWC callback port is unavailable. Free it or select another port.",
                    503,
                ) from None
            port = attempt.server.sockets[0].getsockname()[1]
            attempt.redirect_uri = f"http://127.0.0.1:{port}/auth/callback"
            self.pending, self.operation_state, self.error_code = attempt, "waiting", None
            params = {
                "client_id": attempt.client_id,
                "ext_agent_host_id": host_id,
                "response_type": "code",
                "redirect_uri": attempt.redirect_uri,
                "scope": SCOPES,
                "resource": RESOURCE,
                "state": attempt.state,
                "nonce": attempt.nonce,
                "code_challenge_method": "S256",
                "code_challenge": pkce.challenge,
            }
            if account is None:
                params["agent_name_hint"] = "DeepTutor"
            # Hints are optional. Omitting them keeps ID tokens out of frontend/API URLs.
            attempt.timer = asyncio.create_task(self._expire(attempt))
            return {
                "authorize_url": f"{ISSUER}/api/accounts/authorize?{urlencode(params)}",
                "expires_in": 600,
            }

    async def _expire(self, attempt: LoginAttempt) -> None:
        await asyncio.sleep(600)
        if self.pending is attempt and self.operation_state == "waiting":
            await self.cancel_login()
            self.operation_state = "expired"

    async def cancel_login(self) -> dict:
        if self.operation_state == "exchanging":
            raise ChatGPTAuthError(
                "login_in_progress", "Sign-in is completing. Wait for the result.", 409
            )
        attempt, self.pending = self.pending, None
        if attempt:
            if attempt.timer and attempt.timer is not asyncio.current_task():
                attempt.timer.cancel()
            attempt.server.close()
            await attempt.server.wait_closed()
        self.operation_state = "cancelled"
        return self.status()

    async def _finish(self, attempt: LoginAttempt, query: dict) -> None:
        if (
            self.pending is not attempt
            or self.operation_state != "waiting"
            or time.time() >= attempt.deadline
        ):
            raise ChatGPTAuthError("invalid_callback", "This sign-in attempt is no longer active.")
        # Consumed before the first await: duplicate callbacks cannot exchange a code twice.
        self.operation_state = "exchanging"
        attempt.server.close()
        attempt.timer.cancel()
        try:
            if query.get("error"):
                raise ChatGPTAuthError("access_denied", "ChatGPT authorization was declined.")
            issued = query.get("client_id", attempt.client_id)
            if not issued or issued == "dynamic_agent_client" or len(issued) > 256:
                raise ChatGPTAuthError("missing_client_id", "OpenAI did not issue a client ID.")
            if attempt.client_id != "dynamic_agent_client" and issued != attempt.client_id:
                raise ChatGPTAuthError("client_mismatch", "OpenAI returned a different client ID.")
            if not query.get("code"):
                raise ChatGPTAuthError("missing_code", "OpenAI returned no authorization code.")
            # Retain issued registration even if code exchange fails (e.g. invalid_grant).
            with self.store.locked() as stored:
                stored["accounts"].setdefault(attempt.registration_id, {"client_id": issued})
            await asyncio.to_thread(self._exchange, attempt, issued, query["code"])
            self.operation_state = "completed"
        except ChatGPTAuthError as exc:
            self.operation_state, self.error_code = "failed", exc.code
        except Exception:
            self.operation_state, self.error_code = "failed", "sign_in_failed"
        finally:
            self.pending = None
            # Python 3.13 waits for existing client connections here. The callback
            # handler must send its reply and close the writer before that can finish.

    def _exchange(self, attempt: LoginAttempt, client_id: str, code: str) -> None:
        payload = self._request(
            "POST",
            f"{ISSUER}/api/accounts/oauth/token",
            data={
                "grant_type": "authorization_code",
                "client_id": client_id,
                "code": code,
                "code_verifier": attempt.verifier,
                "redirect_uri": attempt.redirect_uri,
                "resource": RESOURCE,
            },
        ).json()
        identity = self._identity(payload.get("id_token", ""), client_id, attempt.nonce)
        if attempt.subject and identity["sub"] != attempt.subject:
            raise ChatGPTAuthError(
                "account_mismatch", "Sign in with the selected ChatGPT account.", 401
            )
        account = self._tokens(
            payload,
            {
                "client_id": client_id,
                "subject": identity["sub"],
                "issuer": ISSUER,
                "email": identity.get("email", ""),
            },
        )
        with self.store.locked() as stored:
            stored["accounts"][attempt.registration_id] = account
            stored["active_id"] = attempt.registration_id

    def token(self, registration_id: str | None = None) -> dict:
        # File lock spans refresh network IO in a worker thread, serializing rotating tokens
        # across processes as well as concurrent async model calls.
        error = None
        result = None
        with self.store.locked() as stored:
            registration_id = registration_id or stored["active_id"]
            account = stored["accounts"].get(registration_id, {})
            if not account.get("id_token"):
                raise ChatGPTAuthError(
                    "authentication_required", "Continue with ChatGPT in Settings first.", 401
                )
            if DIRECT_SCOPE not in account.get("scopes", []):
                raise ChatGPTAuthError(
                    "plan_disabled", "Authorize ChatGPT plan usage before making AI requests.", 403
                )
            if account.get("expires_at", 0) <= time.time() + 60:
                if not account.get("refresh_token"):
                    raise ChatGPTAuthError(
                        "authentication_required", "Sign in with ChatGPT again.", 401
                    )
                try:
                    payload = self._request(
                        "POST",
                        f"{ISSUER}/api/accounts/oauth/token",
                        data={
                            "grant_type": "refresh_token",
                            "client_id": account["client_id"],
                            "refresh_token": account["refresh_token"],
                            "resource": RESOURCE,
                        },
                    ).json()
                    if payload.get("id_token"):
                        identity = self._identity(payload["id_token"], account["client_id"])
                        if identity["sub"] != account["subject"]:
                            raise ChatGPTAuthError(
                                "account_mismatch",
                                "ChatGPT refresh returned another identity.",
                                401,
                            )
                    account = self._tokens(payload, account)
                    stored["accounts"][registration_id] = account
                except ChatGPTAuthError as exc:
                    if exc.code in TERMINAL_REFRESH_ERRORS:
                        for name in ("access_token", "refresh_token", "id_token"):
                            account.pop(name, None)
                    error = exc
            if error is None:
                if DIRECT_SCOPE not in account.get("scopes", []):
                    error = ChatGPTAuthError(
                        "plan_disabled", "ChatGPT plan usage is disabled.", 403
                    )
                else:
                    result = {
                        "registration_id": registration_id,
                        "access_token": account["access_token"],
                    }
        if error:
            raise error
        return result

    def models(self, registration_id: str | None = None) -> list[dict]:
        token = self.token(registration_id)
        payload = self._request(
            "GET",
            f"{RESOURCE}/models",
            headers={"Authorization": f"Bearer {token['access_token']}"},
        ).json()
        if not isinstance(payload.get("models"), list):
            raise ChatGPTAuthError(
                "invalid_catalog", "OpenAI returned an invalid ChatGPT model catalog."
            )
        return [
            {"slug": item["slug"], "display_name": item.get("display_name") or item["slug"]}
            for item in payload["models"]
            if isinstance(item, dict)
            and item.get("visibility") == "list"
            and isinstance(item.get("slug"), str)
        ]

    def publish_models(self, registration_id: str | None = None) -> list[dict]:
        with self.store.locked() as stored:
            registration_id = registration_id or stored["active_id"]
        models = self.models(registration_id)
        if not models or self.catalog is None:
            return models
        profile_id = f"llm-chatgpt-{registration_id}"

        def mutate(catalog: dict) -> None:
            llm = catalog["services"]["llm"]
            profile = {
                "id": profile_id,
                "name": "ChatGPT (SIWC)",
                "binding": "openai_chatgpt",
                "base_url": RESOURCE,
                "api_key": "",
                "owner_bound": True,
                "models": [
                    {
                        "id": f"{profile_id}-{hashlib.sha256(m['slug'].encode()).hexdigest()[:16]}",
                        "name": m["display_name"],
                        "model": f"openai_chatgpt/{registration_id}/{m['slug']}",
                    }
                    for m in models
                ],
            }
            profiles = llm.setdefault("profiles", [])
            profiles[:] = [p for p in profiles if p["id"] != profile_id]
            profiles.append(profile)
            if not llm.get("active_profile_id") or llm["active_profile_id"] == profile_id:
                llm["active_profile_id"] = profile_id
                if llm.get("active_model_id") not in {m["id"] for m in profile["models"]}:
                    llm["active_model_id"] = profile["models"][0]["id"]

        self.catalog.update(mutate)
        from deeptutor.services.llm.config import clear_llm_config_cache

        clear_llm_config_cache()
        return models

    def logout(self, registration_id: str) -> dict:
        with self._activity_lock:
            if self._inferences or self._signing_out or self.pending:
                raise ChatGPTAuthError(
                    "operation_active",
                    "Wait for the current ChatGPT request or sign-in to finish.",
                    409,
                )
            self._signing_out = True
        try:
            return self._logout(registration_id)
        finally:
            with self._activity_lock:
                self._signing_out = False

    def _logout(self, registration_id: str) -> dict:
        confirmed = True
        with self.store.locked() as stored:
            account = stored["accounts"].get(registration_id)
            if account is None:
                raise ChatGPTAuthError("unknown_account", "Choose a saved ChatGPT account.", 404)
            if account.get("refresh_token"):
                confirmed = False
                for delay in (0, 0.25, 1):
                    if delay:
                        time.sleep(delay)
                    try:
                        self._request(
                            "POST",
                            self._metadata()["revocation_endpoint"],
                            data={
                                "token": account["refresh_token"],
                                "token_type_hint": "refresh_token",
                                "client_id": account["client_id"],
                            },
                        )
                        confirmed = True
                        break
                    except ChatGPTAuthError as exc:
                        if exc.status < 500:
                            break
            for name in ("access_token", "refresh_token", "id_token"):
                account.pop(name, None)
            if stored["active_id"] == registration_id:
                stored["active_id"] = None
        return {"revocation_confirmed": confirmed, **self.status()}


_SERVICES: dict[str, ChatGPTAuthService] = {}


def get_chatgpt_auth_service() -> ChatGPTAuthService:
    from deeptutor.multi_user.paths import get_owner_secrets_dir
    from deeptutor.multi_user.personal_models import owner_catalog_service

    root = get_owner_secrets_dir()
    key = str(root)
    if key not in _SERVICES:
        _SERVICES[key] = ChatGPTAuthService(root, owner_catalog_service())
    return _SERVICES[key]
