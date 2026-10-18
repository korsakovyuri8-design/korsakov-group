// Pure presentation helpers over an AtlasState the provider already resolved.
// No business rules: state, status and certainty all come from the provider.
import type { AtlasState, ItemKind, TripContext, TripItem } from "./domain";

export const isClock = (t: string | undefined): t is string => !!t && /^\d{2}:\d{2}$/.test(t);
export function minutes(t: string) {
  const [h = 0, m = 0] = t.split(":").map(Number);
  return h * 60 + m;
}
export function formatDuration(mins: number) {
  const h = Math.floor(mins / 60);
  const m = mins % 60;
  return h ? (m ? `${h} h ${m} min` : `${h} h`) : `${m} min`;
}
export function formatHours(mins: number) {
  if (mins % 60 !== 0) return formatDuration(mins);
  const h = mins / 60;
  return `${h} hour${h === 1 ? "" : "s"}`;
}

/** Next timed booking today at or after the provider's "now". */
export function nextItem({ trip, context }: AtlasState): TripItem | undefined {
  if (!isClock(context.time)) return undefined;
  const now = minutes(context.time);
  return trip.items.find((i) => i.day === context.day && i.isBooking && i.status !== "cancelled" && isClock(i.start) && minutes(i.start) >= now);
}

/** Rest of today: anything not finished at the provider's "now" (unknown times stay). */
export function restOfToday({ trip, context }: AtlasState): TripItem[] {
  if (!isClock(context.time)) return trip.items.filter((i) => i.day === context.day);
  const now = minutes(context.time);
  return trip.items.filter((i) => {
    if (i.day !== context.day) return false;
    const t = i.end ?? i.start;
    return !isClock(t) || minutes(t) >= now;
  });
}

export interface Headline {
  tone: "free" | "attention" | "open";
  lead: string;
  emphasis?: string;
  tail?: string;
}

/** Split a backend sentence pair: "You have 3 hours before dinner. Here are 3…" */
export function splitHeadline(s: string): [string, string | undefined] {
  const i = s.indexOf(". ");
  return i < 0 ? [s, undefined] : [s.slice(0, i + 1), s.slice(i + 2)];
}

const noun: Record<ItemKind, string> = { flight: "flight", transfer: "transfer", hotel: "hotel", free: "plan", restaurant: "dinner booking", activity: "plan" };

/** The Today headline is a direct rendering of the provider's trip state. */
export function headline({ state, trip }: AtlasState): Headline {
  switch (state.kind) {
    case "ON_PLAN": {
      const w = trip.freeWindow;
      if (w?.status === "valid" && w.headline) return { tone: "free", lead: splitHeadline(w.headline)[0] };
      if (w?.status === "valid") return { tone: "free", lead: "You have ", emphasis: formatHours(w.durationMinutes), tail: ` before ${w.to.noun}.` };
      return { tone: "open", lead: "Your afternoon is open." };
    }
    case "FLIGHT_CHANGED": {
      const item = state.attentionItemId ? trip.items.find((i) => i.id === state.attentionItemId) : undefined;
      return { tone: "attention", lead: "Your ", emphasis: item ? noun[item.kind] : "plan", tail: " needs attention." };
    }
    case "MISSING_TRANSPORT":
      // Matches Atlas: no transport between landing and the next confirmed place. Hotel arrival is unproven, not assumed.
      return { tone: "attention", lead: "There is ", emphasis: "no transport", tail: " after you land." };
    case "NO_PLAN":
      if (trip.items.some((i) => !i.isBooking && i.status === "tentative"))
        return { tone: "open", lead: "Your trip is taking shape." };
      return { tone: "open", lead: "Your afternoon is open." };
  }
}

export const headlineText = (h: Headline) => `${h.lead}${h.emphasis ?? ""}${h.tail ?? ""}`;

/** Planned (never observed) location, worded from exactly what the provider supplied. */
export function plannedLocationText(c: TripContext): string | undefined {
  const p = c.plannedLocation;
  if (!p) return c.plannedPlace ? `Planned location: ${c.plannedPlace}.` : undefined;
  const at = (a?: string, b?: string) => [a, b].filter(Boolean).join(", ");
  switch (p.status) {
    case "arrived": return `Planned arrival at ${at(p.place, p.locality) || "an unknown place"}${p.since ? ` since ${p.since}` : ""}.`;
    case "at": return `Planned at ${at(p.place, p.locality) || "an unknown place"}.`;
    case "in_transit": return `Planned in transit ${p.from ?? "?"} → ${p.to ?? "?"}${p.until ? ` until ${p.until}` : ""}.`;
    case "unknown":
      if (p.reason === "connection_not_proven") return "Planned location is uncertain because the connection is no longer proven.";
      return "No planned location is established yet.";
  }
}
