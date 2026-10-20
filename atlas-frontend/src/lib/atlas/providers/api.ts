import type {
  ConfirmedTransport,
  MobilityComparison,
  MobilityCancellation,
  MobilityCancelResult,
  MobilityConfirmResult,
  MobilityQuote,
  MobilityTransaction,
  TransportChange,
} from "../domain";
// ApiAtlasProvider — talks to the existing Atlas HTTP API:
//   GET /demo/sample · POST /demo/import · GET /demo/trip/{session} · POST /demo/reset
// It only renames and normalizes fields into view models. It never decides fit, proof,
// free time, statuses or conflicts — those come from Python Atlas as-is.
import type {
  AtlasProvider, AtlasState, DiscoverySuggestion, FreeWindow, ImportResult, ImportedBooking,
  ItemKind, NextItem, NextStep, PlannedLocation, PlanningNeed, TripIssue, TripItem, TripState,
  RoutePlan,
} from "../domain";

/** "" = same origin (the Atlas service serves this frontend). Override with VITE_ATLAS_API_BASE. */
const BASE = ((import.meta.env["VITE_ATLAS_API_BASE"] as string | undefined) ?? "").replace(/\/$/, "");
const SESSION_KEY = "atlas.session";

/** Browser time zone is display context only. Python Atlas still owns travel facts. */
const clientTimezone = () => {
  if (typeof Intl === "undefined") return undefined;
  const tz = Intl.DateTimeFormat().resolvedOptions().timeZone;
  return typeof tz === "string" && tz ? tz : undefined;
};

// ---- session (sessionStorage only; never URL, UI, analytics or logs) -----------
const session = {
  get: () => (typeof window === "undefined" ? null : window.sessionStorage.getItem(SESSION_KEY)),
  set: (s: unknown) => { if (typeof s === "string" && s) window.sessionStorage.setItem(SESSION_KEY, s); },
  clear: () => { if (typeof window !== "undefined") window.sessionStorage.removeItem(SESSION_KEY); },
};

class HttpError extends Error {
  constructor(
    public status: number,
    message?: string,
    public code?: string,
  ) {
    super(message ?? `Atlas HTTP ${status}`);
    this.name = "HttpError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
  });

  if (!res.ok) {
    let message = `Atlas HTTP ${res.status}`;
    let code: string | undefined;

    try {
      const payload = (await res.json()) as Record<string, unknown>;

      if (
        typeof payload["message"] === "string" &&
        payload["message"].trim()
      ) {
        message = payload["message"];
      }

      if (
        typeof payload["code"] === "string" &&
        payload["code"].trim()
      ) {
        code = payload["code"];
      }
    } catch {
      // Non-JSON error response: HTTP status remains authoritative.
    }

    throw new HttpError(res.status, message, code);
  }

  return (await res.json()) as T;
}

// ---- API view-model (only what we read; everything optional) ---------------------
type Obj = Record<string, unknown>;
const str = (v: unknown) => (typeof v === "string" && v.trim() !== "" ? v : undefined);
const num = (v: unknown) => (typeof v === "number" ? v : undefined);
const arr = (v: unknown): Obj[] => (Array.isArray(v) ? v.filter((x): x is Obj => !!x && typeof x === "object") : []);
const obj = (v: unknown): Obj | undefined => (v && typeof v === "object" && !Array.isArray(v) ? (v as Obj) : undefined);

/** "09:15–12:55" → ["09:15","12:55"]; "20:00" → ["20:00"]. Pure string split. */
const span = (t: unknown): [string | undefined, string | undefined] => {
  const [a, b] = (str(t) ?? "").split(/[–-]/).map((x) => x.trim());
  return [a || undefined, b || undefined];
};

const KIND: Record<string, ItemKind> = {
  flight: "flight", transfer: "transfer", restaurant: "restaurant", free: "free",
  accommodation: "hotel", arrival: "hotel", check_in: "hotel", check_out: "hotel", activity: "activity",
};
const kindOf = (k: unknown): ItemKind => KIND[str(k) ?? ""] ?? "activity";

const ISSUE_KIND: Record<string, TripIssue["kind"]> = { missing_transfer: "missing_transport", impossible_connection: "may_not_fit" };

function mapItems(days: Obj[]): TripItem[] {
  return days.flatMap((d, di) =>
    arr(d["itinerary"]).map((e, ei) => {
      const [start, end] = span(e["time"]);
      const kind = kindOf(e["kind"]);
      const status: TripItem["status"] = kind === "free" ? "planned" : e["assumed"] === true ? "tentative" : "listed";
      return {
        id: `it-${di}-${ei}`, kind, status, isBooking: false,
        day: str(d["label"]) ?? str(d["date"]) ?? "", city: "",
        title: str(e["label"]) ?? "", subtitle: str(e["detail"]), start, end,
      };
    }),
  );
}

