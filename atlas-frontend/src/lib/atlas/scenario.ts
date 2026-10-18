// Which provider-supplied moment is shown (demo only). One coherent state at a time.
import { useSyncExternalStore } from "react";
import type { QueryClient } from "@tanstack/react-query";
import { atlasQuery, isLive, scenarioOptions } from "./api";
import type { TripStateKind } from "./domain";

const KEY = "atlas.state";
const ids = scenarioOptions.map((o) => o.id);
const listeners = new Set<() => void>();
let current: TripStateKind | undefined;

const valid = (s: string | null | undefined): s is TripStateKind => !!s && (ids as string[]).includes(s);

function read(): TripStateKind | undefined {
  if (!ids.length) return undefined;
  if (current) return current;
  const fromUrl = new URLSearchParams(window.location.search).get("state")?.toUpperCase();
  const stored = window.localStorage.getItem(KEY);
  current = valid(fromUrl) ? fromUrl : valid(stored) ? stored : ids[0];
  return current;
}

export function setScenario(s: TripStateKind) {
  current = s;
  window.localStorage.setItem(KEY, s);
  listeners.forEach((l) => l());
}

export function useScenario(): TripStateKind | undefined {
  return useSyncExternalStore(
    (l) => (listeners.add(l), () => listeners.delete(l)),
    read,
    () => ids[0],
  );
}

/** Loaders prefetch every scenario so switching never suspends. */
export const prefetchTrips = async (qc: QueryClient) => {
  // Live state lives in a browser session; the server has nothing to prefetch.
  if (isLive && typeof window === "undefined") return [];
  return Promise.all((ids.length ? ids : [undefined]).map((s) => qc.ensureQueryData(atlasQuery(s))));
};
