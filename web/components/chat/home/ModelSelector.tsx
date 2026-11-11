"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { AlertCircle, Bot, Check, ChevronDown } from "lucide-react";
import { useTranslation } from "react-i18next";
import { useLingerExpand } from "@/hooks/use-linger-expand";
import { useOutsideClick } from "@/hooks/use-outside-click";
import Tooltip from "@/shared/ui/Tooltip";
import ToolbarLabel from "./ToolbarLabel";
import ProviderIcon from "@/components/common/ProviderIcon";
import type { LLMSelection } from "@/features/chat/model/protocol";
import {
  llmSelectionKey,
  sameLLMSelection,
  type LLMOption,
} from "@/lib/llm-options";

function formatContextWindow(value?: number) {
  if (!value) return "";
  if (value >= 1_000_000) return `${Math.round(value / 1_000_000)}M ctx`;
  if (value >= 1_000) return `${Math.round(value / 1_000)}k ctx`;
  return `${value} ctx`;
}

function providerLabel(option: LLMOption) {
  return (
    option.provider_label || option.provider || option.profile_name || "LLM"
  );
}

function modelDisplayName(option: LLMOption) {
  if (option.provider === "openai_chatgpt") {
    const name = option.model_name.trim();
    if (name && !name.startsWith("openai_chatgpt/")) return name;
    return option.model.replace(/^openai_chatgpt\/[^/]+\/?/, "") || "ChatGPT";
  }
  return option.model || option.model_name;
}

function ModelOptionRow({
  option,
  selected,
  secondaryLabel,
  onSelect,
}: {
  option: LLMOption;
  selected: boolean;
  secondaryLabel: string;
  onSelect: () => void;
}) {
  const { t } = useTranslation();
  // SIWC's model field includes account routing. Keep it out of display labels.
  const modelLabel = modelDisplayName(option);
  const contextWindow = formatContextWindow(option.context_window);
  // Long model ids ("google/gemini-3-flash-preview") get ellipsized by the
  // inline layout; hovering the row reveals the full id as an overlay. The
  // scrollWidth check at mouseenter time keeps the overlay away from rows
  // that aren't actually truncated.
  const nameRef = useRef<HTMLSpanElement>(null);
  const [revealFull, setRevealFull] = useState(false);
  return (
    <button
      type="button"
      aria-label={`${option.provider === "openai_chatgpt" ? modelLabel : option.model_name} | ${option.provider === "openai_chatgpt" ? secondaryLabel : option.profile_name}`}
      title={option.provider === "openai_chatgpt" ? `${modelLabel} · ${secondaryLabel}` : undefined}
      onClick={onSelect}
      onMouseEnter={() => {
        const el = nameRef.current;
        setRevealFull(!!el && el.scrollWidth > el.clientWidth + 1);
      }}
      onMouseLeave={() => setRevealFull(false)}
      className={`relative flex w-full items-center gap-2 px-3 py-1.5 text-left transition-colors active:bg-[var(--muted)]/70 ${
        selected ? "bg-[var(--primary)]/[0.06]" : "hover:bg-[var(--muted)]/45"
      }`}
    >
      <ProviderIcon
        provider={option.provider}
        size={14}
        className={
          selected ? "text-[var(--primary)]" : "text-[var(--muted-foreground)]"
        }
      />
      <span
        ref={nameRef}
        className="min-w-0 flex-1 truncate text-[12.5px] font-medium text-[var(--foreground)]"
      >
        {modelLabel}
      </span>
      {option.is_active_default && (
        <span className="shrink-0 rounded-full bg-[var(--muted)] px-1.5 py-px text-[9px] font-semibold uppercase tracking-wide text-[var(--muted-foreground)]">
          {t("Default")}
        </span>
      )}
      <span className="max-w-[84px] shrink-0 truncate text-[11px] text-[var(--muted-foreground)]">
        {secondaryLabel}
      </span>
      {contextWindow ? (
        <span className="shrink-0 text-[11px] text-[var(--muted-foreground)]">
          {contextWindow}
        </span>
      ) : null}
      {selected && (
        <Check
          size={14}
          strokeWidth={2}
          className="shrink-0 text-[var(--primary)]"
        />
      )}
      {revealFull && (
        <span className="pointer-events-none absolute inset-x-1.5 top-1/2 z-10 -translate-y-1/2 break-all rounded-lg border border-[var(--border)] bg-[var(--popover)] px-2 py-1 text-[12px] font-medium text-[var(--foreground)] shadow-md">
          {modelLabel}
        </span>
      )}
    </button>
  );
}

