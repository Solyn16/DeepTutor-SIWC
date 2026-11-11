"""CLI commands for provider auth and access validation."""

from __future__ import annotations

import asyncio
import webbrowser

import httpx
import typer

from deeptutor.services.codex_auth import CodexAuthError, get_codex_oauth_service

from .common import maybe_run


def register(app: typer.Typer) -> None:
    @app.command("login")
    def provider_login(
        provider: str = typer.Argument(
            ...,
            help=(
                "Provider: openai-codex (OAuth login) | github-copilot "
                "(GitHub device login) | codebuddy (validate CodeBuddy SDK auth) | "
                "openai-chatgpt (official SIWC)"
            ),
        ),
    ) -> None:
        """Authenticate or validate provider access."""
        key = provider.strip().lower().replace("-", "_")
        if key in {"openai_chatgpt", "siwc"}:
            maybe_run(_login_chatgpt())
            return
        if key == "openai_codex":
            maybe_run(_login_openai_codex())
            return
        if key == "github_copilot":
            maybe_run(_login_github_copilot())
            return
        if key in {"codebuddy", "codebuddy_code", "workbuddy"}:
            maybe_run(_login_codebuddy())
            return
        raise typer.BadParameter(
            f"Unknown provider `{provider}`. Supported: openai-chatgpt, openai-codex, github-copilot, codebuddy"
        )


async def _login_chatgpt() -> None:
    from deeptutor.services.chatgpt_auth import ChatGPTAuthError, get_chatgpt_auth_service

    service = get_chatgpt_auth_service()
    try:
        saved = service.status()["accounts"]
        registration_id = None
        if saved:
            for index, account in enumerate(saved, 1):
                typer.echo(f"{index}. {account['label']} ({account['client_id']})")
            selected = typer.prompt("Account number (0 to add an account)", default=0, type=int)
            if not 0 <= selected <= len(saved):
                raise typer.BadParameter("Choose a listed account number.")
            if selected:
                registration_id = saved[selected - 1]["id"]
        started = await service.start_login(registration_id)
        typer.echo("Continue with ChatGPT in your browser. Authorize plan usage to enable AI requests.")
        if not webbrowser.open(started["authorize_url"]):
            typer.echo(started["authorize_url"])
        while True:
            status = service.status()
            if status["operation_state"] == "completed":
                active = next(a for a in status["accounts"] if a["id"] == status["active_id"])
                if not active["plan_enabled"]:
                    typer.echo("Signed in. ChatGPT plan usage was not authorized; inference is disabled.")
                    return
                models = await asyncio.to_thread(service.publish_models, active["id"])
                typer.echo(f"ChatGPT connected. {len(models)} models added to Settings.")
                typer.echo("Eligible AI requests use your ChatGPT plan. Manage usage: https://chatgpt.com/settings/usage")
                return
            if status["operation_state"] in {"failed", "expired", "cancelled"}:
                typer.echo(f"ChatGPT sign-in did not complete: {status['error_code'] or status['operation_state']}")
                raise typer.Exit(1)
            await asyncio.sleep(0.5)
    except (asyncio.CancelledError, KeyboardInterrupt):
        await service.cancel_login()
        raise typer.Exit(130) from None
    except ChatGPTAuthError as exc:
        typer.echo(str(exc))
        raise typer.Exit(1) from None


async def _login_openai_codex() -> None:
    service = get_codex_oauth_service()
    try:
        started = await service.start_login()
        authorize_url = str(started["authorize_url"])
        typer.echo(f"Callback: {started['redirect_uri']}")
        typer.echo(f"Authorization URL: {authorize_url}")
        typer.echo(f"Remote server tunnel command: {started['ssh_forward_command']}")
        typer.echo(
            "Opening the OpenAI Codex sign-in in your browser; "
            "credentials are written only to DeepTutor's private directory."
        )
        if not webbrowser.open(authorize_url):
            typer.echo(f"The browser did not open automatically. Visit: {authorize_url}")

        while True:
            status = service.public_status()
            operation_state = status.get("operation_state")
            if operation_state == "completed":
                typer.echo(
                    f"OpenAI Codex sign-in succeeded. Models available: "
                    f"{status.get('model_count', 0)}."
                )
                active_model = status.get("active_model")
                if active_model:
                    typer.echo(f"Codex is the active model: {active_model}")
                else:
                    typer.echo("Select a Codex model in Settings to start using it.")
                return
            if operation_state in {"failed", "expired", "cancelled"}:
                error_code = status.get("error_code") or operation_state
                typer.echo(f"OpenAI Codex sign-in did not complete: {error_code}")
                raise typer.Exit(code=1)
            await asyncio.sleep(0.5)
    except asyncio.CancelledError:
        await service.cancel_login()
        typer.echo("Cancelled the OpenAI Codex sign-in.")
        raise typer.Exit(code=130) from None
    except CodexAuthError as exc:
        typer.echo(f"OpenAI Codex sign-in failed: {exc.public_message}")
        raise typer.Exit(code=1)