function mapBookings(bookings: Obj[]): TripItem[] {
  return bookings.map((b, i) => ({
    id: `bk-${i}`, kind: kindOf(b["kind"]),
    status: str(b["status"]) === "confirmed" ? "confirmed" : str(b["status"]) === "cancelled" ? "cancelled" : "listed",
    isBooking: true, day: "", city: "",
    title: str(b["title"]) ?? "", subtitle: str(b["when"]),
    provider: str(b["provider"]), reference: str(b["reference"]),
  }));
}

function mapIssues(attention: Obj[]): TripIssue[] {
  return attention.map((a, i) => {
    const lines = Array.isArray(a["lines"]) ? (a["lines"] as unknown[]).map(str).filter((x): x is string => !!x) : [];
    const why = [str(a["status"]), str(a["plan"])].filter(Boolean).join(". ").replace(/\.\./g, ".");
    return {
      id: `att-${i}`, kind: ISSUE_KIND[str(a["kind"]) ?? ""] ?? "conflict",
      title: str(a["headline"]) ?? "", what: lines.join(" "), lines, why: why || undefined, relatedIds: [],
    };
  });
}

function mapWindow(a: Obj | undefined): FreeWindow | undefined {
  const w = obj(a?.["window"]);
  if (!a || !w) return undefined;
  const [start, end] = span(w["time"]);
  if (!start || !end) return undefined;
  const endsAt = str(w["ends_at"]);
  const buffer = arr(w["assumptions"]).find((x) => x["policy"] === "reservation_buffer_minutes" && x["at"] === "end");
  const suggestions: DiscoverySuggestion[] = arr(a["suggestions"]).map((s, i) => ({
    id: `sg-${i}-${str(s["name"]) ?? ""}`,
    name: str(s["name"]) ?? "",
    category: str(s["kind"]),
    travel: { fromMinutes: num(s["walk_there"]) ?? null, toMinutes: num(s["walk_on"]) ?? null, mode: "walk" },
    visit: { start: str(s["visit_from"]) ?? "", end: str(s["visit_to"]) ?? "" },
    leaveAt: str(s["leave_hotel"]),
    arriveNextAt: str(s["arrive_dinner"]),
    visitMinutes: num(s["visit_minutes"]),
  }));
  return {
    id: `fw-${str(a["day"]) ?? ""}-${start}`,
    status: "valid",
    start, end,
    durationMinutes: num(w["minutes"]) ?? 0,
    from: { place: str(w["from"]) ?? "", noun: "start" },
    to: { place: str(w["to"]) ?? "", noun: endsAt === "reservation" ? "reservation" : endsAt === "sleep" ? "end of day" : "next" },
    suggestions,
    headline: str(a["headline"]),
    day: str(a["day"]),
    endBufferMinutes: num(buffer?.["minutes"]),
    considered: num(a["considered"]),
    leftOut: num(a["left_out"]),
  };
}

/** Naming normalization of the backend's attention kinds — not a decision. */
function stateOf(attention: Obj[], bookings: Obj[]): TripState {
  const kinds = attention.map((a) => str(a["kind"]));
  if (kinds.includes("impossible_connection")) return { kind: "FLIGHT_CHANGED" };
  if (kinds.includes("missing_transfer")) return { kind: "MISSING_TRANSPORT" };
  return bookings.length ? { kind: "ON_PLAN" } : { kind: "NO_PLAN" };
}

// ---- API v2 (context, next_item, items, disruption) — all optional ----------------
/** "2026-10-18T12:55" → "12:55". Pure string slice. */
const hhmm = (v: unknown) => str(v)?.match(/T(\d{2}:\d{2})/)?.[1];
const local = (v: unknown) => hhmm(obj(v)?.["local"]);
const dateOf = (v: unknown) => str(obj(v)?.["local"])?.slice(0, 10);
const plural = (n: number | undefined, w: string) => (n === undefined ? undefined : `${n} ${w}${n === 1 ? "" : "s"}`);
const join = (...xs: (string | undefined)[]) => xs.filter(Boolean).join(" · ") || undefined;
const FIELD: Record<string, string> = { departs: "Departs", arrives: "Arrives" };