export default function ModelSelector({
  options,
  activeDefault,
  value,
  loading,
  error,
  allowSystemDefault = false,
  systemDefaultLabel,
  systemDefaultDetail,
  helperText,
  placement = "top",
  pinned = false,
  onChange,
  onRefresh,
}: {
  options: LLMOption[];
  activeDefault: LLMSelection | null;
  value: LLMSelection | null;
  loading: boolean;
  error: boolean;
  allowSystemDefault?: boolean;
  systemDefaultLabel?: string;
  systemDefaultDetail?: string;
  helperText?: string;
  placement?: "top" | "bottom";
  /** Optional always-visible label for surfaces outside the composer. */
  pinned?: boolean;
  onChange: (selection: LLMSelection | null) => void;
  onRefresh?: () => void;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const {
    expanded,
    linger,
    triggerProps: lingerProps,
  } = useLingerExpand(open, 1200, pinned);

  const selectedSelection = allowSystemDefault
    ? value
    : (value ?? activeDefault);
  const selectedKey = llmSelectionKey(selectedSelection);
  const selectedOption = useMemo(
    () =>
      options.find((option) => sameLLMSelection(option, selectedSelection)) ??
      null,
    [options, selectedSelection],
  );
  const chatgptProfiles = useMemo(
    () => [...new Set(options.filter(option => option.provider === "openai_chatgpt").map(option => option.profile_id))],
    [options],
  );
  const secondaryLabel = (option: LLMOption) => {
    if (option.provider !== "openai_chatgpt") return providerLabel(option);
    const name = option.profile_name.trim();
    if (name && name !== "ChatGPT (SIWC)" && name !== "ChatGPT") return name;
    return chatgptProfiles.length > 1
      ? `ChatGPT ${chatgptProfiles.indexOf(option.profile_id) + 1}`
      : "ChatGPT";
  };

  useOutsideClick(rootRef, open, () => {
    setOpen(false);
    linger();
  });

  const defaultLabel = systemDefaultLabel || t("System default");
  const defaultDetail =
    systemDefaultDetail || t("Use the active default model from Settings");
  const canRefresh = error && Boolean(onRefresh);
  const disabled =
    loading ||
    (!canRefresh && (error || (options.length === 0 && !allowSystemDefault)));
  const label = loading
    ? t("Loading models")
    : error
      ? canRefresh
        ? t("Refresh models")
        : t("Models unavailable")
      : allowSystemDefault && !selectedSelection
        ? defaultLabel
        : selectedOption
          ? modelDisplayName(selectedOption)
          : t("Select model");
  const menuPlacementClass =
    placement === "bottom" ? "top-full mt-1.5" : "bottom-full mb-1.5";

  return (
    <div ref={rootRef} className="relative">
      {/* The model name shares the composer selectors' spring and hover delay. */}
      <Tooltip label={canRefresh ? t("Refresh models") : label} suppressed={open} side="top">
        <button
          type="button"
          disabled={disabled}
          onClick={() => {
            if (canRefresh) {
              setOpen(false);
              onRefresh?.();
              return;
            }
            setOpen((current) => !current);
          }}
          aria-label={canRefresh ? t("Refresh models") : t("Select model")}
          aria-expanded={open}
          {...lingerProps}
          className={`inline-flex h-8 shrink-0 items-center rounded-lg px-2 text-[14px] font-medium transition-[background-color,color,transform] duration-150 active:scale-[0.97] ${
            disabled
              ? "cursor-not-allowed text-[var(--border)]"
              : open
                ? "bg-[var(--muted)] text-[var(--foreground)]"
                : "text-[var(--muted-foreground)] hover:bg-[var(--muted)]/55 hover:text-[var(--foreground)]"
          }`}
        >
          {error ? (
            <AlertCircle size={16} strokeWidth={1.7} className="shrink-0" />
          ) : (
            <ProviderIcon provider={selectedOption?.provider} size={16} />
          )}
          <ToolbarLabel expanded={expanded} fullWidth>
            <span className="min-w-0">{label}</span>
            <ChevronDown
              size={13}
              className={`shrink-0 transition-transform ${open ? "rotate-180" : ""}`}
            />
          </ToolbarLabel>
        </button>
      </Tooltip>

      {open && !disabled && (
        <div
          className={`absolute right-0 z-50 ${menuPlacementClass} w-[min(280px,calc(100vw-32px))] overflow-hidden rounded-xl border border-[var(--border)] bg-[var(--popover)] shadow-lg backdrop-blur-md`}
        >
          {helperText ? (
            <div className="border-b border-[var(--border)]/50 px-3 py-1.5 text-[11px] text-[var(--muted-foreground)]">
              {helperText}
            </div>
          ) : null}
          <div className="max-h-[280px] overflow-y-auto py-1">
            {allowSystemDefault && (
              <button
                type="button"
                aria-label={defaultDetail}
                onClick={() => {
                  onChange(null);
                  setOpen(false);
                  linger();
                }}
                className={`flex w-full items-center gap-2 px-3 py-1.5 text-left transition-colors active:bg-[var(--muted)]/70 ${
                  selectedKey === ""
                    ? "bg-[var(--primary)]/[0.06]"
                    : "hover:bg-[var(--muted)]/45"
                }`}
              >
                <Bot
                  size={14}
                  strokeWidth={1.7}
                  className={`shrink-0 ${
                    selectedKey === ""
                      ? "text-[var(--primary)]"
                      : "text-[var(--muted-foreground)]"
                  }`}
                />
                <span className="min-w-0 flex-1 truncate text-[12.5px] font-medium text-[var(--foreground)]">
                  {defaultLabel}
                </span>
                {selectedKey === "" && (
                  <Check
                    size={14}
                    strokeWidth={2}
                    className="shrink-0 text-[var(--primary)]"
                  />
                )}
              </button>
            )}
            {options.map((option) => {
              const optionSelection = {
                profile_id: option.profile_id,
                model_id: option.model_id,
              };
              const optionKey = llmSelectionKey(optionSelection);
              return (
                <ModelOptionRow
                  key={optionKey}
                  option={option}
                  secondaryLabel={secondaryLabel(option)}
                  selected={optionKey === selectedKey}
                  onSelect={() => {
                    onChange(optionSelection);
                    setOpen(false);
                    linger();
                  }}
                />
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
