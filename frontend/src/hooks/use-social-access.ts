"use client";

import { useEffect, useState } from "react";

// One request per page load, shared by the shell and the pages it wraps.
let pending: Promise<boolean> | null = null;

/** True only when this server explicitly grants social publishing. Missing or false stays hidden. */
export function isSocialPublishingEnabled(data: { enabled?: boolean } | null | undefined) {
  return data?.enabled === true;
}

const STORAGE_KEY = "supoclip:social-access";

function readCachedAccess(): boolean | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.sessionStorage.getItem(STORAGE_KEY);
    return raw === "1" ? true : raw === "0" ? false : null;
  } catch {
    return null;
  }
}

function writeCachedAccess(value: boolean) {
  if (typeof window === "undefined") return;
  try {
    window.sessionStorage.setItem(STORAGE_KEY, value ? "1" : "0");
  } catch { /* ignore storage errors */ }
}

function loadSocialAccess() {
  pending ??= fetch("/api/social/providers", { cache: "no-store" })
    .then(async (response) => {
      if (!response.ok) {
        pending = null;
        return false;
      }
      const data = (await response.json()) as { enabled?: boolean };
      const value = isSocialPublishingEnabled(data);
      writeCachedAccess(value);
      return value;
    })
    .catch(() => {
      pending = null;
      return false;
    });
  return pending;
}

/** Whether social publishing is available to the signed-in user (null while loading). */
export function useSocialAccess(enabled = true) {
  const [access, setAccess] = useState<boolean | null>(() => (enabled ? readCachedAccess() : null));
  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    void loadSocialAccess().then((value) => {
      if (!cancelled) setAccess(value);
    });
    return () => {
      cancelled = true;
    };
  }, [enabled]);
  return access;
}