function factsOf(kind: string | undefined, d: Obj, rev: Obj | undefined): TripItem["facts"] {
  const o = obj(d["origin"]), t = obj(d["destination"]), pu = obj(d["pickup"]), dr = obj(d["dropoff"]);
  const ci = obj(d["check_in"]), co = obj(d["check_out"]);
  const cf = Array.isArray(rev?.["changed_fields"]) ? (rev!["changed_fields"] as unknown[]).map(str).filter(Boolean) : [];
  const revision = num(rev?.["count"]) && num(rev?.["count"])! > 1 ? `Revision ${num(rev?.["current"])} of ${num(rev?.["count"])}${cf.length ? ` · changed ${cf.join(", ")}` : ""}` : undefined;
  const rows: [string, string | undefined][] =
    kind === "flight" ? [
      ["Carrier", str(d["carrier"])], ["Flight", str(d["flight_number"])], ["Cabin", str(d["cabin"])],
      ["Passengers", plural(num(d["passengers"]), "passenger")],
      ["From", join(str(o?.["code"]), str(o?.["city"]), str(o?.["timezone"]))],
      ["To", join(str(t?.["code"]), str(t?.["city"]), str(t?.["timezone"]))],
      ["Departs", join(dateOf(d["departs"]), local(d["departs"]))], ["Arrives", join(dateOf(d["arrives"]), local(d["arrives"]))],
      ["Duration", num(d["duration_minutes"]) !== undefined ? `${num(d["duration_minutes"])} min` : undefined],
      ["Revision", revision],
    ] : kind === "accommodation" ? [
      ["Address", str(d["address"])], ["Locality", str(d["locality"])], ["Room", str(d["room"])],
      ["Guests", plural(num(d["guests"]), "guest")],
      ["Check-in", join(str(ci?.["date"]), str(ci?.["from"]) && `from ${str(ci?.["from"])}`)],
      ["Check-out", join(str(co?.["date"]), str(co?.["until"]) && `until ${str(co?.["until"])}`)],
      ["Nights", num(d["nights"])?.toString()], ["Revision", revision],
    ] : kind === "transfer" ? [
      ["Pickup", join(str(pu?.["place"]), str(pu?.["locality"]))], ["Pickup time", local(d["pickup_at"])],
      ["Drop-off", join(str(dr?.["place"]), str(dr?.["locality"]))], ["Drop-off time", local(d["dropoff_at"])],
      ["Passengers", plural(num(d["passengers"]), "passenger")], ["Revision", revision],
    ] : kind === "restaurant" ? [
      ["Venue", str(d["venue"])], ["Address", str(d["address"])], ["Locality", str(d["locality"])],
      ["Party", num(d["party"])?.toString()], ["Starts", join(dateOf(d["starts"]), local(d["starts"]))], ["Revision", revision],
    ] : [];
  return rows.filter((r): r is [string, string] => !!r[1]).map(([label, value]) => ({ label, value }));
}

/** trip.items → booking views. Titles come from trip.bookings (same reference); nothing is invented. */
function mapItemsV2(items: Obj[], bookings: Obj[], disruption: Obj | undefined): TripItem[] {
  const cause = obj(disruption?.["cause"]);
  return items.map((it, i) => {
    const k = str(it["kind"]);
    const d = obj(it["detail"]) ?? {};
    if (k === "plan") {
      const event = str(d["event"]);
      const place = str(d["place"]);
      const origin = str(d["origin_text"]);
      const date = str(d["date_text"]);
      const timing = str(d["timing_text"]);
      const duration = str(d["duration_text"]);
      const note = str(d["note"]);
      const when = date ?? timing ?? duration;

      return {
        id: `plan-${i}`,
        key: str(it["key"]),
        kind: "activity" as const,
        status: "tentative" as const,
        isBooking: false,
        day: when ?? "",
        city: place ?? "",
        title: event ?? "Stated plan",
        subtitle: when ?? (origin ? `From ${origin}` : undefined),
        facts: [
          ["Event", event],
          ["From", origin],
          ["Place", place],
          ["Date", date],
          ["Timing", timing],
          ["Duration", duration],
          ["Note", note],
          ["Status", "Stated"],
        ]
          .filter((x): x is [string, string] => !!x[1])
          .map(([label, value]) => ({ label, value })),
      } satisfies TripItem & { key?: string | undefined };
    }
    const b = bookings.find((x) => str(x["reference"]) && x["reference"] === it["reference"]);
    const [start, end] =
      k === "flight" ? [local(d["departs"]), local(d["arrives"])]
      : k === "transfer" ? [local(d["pickup_at"]), local(d["dropoff_at"])]
      : k === "restaurant" ? [local(d["starts"]), undefined]
      : k === "accommodation" ? [str(obj(d["check_in"])?.["from"]), undefined] : [undefined, undefined];
    const prev = obj(cause?.["previous"]), cur = obj(cause?.["current"]);
    const changes = cause && cause["key"] === it["key"] && Array.isArray(cause["changed_fields"])
      ? (cause["changed_fields"] as unknown[]).map(str).filter((f): f is string => !!f)
          .map((f) => ({ field: FIELD[f] ?? f, from: hhmm(prev?.[f]) ?? "", to: hhmm(cur?.[f]) ?? "" })).filter((c) => c.from && c.to)
      : undefined;
    const status = str(it["status"]);
    return {
      id: `bk-${i}`, key: str(it["key"]), kind: kindOf(k),
      status: status === "confirmed" ? "confirmed" : status === "cancelled" ? "cancelled" : "listed",
      isBooking: true, day: "", city: str(d["locality"]) ?? "",
      title: str(b?.["title"]) ?? str(d["venue"]) ?? str(it["provider"]) ?? "",
      subtitle: str(b?.["when"]), start, end,
      provider: str(it["provider"]), reference: str(it["reference"]),
      facts: factsOf(k, d, obj(it["revision"])),
      changes: changes?.length ? changes : undefined,
    } satisfies TripItem & { key?: string | undefined };
  });
}

