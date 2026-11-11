"use client";

import { useEffect } from "react";
import { browserStorage } from "@/shared/storage";
import { usePathname } from "next/navigation";
import { SETTINGS_RETURN_KEY, settingsReturnPath } from "@/lib/settings-return";

export { SETTINGS_RETURN_KEY } from "@/lib/settings-return";

/** Session-only navigation state; never touches a user's configuration. */
export default function SettingsReturnTracker() {
  const pathname = usePathname();
  useEffect(() => {
    if (!pathname) return;
    // Without the workspace: returning from Settings stays in whichever
    // workspace the tab is in by then, instead of switching back.
    const destination = settingsReturnPath(
      pathname + window.location.search + window.location.hash,
    );
    if (!destination) return;
    try {
      browserStorage.writeRaw(
        "session",
        SETTINGS_RETURN_KEY,
        destination,
      );
    } catch {
      /* Storage can be disabled. Returning home remains available. */
    }
  }, [pathname]);
  return null;
}
