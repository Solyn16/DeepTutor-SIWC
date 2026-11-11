"use client";

import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { apiFetch, apiUrl } from "@/lib/api";
import { invalidateLLMOptionsCache } from "@/lib/llm-options";
import { useSettings } from "@/features/settings/store/SettingsStore";
import { registryButton, registryPrimary } from "./RegistryControls";
import ProviderIcon from "@/components/common/ProviderIcon";

type Account = { id: string; label: string; connected: boolean; plan_enabled: boolean };
type Status = { accounts: Account[]; active_id: string | null; operation_state: string; error_code: string | null };

async function request<T>(action: string, registration_id?: string): Promise<T> {
  const response = await apiFetch(apiUrl(`/api/settings/providers/openai-chatgpt/${action}`), {
    method: action === "status" ? "GET" : "POST",
    ...(action === "status" ? {} : {
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ registration_id }),
    }),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail?.message || data.detail || `HTTP ${response.status}`);
  return data as T;
}

export function ChatGPTAuthCard() {
  const { t } = useTranslation();
  const { reloadSettings, hasUnsavedChanges } = useSettings();
  const [status, setStatus] = useState<Status | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [authorizeUrl, setAuthorizeUrl] = useState("");
  const waiting = ["waiting", "exchanging"].includes(status?.operation_state || "");

  const refreshModels = useCallback(async (id?: string) => {
    await request("models", id);
    invalidateLLMOptionsCache();
    await reloadSettings();
    setNotice(t("siwc.planEnabled"));
  }, [reloadSettings, t]);

  useEffect(() => {
    let alive = true;
    request<Status>("status").then(value => { if (alive) setStatus(value); })
      .catch(err => { if (alive) setError(String(err.message)); });
    return () => { alive = false; };
  }, []);

  useEffect(() => {
    if (!waiting) return;
    let alive = true;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const next = await request<Status>("status");
        if (!alive) return;
        setStatus(next);
        if (["waiting", "exchanging"].includes(next.operation_state)) {
          timer = setTimeout(poll, 1000);
          return;
        }
        setAuthorizeUrl("");
        if (next.operation_state === "completed") {
          const active = next.accounts.find(account => account.id === next.active_id);
          setNotice(t(active?.plan_enabled ? "siwc.planEnabled" : "siwc.planDisabled"));
          if (active?.plan_enabled && !hasUnsavedChanges) await refreshModels(active.id);
        } else if (next.error_code) setError(`${t("siwc.failed")} (${next.error_code})`);
      } catch (err) {
        if (alive) {
          setError(err instanceof Error ? err.message : t("siwc.failed"));
          timer = setTimeout(poll, 3000);
        }
      }
    };
    timer = setTimeout(poll, 500);
    return () => { alive = false; clearTimeout(timer); };
  }, [waiting, hasUnsavedChanges, refreshModels, t]);

  async function act(action: "start" | "cancel" | "logout" | "models", id?: string) {
    // Open synchronously from the click so normal popup protection permits sign-in.
    const popup = action === "start" ? window.open("about:blank", "_blank") : null;
    if (popup) popup.opener = null;
    setBusy(true); setError(""); setNotice("");
    try {
      if (action === "start") {
        const started = await request<{ authorize_url: string }>(action, id);
        const target = new URL(started.authorize_url);
        if (target.origin !== "https://auth.openai.com") throw new Error(t("siwc.failed"));
        setAuthorizeUrl(target.href);
        if (popup) popup.location.href = target.href;
      } else if (action === "models") {
        await refreshModels(id);
      } else {
        const result = await request<{ revocation_confirmed?: boolean }>(action, id);
        if (result.revocation_confirmed === false) setNotice(t("siwc.revocationUnconfirmed"));
        setAuthorizeUrl("");
      }
      setStatus(await request<Status>("status"));
    } catch (err) {
      popup?.close();
      setError(err instanceof Error ? err.message : t("siwc.failed"));
    } finally { setBusy(false); }
  }

  return (
    <section aria-label={t("siwc.title")} className="space-y-4 rounded-xl border border-[var(--border)] p-4">
      <h3 className="font-semibold">{t("siwc.title")}</h3>
      <p className="text-sm text-[var(--muted-foreground)]">{t("siwc.description")}</p>
      {hasUnsavedChanges && <p role="status" className="text-sm">{t("siwc.saveFirst")}</p>}
      {status?.accounts.map((account, index) => (
        <div key={account.id} className="space-y-2 rounded-lg bg-[var(--muted)] p-3">
          <p className="break-words text-sm">{index + 1}. {account.label} · {t(account.plan_enabled ? "siwc.enabled" : account.connected ? "siwc.identityOnly" : "siwc.disconnected")}</p>
          <div className="flex flex-wrap gap-2">
            <button type="button" data-oai-siwc-button className={registryButton} disabled={busy || waiting || hasUnsavedChanges} onClick={() => void act("start", account.id)}><ProviderIcon provider="openai_chatgpt" size={16} />{t("siwc.continue")}</button>
            {account.plan_enabled && <button type="button" className={registryButton} disabled={busy || waiting || hasUnsavedChanges} onClick={() => void act("models", account.id)}>{t("siwc.refreshModels")}</button>}
            {account.connected && <button type="button" className={registryButton} disabled={busy || waiting} onClick={() => void act("logout", account.id)}>{t("siwc.signOut")}</button>}
          </div>
        </div>
      ))}
      <div className="flex flex-wrap gap-3">
        <button type="button" data-oai-siwc-button className={registryPrimary} disabled={busy || waiting || hasUnsavedChanges} onClick={() => void act("start")}><ProviderIcon provider="openai_chatgpt" size={16} />{t("siwc.continue")}</button>
        {waiting && <button type="button" className={registryButton} disabled={busy || status?.operation_state === "exchanging"} onClick={() => void act("cancel")}>{t("siwc.cancel")}</button>}
        <a href="https://chatgpt.com/settings/usage" target="_blank" rel="noopener noreferrer" className={registryButton}>{t("siwc.manageUsage")}</a>
      </div>
      {waiting && <p role="status" className="text-sm">{t("siwc.waiting")}</p>}
      {authorizeUrl && <a href={authorizeUrl} target="_blank" rel="noopener noreferrer" className="text-sm underline">{t("siwc.openBrowser")}</a>}
      {notice && <p role="status" className="text-sm">{notice}</p>}
      {error && <p role="alert" className="text-sm text-red-600">{error}</p>}
    </section>
  );
}