function mapPlanned(p: Obj | undefined): PlannedLocation | undefined {
  switch (str(p?.["status"])) {
    case "arrived": return { status: "arrived", place: str(p!["place"]), locality: str(p!["locality"]), since: local(p!["since"]) };
    case "at": return { status: "at", place: str(p!["place"]), locality: str(p!["locality"]) };
    case "in_transit": return { status: "in_transit", from: str(p!["from"]), to: str(p!["to"]), until: local(p!["until"]) };
    case "unknown": return { status: "unknown", reason: str(p!["reason"]) };
    default: return undefined;
  }
}

function mapNextItem(n: Obj | undefined, items: (TripItem & { key?: string | undefined })[]): NextItem | undefined {
  if (!n) return undefined;
  const item = items.find((i) => i.key && i.key === n["key"]) ?? {
    id: `next-${str(n["key"]) ?? ""}`, kind: kindOf(n["kind"]), status: "listed" as const, isBooking: false,
    day: "", city: str(n["locality"]) ?? "", title: str(n["place"]) ?? "",
  };
  const start = local(n["starts"]);
  return {
    item: { ...item, start: start ?? item.start, subtitle: str(n["place"]) ?? item.subtitle },
    minutesFromNow: num(n["minutes_from_now"]),
    issues: Array.isArray(n["issues"]) ? (n["issues"] as unknown[]).map(str).filter((x): x is string => !!x) : [],
  };
}

const HOLD_REASON: Record<string, string> = { start_anchor_not_proven: "Atlas can no longer prove when you reach the hotel." };
const ANCHOR_NOUN: Record<string, string> = { check_in: "hotel", reservation: "dinner" };

function mapHeld(disruption: Obj | undefined): FreeWindow[] | undefined {
  const ws = arr(disruption?.["windows_no_longer_provable"]);
  if (!ws.length) return undefined;
  return ws.map((w, i) => {
    const sa = obj(w["start_anchor"]), ea = obj(w["end_anchor"]);
    return {
      id: `held-${i}-${str(w["start"]) ?? ""}`, status: "on_hold" as const,
      holdReason: HOLD_REASON[str(w["reason"]) ?? ""],
      start: hhmm(w["start"]) ?? "", end: hhmm(w["end"]) ?? "",
      durationMinutes: num(w["minutes"]) ?? 0,
      from: { place: str(sa?.["place"]) ?? "", noun: ANCHOR_NOUN[str(sa?.["role"]) ?? ""] ?? "start" },
      to: { place: str(ea?.["place"]) ?? "", noun: ANCHOR_NOUN[str(ea?.["role"]) ?? ""] ?? "next booking" },
      suggestions: [],
    };
  });
}

function mapPlanningNeeds(items: Obj[]): PlanningNeed[] {
  return items.flatMap((it, itemIndex) => {
    if (str(it["kind"]) !== "plan") return [];

    const detail = obj(it["detail"]);
    const planning = obj(detail?.["planning"]);
    const planKey = str(it["key"]);

    return arr(planning?.["needs"]).map((need, needIndex) => ({
      id: `${planKey ?? `plan-${itemIndex}`}:${str(need["code"]) ?? needIndex}`,
      planKey,
      code: str(need["code"]) ?? "unknown",
      category: str(need["category"]) ?? "unknown",
      state: str(need["state"]) ?? "unknown",
      basis: str(need["basis"]),
    }));
  });
}

