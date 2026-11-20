"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { ApiError, getBusiness, type Business } from "@/lib/api";
import { POLLING_STATUSES } from "@/lib/stages";

const POLL_MS = 2000;

interface Workspace {
  id: string;
  business: Business | null;
  loading: boolean;
  error: string | null;
  // Increments after every successful poll. Pages that show data derived from the business
  // (e.g. site and deployments while DEPLOYING) refetch when it changes, so the provider stays
  // the only polling loop.
  tick: number;
  // Re-fetch now (e.g. after an action that changed the business).
  refresh: () => Promise<void>;
  // Adopt a business the API just returned (retry, assumption edits) without another request.
  update: (b: Business) => void;
}

const Ctx = createContext<Workspace | null>(null);

export function useWorkspace(): Workspace {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useWorkspace must be used inside WorkspaceProvider");
  return ctx;
}

const messageOf = (e: unknown, fallback: string) => (e instanceof Error ? e.message : fallback);

export default function WorkspaceProvider({ id, children }: { id: string; children: ReactNode }) {
  const [business, setBusiness] = useState<Business | null>(null);
  const [error, setError] = useState<string | null>(null);
  // A 4xx other than 408/429 (unknown business, malformed id) will not fix itself: stop polling.
  const [fatal, setFatal] = useState(false);
  const [tick, setTick] = useState(0);

  // The single polling loop. It runs while nothing has loaded yet (so a transient error retries)
  // and while the pipeline is working. `active` only flips at those boundaries, so moving from
  // INTAKE to PREMORTEM does not restart the loop and cannot start a second one.
  const status = business?.status ?? null;
  const active = !fatal && (status === null || POLLING_STATUSES.has(status));

  useEffect(() => {
    if (!active) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const b = await getBusiness(id);
        if (cancelled) return;
        setBusiness(b);
        setError(null);
        setTick((t) => t + 1);
      } catch (e) {
        if (cancelled) return;
        setError(messageOf(e, "Could not load this business."));
        if (e instanceof ApiError && e.status >= 400 && e.status < 500 && e.status !== 408 && e.status !== 429) {
          setFatal(true);
          return;
        }
      }
      timer = setTimeout(poll, POLL_MS);
    }
    void poll();
    return () => { cancelled = true; clearTimeout(timer); };
  }, [id, active]);

  const refresh = useCallback(async () => {
    try {
      setBusiness(await getBusiness(id));
      setError(null);
    } catch (e) {
      setError(messageOf(e, "Could not refresh this business."));
    }
  }, [id]);

  const update = useCallback((b: Business) => { setBusiness(b); setError(null); }, []);

  const value = useMemo<Workspace>(
    () => ({ id, business, loading: business === null && error === null, error, tick, refresh, update }),
    [id, business, error, tick, refresh, update],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}
