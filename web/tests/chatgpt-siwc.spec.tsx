import React, { act } from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { ChatGPTAuthCard } from "@/components/settings/ChatGPTAuthCard";

const mocks = vi.hoisted(() => ({ fetch: vi.fn(), reload: vi.fn(), dirty: false }));
vi.mock("@/lib/api", () => ({ apiUrl: (s: string) => s, apiFetch: (...args: unknown[]) => mocks.fetch(...args) }));
vi.mock("@/lib/llm-options", () => ({ invalidateLLMOptionsCache: vi.fn() }));
vi.mock("@/features/settings/store/SettingsStore", () => ({
  useSettings: () => ({ reloadSettings: mocks.reload, hasUnsavedChanges: mocks.dirty }),
}));
vi.mock("react-i18next", () => ({ useTranslation: () => ({ t: (s: string) => s }) }));

beforeEach(() => {
  mocks.dirty = false;
  mocks.fetch.mockReset();
  mocks.reload.mockReset();
});

function response(value: unknown, status = 200) {
  return { ok: status < 400, json: async () => value };
}
const idle = { accounts: [], active_id: null, operation_state: "idle", error_code: null };

it("opens official authorization and offers a fallback link", async () => {
  const popup = { opener: null, location: { href: "" }, close: vi.fn() };
  vi.spyOn(window, "open").mockReturnValue(popup as unknown as Window);
  const authorize_url = "https://auth.openai.com/api/accounts/authorize?client_id=dynamic_agent_client";
  mocks.fetch.mockImplementation(async (url: string) => url.endsWith("/start")
    ? response({ authorize_url }) : response(idle));
  await act(async () => { render(<ChatGPTAuthCard />); });
  await waitFor(() => expect(mocks.fetch).toHaveBeenCalledTimes(1));
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: "siwc.continue" })); });
  await waitFor(() => expect(popup.location.href).toBe(authorize_url));
  expect(screen.getByRole("link", { name: "siwc.openBrowser" })).toHaveAttribute("href", authorize_url);
  expect(mocks.fetch.mock.calls.some(([url]) => url.endsWith("/start"))).toBe(true);
});

it("protects pending settings and exposes no credential fields", async () => {
  mocks.dirty = true;
  mocks.fetch.mockResolvedValue(response(idle));
  await act(async () => { render(<ChatGPTAuthCard />); });
  expect(screen.getByRole("button", { name: "siwc.continue" })).toBeDisabled();
  expect(screen.queryByRole("textbox")).toBeNull();
  expect(screen.getByText("siwc.saveFirst")).toBeInTheDocument();
});

it("shows local-only failures and closes the empty authorization popup", async () => {
  const popup = { opener: null, location: { href: "" }, close: vi.fn() };
  vi.spyOn(window, "open").mockReturnValue(popup as unknown as Window);
  mocks.fetch.mockImplementation(async (url: string) => url.endsWith("/start")
    ? response({ detail: "Local installation required" }, 403) : response(idle));
  await act(async () => { render(<ChatGPTAuthCard />); });
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: "siwc.continue" })); });
  expect(await screen.findByRole("alert")).toHaveTextContent("Local installation required");
  expect(popup.close).toHaveBeenCalled();
});

it("retains remote revocation warning after local sign-out", async () => {
  const connected = { ...idle, accounts: [{ id: "a", label: "example", connected: true, plan_enabled: true }] };
  mocks.fetch.mockImplementation(async (url: string) => url.endsWith("/logout")
    ? response({ ...idle, revocation_confirmed: false }) : response(connected));
  await act(async () => { render(<ChatGPTAuthCard />); });
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: "siwc.signOut" })); });
  expect(await screen.findByText("siwc.revocationUnconfirmed")).toBeInTheDocument();
  expect(mocks.fetch.mock.calls.some(([url, options]) => url.endsWith("/logout") &&
    JSON.parse(options.body).registration_id === "a")).toBe(true);
});