function mapRoutePlan(value: unknown): RoutePlan | undefined {
  const r = obj(value);
  if (!r) return undefined;

  const origin = obj(r["origin"]);
  const destination = obj(r["destination"]);
  const originCoordinates = obj(origin?.["coordinates"]);
  const destinationCoordinates = obj(destination?.["coordinates"]);

  const originName = str(origin?.["name"]);
  const destinationName = str(destination?.["name"]);

  const distanceKm = num(r["distance_km"]);
  const routeDurationMinutes = num(r["route_duration_minutes"]);
  const planningBufferMinutes = num(r["planning_buffer_minutes"]);

  const arriveByLocal = str(r["arrive_by_local"]);
  const leaveByLocal = str(r["leave_by_local"]);
  const timezone = str(r["timezone"]);

  if (
    !originName ||
    !destinationName ||
    distanceKm === undefined ||
    routeDurationMinutes === undefined ||
    planningBufferMinutes === undefined ||
    !arriveByLocal ||
    !leaveByLocal ||
    !timezone
  ) {
    return undefined;
  }

  function coords(value: Obj | undefined) {
    const lat = num(value?.["lat"]);
    const lng = num(value?.["lon"]);

    if (lat === undefined || lng === undefined) {
      return undefined;
    }

    return { lat, lng };
  }

  return {
    status: "estimated",
    mode: "driving",

    origin: {
      name: originName,
      locality: str(origin?.["locality"]),
      coords: coords(originCoordinates),
    },

    destination: {
      name: destinationName,
      locality: str(destination?.["locality"]),
      coords: coords(destinationCoordinates),
    },

    distanceKm,
    routeDurationMinutes,
    planningBufferMinutes,

    arriveByLocal,
    leaveByLocal,
    timezone,

    trafficLive: r["traffic_live"] === true,
    provider: str(r["provider"]),
    providerGuarantee: r["provider_guarantee"] === true,
    policyVersion: str(r["policy_version"]),
    basis: str(r["basis"]),
    generatedAt: str(r["generated_at"]),
  };
}

export function mapMobilityComparison(raw: unknown): MobilityComparison {
  const data = obj(raw) ?? {};
  const request = obj(data["request"]) ?? {};

  return {
    request: {
      origin: str(request["origin"]) ?? "",
      destination: str(request["destination"]) ?? "",
      partySize: num(request["party_size"]),
      arriveByLocal: str(request["arrive_by_local"]) ?? "",
      leaveByLocal: str(request["leave_by_local"]),
      timezone: str(request["timezone"]) ?? "UTC",
    },

    modes: arr(data["modes"]).map((rawMode) => {
      const mode = obj(rawMode) ?? {};

      return {
        mode: (str(mode["mode"]) ?? "private_transfer") as MobilityComparison["modes"][number]["mode"],
        serviceType: str(mode["service_type"]) ?? "",
        status: (str(mode["status"]) ?? "unavailable") as MobilityComparison["modes"][number]["status"],
        reason: str(mode["reason"]) ?? "",
        providers: arr(mode["providers"]).map((rawProvider) => {
          const provider = obj(rawProvider) ?? {};

          return {
            id: str(provider["id"]) ?? "",
            slug: str(provider["slug"]) ?? "",
            name: str(provider["name"]) ?? "",
            sandbox: provider["sandbox"] === true,
          };
        }),
        quoteCreated: mode["quote_created"] === true,
        bookingCreated: mode["booking_created"] === true,
      };
    }),
  };
}

function mapConfirmedTransport(
  raw: unknown,
): ConfirmedTransport | undefined {
  const value = obj(raw);

  if (!value) return undefined;

  const status = str(value["status"]);
  const provider = str(value["provider"]);

  if (!status || !provider) return undefined;

  return {
    status,
    provider,
    providerReference:
      str(value["provider_reference"]),
    pickup: str(value["pickup"]),
    destination: str(value["destination"]),
    pickupTime: str(value["pickup_time"]),
    partySize: num(value["party_size"]),
    amount: str(value["amount"]),
    currency: str(value["currency"]),
    sandbox: value["sandbox"] === true,
  };
}


function mapMobilityTransaction(
  raw: unknown,
): MobilityTransaction {
  const tx = obj(raw) ?? {};

  return {
    id: str(tx["id"]) ?? "",
    actionId: str(tx["action_id"]) ?? "",
    quoteId: str(tx["quote_id"]) ?? "",
    status: str(tx["status"]) ?? "",
    provider: str(tx["provider"]) ?? "",
    providerReference:
      str(tx["provider_reference"]),
    serviceType:
      str(tx["service_type"]) ?? "",
    amount: str(tx["amount"]) ?? "0.00",
    currency: str(tx["currency"]) ?? "",
    sandbox: tx["sandbox"] === true,
    transactionCreated:
      tx["transaction_created"] === true,
    providerAccepted:
      tx["provider_accepted"] === true,
    realWorld: tx["real_world"] === true,
  };
}


function mapMobilityQuote(raw: unknown): MobilityQuote {
  const q = obj(raw) ?? {};

  return {
    id: str(q["id"]) ?? "",
    code: str(q["code"]) ?? "",
    status: str(q["status"]) ?? "",
    provider: str(q["provider"]) ?? "",
    sandbox: q["sandbox"] === true,
    serviceType: str(q["service_type"]) ?? "",
    amount: str(q["amount"]) ?? "0.00",
    currency: str(q["currency"]) ?? "",
    description: str(q["description"]),
    conditions: str(q["conditions"]),
    validUntil: str(q["valid_until"]) ?? "",
    request: obj(q["request"]) ?? {},
    bookingCreated: q["booking_created"] === true,
    transactionCreated: q["transaction_created"] === true,
  };
}