async def _login_github_copilot() -> None:
    """Authenticate with GitHub device flow and validate Copilot access."""
    try:
        from deeptutor.services.github_copilot_auth import (
            list_github_copilot_models,
            login_github_copilot,
        )
        from deeptutor.services.llm.provider_core.github_copilot_provider import (
            validate_github_copilot_model,
        )
    except ImportError:
        typer.echo(
            "GitHub Copilot login dependencies are not installed. Install CLI deps: "
            "python -m pip install -e ./packaging/deeptutor-cli"
        )
        raise typer.Exit(code=1)
    try:
        token = await login_github_copilot(print_fn=typer.echo)
        typer.echo(
            "GitHub sign-in saved in DeepTutor's owner-private storage. Validating Copilot..."
        )
        models = await list_github_copilot_models()
        if not models:
            raise RuntimeError("No usable GitHub Copilot models are available for this account.")
        await validate_github_copilot_model(models[0])
    except (httpx.HTTPError, OSError, RuntimeError, ValueError, KeyError) as exc:
        typer.echo(f"GitHub Copilot login failed: {exc}")
        raise typer.Exit(code=1) from exc
    account = getattr(token, "account_id", None) or "current account"
    typer.echo(f"GitHub Copilot login succeeded for {account}.")
    typer.echo(f"Validated model access: {models[0]}")


async def _login_codebuddy() -> None:
    """Validate CodeBuddy auth, starting an SDK login when needed."""
    from deeptutor.services.codebuddy_credentials import load_credentials, probe_account

    credentials = load_credentials()
    if credentials is not None:
        label = await probe_account(credentials)
        if label:
            typer.echo(f"CodeBuddy auth validation succeeded for {label}.")
            return

    try:
        from codebuddy_agent_sdk import authenticate, query
    except ImportError:
        typer.echo(
            "CodeBuddy is not signed in. Sign in with the CodeBuddy IDE plugin, run "
            "`codebuddy` and enter `/login`, or set CODEBUDDY_API_KEY. For in-app login "
            "install the SDK: `python -m pip install codebuddy-agent-sdk`."
        )
        raise typer.Exit(code=1)

    try:
        saw_message = False
        async for _message in query(prompt="Reply with only OK."):
            saw_message = True
        if not saw_message:
            raise RuntimeError("CodeBuddy SDK returned no messages.")
    except Exception as exc:
        if "Authentication required" not in str(exc):
            typer.echo(f"CodeBuddy auth validation failed: {exc}")
            raise typer.Exit(code=1) from exc
        typer.echo("CodeBuddy is not logged in. Starting CodeBuddy login flow ...")
        try:
            auth = await authenticate(timeout=300.0)
            if getattr(auth, "auth_url", ""):
                typer.echo(f"Open this URL to sign in: {auth.auth_url}")
                try:
                    webbrowser.open(auth.auth_url)
                except Exception:
                    pass
            result = await auth
        except Exception as login_exc:
            typer.echo(f"CodeBuddy login failed: {login_exc}")
            typer.echo("You can also run `codebuddy`, enter `/login`, or set CODEBUDDY_API_KEY.")
            raise typer.Exit(code=1) from login_exc
        userinfo = getattr(result, "userinfo", None)
        label = (
            getattr(userinfo, "user_nickname", None)
            or getattr(userinfo, "user_name", None)
            or getattr(userinfo, "user_id", None)
            or "current account"
        )
        typer.echo(f"CodeBuddy auth validation succeeded for {label}.")
        return
    typer.echo("CodeBuddy auth validation succeeded.")