function mapCancellation(
  raw: unknown,
): MobilityCancellation | undefined {
  const c = obj(raw);
  if (!c) return undefined;

  const policy = obj(c["policy"]);

  return {
    transactionId: str(c["transaction_id"]) ?? "",
    transactionStatus: str(c["transaction_status"]) ?? "",
    cancellable: c["cancellable"] === true,
    policy: {
      status: str(policy?.["status"]) ?? "unknown",
      deadline: str(policy?.["deadline"]),
      fee: str(policy?.["fee"]),
    },
    provider: str(c["provider"]) ?? "",
    providerReference: str(c["provider_reference"]),
    sandbox: c["sandbox"] === true,
  };
}

function mapTransportChange(
  raw: unknown,
): TransportChange | undefined {
  const change = obj(raw);
  if (!change) return undefined;

  const status = str(change["status"]);

  if (
    status !== "completed" &&
    status !== "needs_attention"
  ) return undefined;

  return {
    status,
    reason: str(change["reason"]),
    oldTransactionStatus:
      str(change["old_transaction_status"]),
    newTransactionStatus:
      str(change["new_transaction_status"]),
    oldProviderReference:
      str(change["old_provider_reference"]),
  };
}

export function mapTrip(raw: unknown): AtlasState {
  const t = obj(raw) ?? {};
  const days = arr(t["days"]);
  const bookings = arr(t["bookings"]);
  const attention = arr(t["attention"]);
  const ctx = obj(t["context"]);
  const now = obj(ctx?.["now"]);
  const disruption = obj(t["disruption"]);

  const rawItems = arr(t["items"]);
  const planningNeeds = mapPlanningNeeds(rawItems);

  const rawPlan = rawItems.find(
    (item) => str(item["kind"]) === "plan",
  );
  const rawPlanDetail = obj(rawPlan?.["detail"]);
  const routePlan = mapRoutePlan(rawPlanDetail?.["route_plan"]);

  const v2Items = Array.isArray(t["items"])
    ? mapItemsV2(rawItems, bookings, disruption)
    : undefined;
  const v2Plans = v2Items?.filter((i) => i.isBooking === false) ?? [];
  const v2Bookings = v2Items?.filter((i) => i.isBooking !== false);
  const planPlaces = v2Plans.map((i) => i.city).filter((x): x is string => !!x);
  const planDates = v2Plans.map((i) => i.subtitle).filter((x): x is string => !!x);
  const nowDate = str(now?.["local"])?.slice(0, 10);
  const route = Array.isArray(t["route"]) ? (t["route"] as unknown[]).map(str).filter((x): x is string => !!x) : [];
  const displayRoute = route.length ? route : [...new Set(planPlaces)];
  const next: NextStep[] = arr(t["next"]).map((n) => ({ id: str(n["id"]) ?? "", label: str(n["label"]) ?? "", hint: str(n["hint"]) })).filter((n) => n.id);
  return {
    state: stateOf(attention, bookings),
    context: {
      day: str((days.find((d) => nowDate && d["date"] === nowDate) ?? days[0])?.["label"]) ?? "",
      time: hhmm(now?.["local"]),
      timezone: str(now?.["timezone"]),
      plannedLocation: mapPlanned(obj(ctx?.["planned_location"])),
      clock: str(now?.["clock"]) === "demo" ? "frozen_demo" : "live",
    },
    trip: {
      id: "session", title: route[route.length - 1] ?? planPlaces[0] ?? "Your trip",
      dates: str(t["dates"]) ?? planDates[0] ?? "", route: displayRoute,
      items: [...v2Plans, ...mapItems(days)], issues: mapIssues(attention), planningNeeds, routePlan, freeWindow: mapWindow(obj(t["activities"])),
      places: [],
      bookings: v2Bookings ?? mapBookings(bookings),
      heldWindows: mapHeld(disruption),
      confirmedTransport: mapConfirmedTransport(
        rawPlanDetail?.["confirmed_transport"],
      ),
      transportPlanKey: str(rawPlan?.["key"]),
      transportChange: mapTransportChange(
        rawPlanDetail?.["transport_change"],
      ),
    },
    next,
    nextItem: v2Items ? mapNextItem(obj(t["next_item"]), v2Items) : undefined,
    session: "active",
  };
}

const empty = (s: "none" | "ended"): AtlasState => ({
  state: { kind: "NO_PLAN" }, context: { day: "", clock: "live" }, session: s,
  trip: { id: "none", title: "No trip yet", dates: "", route: [], items: [], issues: [], places: [] },
});

function mapUnderstood(u: unknown): ImportedBooking[] {
  return arr(Array.isArray(u) ? u : [u]).filter((b) => b["outcome"] !== "unreadable").map((b) => ({
    kind: kindOf(b["kind"]),
    title: str(b["title"]),
    outcome: b["outcome"] === "updated" ? "updated" : b["outcome"] === "new" ? "new" : undefined,
    isBooking: str(b["kind"]) !== "plan",
    facts: ([["Provider", b["provider"]], ["Place", b["route"]], ["When", b["when"]], ["Status", b["status"]]] as const)
      .map(([label, v]) => ({ label, value: str(v) }))
      .filter((f): f is { label: "Provider" | "Place" | "When" | "Status"; value: string } => !!f.value),
    changes: Array.isArray(b["changes"]) ? (b["changes"] as unknown[]).map(str).filter((x): x is string => !!x) : undefined,
  }));
}

function toResult(data: Obj): ImportResult {
  const u = data["understood"];
  const unreadable = obj(u)?.["outcome"] === "unreadable";
  if (unreadable || !data["trip"]) {
    const o = obj(u);
    return { ok: false, reason: "unsupported", message: str(o?.["message"]) ?? "Atlas can’t read this booking format yet.", detail: str(o?.["detail"]) ?? "No trip data was changed." };
  }
  session.set(data["session"]);
  return { ok: true, bookings: mapUnderstood(u), state: mapTrip(data["trip"]) };
}

const failed = (e: unknown): ImportResult => {
  if (e instanceof HttpError && e.status === 404) { session.clear(); return { ok: false, reason: "ended", message: "This Atlas session has ended." }; }
  return { ok: false, reason: "unreachable", message: "Atlas couldn’t reach the service right now.", detail: "No trip data was changed." };
};

export const ApiAtlasProvider: AtlasProvider = {
  scenarios: [],
  live: true,
  async getState() {
    const s = session.get();
    if (!s) return empty("none");
    try {
      const data = await request<Obj>(`/demo/trip/${encodeURIComponent(s)}`);
      return mapTrip(data["trip"]);
    } catch (e) {
      if (e instanceof HttpError && e.status === 404) { session.clear(); return empty("ended"); }
      throw e;
    }
  },
  async importBooking(input) {
    try {
      const tz = clientTimezone();
      const body = { ...input, ...(session.get() ? { session: session.get() } : {}), ...(tz ? { client_timezone: tz } : {}) };
      return toResult(await request<Obj>("/demo/import", { method: "POST", body: JSON.stringify(body) }));
    } catch (e) { return failed(e); }
  },
  async startSample() {
    try { return toResult(await request<Obj>("/demo/sample")); } catch (e) { return failed(e); }
  },
  async answerPlaceRole(
    planKey: string,
    role: "venue" | "stay" | "both",
  ) {
    const current = session.get();

    if (!current) {
      return empty("none");
    }

    try {
      const data = await request<Obj>("/demo/plan/role", {
        method: "POST",
        body: JSON.stringify({
          session: current,
          plan_key: planKey,
          role,
        }),
      });

      session.set(data["session"]);
      return mapTrip(data["trip"]);
    } catch (e) {
      if (e instanceof HttpError && e.status === 404) {
        session.clear();
        return empty("ended");
      }

      throw e;
    }
  },

  async answerPlanDate(planKey: string, value: string) {
    const s = session.get();
    if (!s) return empty("none");

    const data = await request<Obj>("/demo/plan/date", {
      method: "POST",
      body: JSON.stringify({
        session: s,
        plan_key: planKey,
        value,
      }),
    });

    session.set(data["session"]);
    return mapTrip(data["trip"]);
  },

  async answerStayIntent(planKey: string, value: boolean) {
    const s = session.get();
    if (!s) return empty("none");

    const data = await request<Obj>("/demo/plan/stay-intent", {
      method: "POST",
      body: JSON.stringify({
        session: s,
        plan_key: planKey,
        value,
      }),
    });

    session.set(data["session"]);
    return mapTrip(data["trip"]);
  },

  async answerOrigin(planKey: string, origin: string) {
    const s = session.get();
    if (!s) return empty("none");

    const data = await request<Obj>("/demo/plan/origin", {
      method: "POST",
      body: JSON.stringify({
        session: s,
        plan_key: planKey,
        origin,
      }),
    });

    session.set(data["session"]);
    return mapTrip(data["trip"]);
  },

  async answerArrivalTime(
    planKey: string,
    value: string,
  ) {
    const current = session.get();

    if (!current) return empty("none");

    try {
      const data = await request<Obj>("/demo/plan/arrival-time", {
        method: "POST",
        body: JSON.stringify({
          session: current,
          plan_key: planKey,
          value,
        }),
      });

      session.set(data["session"]);
      return mapTrip(data["trip"]);
    } catch (e) {
      if (e instanceof HttpError && e.status === 404) {
        session.clear();
        return empty("ended");
      }

      throw e;
    }
  },

  async answerPartySize(
    planKey: string,
    value: number,
  ) {
    const current = session.get();

    if (!current) return empty("none");

    const data = await request<Obj>("/demo/plan/party-size", {
      method: "POST",
      body: JSON.stringify({
        session: current,
        plan_key: planKey,
        value,
      }),
    });

    session.set(data["session"]);
    return mapTrip(data["trip"]);
  },

  async compareMobility(planKey: string) {
    const current = session.get();

    if (!current) {
      throw new Error("Atlas session has ended.");
    }

    const data = await request<Obj>("/demo/plan/mobility", {
      method: "POST",
      body: JSON.stringify({
        session: current,
        plan_key: planKey,
      }),
    });

    session.set(data["session"]);

    return mapMobilityComparison(data["mobility"]);
  },

  async getActiveTransferQuote(planKey: string) {
    const current = session.get();

    if (!current) return undefined;

    const data = await request<Obj>(
      "/demo/plan/transport-state",
      {
        method: "POST",
        body: JSON.stringify({
          session: current,
          plan_key: planKey,
        }),
      },
    );

    session.set(data["session"]);

    return data["quote"]
      ? mapMobilityQuote(data["quote"])
      : undefined;
  },

  async getTransferQuote(planKey: string) {
    const current = session.get();

    if (!current) {
      throw new Error("Atlas session has ended.");
    }

    const data = await request<Obj>("/demo/plan/transfer-quote", {
      method: "POST",
      body: JSON.stringify({
        session: current,
        plan_key: planKey,
      }),
    });

    session.set(data["session"]);

    return mapMobilityQuote(data["quote"]);
  },

  async getTransferCancellation(planKey: string) {
    const current = session.get();

    if (!current) {
      throw new Error("Atlas session has ended.");
    }

    const data = await request<Obj>(
      "/demo/plan/transfer-cancellation",
      {
        method: "POST",
        body: JSON.stringify({
          session: current,
          plan_key: planKey,
        }),
      },
    );

    session.set(data["session"]);
    return mapCancellation(data["cancellation"]);
  },

  async cancelTransfer(
    planKey: string,
    transactionId: string,
  ): Promise<MobilityCancelResult> {
    const current = session.get();

    if (!current) {
      throw new Error("Atlas session has ended.");
    }

    const data = await request<Obj>(
      "/demo/plan/transfer-cancel",
      {
        method: "POST",
        body: JSON.stringify({
          session: current,
          plan_key: planKey,
          transaction_id: transactionId,
          consent: true,
        }),
      },
    );

    session.set(data["session"]);

    const transaction = mapMobilityTransaction(
      data["transaction"],
    );

    return {
      transaction,
      state: mapTrip(data["trip"]),
      pending:
        transaction.status !== "cancelled"
        || data["reason"] === "cancellation_pending",
    };
  },

  async confirmTransfer(
    planKey: string,
    quoteId: string,
  ): Promise<MobilityConfirmResult> {
    const current = session.get();

    if (!current) {
      throw new Error("Atlas session has ended.");
    }

    const data = await request<Obj>(
      "/demo/plan/transfer-confirm",
      {
        method: "POST",
        body: JSON.stringify({
          session: current,
          plan_key: planKey,
          quote_id: quoteId,
          consent: true,
        }),
      },
    );

    session.set(data["session"]);

    const state = mapTrip(data["trip"]);

    // The endpoint can return an explicit fail-closed change
    // result even if a trip projection is unavailable.
    const responseChange = mapTransportChange(
      data["change"],
    );

    if (!state.trip.transportChange && responseChange) {
      state.trip.transportChange = responseChange;
    }

    return {
      transaction:
        mapMobilityTransaction(
          data["transaction"],
        ),
      state,
    };
  },

  async planRoute(planKey: string) {
    const current = session.get();

    if (!current) return empty("none");

    try {
      const data = await request<Obj>("/demo/plan/route", {
        method: "POST",
        body: JSON.stringify({
          session: current,
          plan_key: planKey,
        }),
      });

      session.set(data["session"]);
      return mapTrip(data["trip"]);
    } catch (e) {
      if (e instanceof HttpError && e.status === 404) {
        session.clear();
        return empty("ended");
      }

      throw e;
    }
  },

  async reset() {
    const s = session.get();
    session.clear();
    if (s) await request("/demo/reset", { method: "POST", body: JSON.stringify({ session: s }) }).catch(() => undefined);
  },
};
