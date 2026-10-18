// API v2 adapter tests against frozen live responses (only the session token is substituted).
import { afterEach, describe, expect, it, vi } from "vitest";
import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import sample from "../../../../docs/atlas_api/01_get_demo_sample.json";
import transfer from "../../../../docs/atlas_api/02_post_import_sample_transfer.json";
import flight from "../../../../docs/atlas_api/03_post_import_sample_flight_change.json";
import trip from "../../../../docs/atlas_api/04_get_demo_trip.json";
import unreadable from "../../../../docs/atlas_api/06_post_import_unreadable.json";
import legacy from "../fixtures/api-sample.json";
import { ApiAtlasProvider, mapTrip } from "./api";
import { plannedLocationText } from "../summary";
import { demoStates } from "../demo-data";

describe("sample", () => {
  const s = mapTrip(sample.trip);
  it("planned arrival at TGD/Podgorica since 12:55, demo clock 13:00", () => {
    expect(s.context.plannedLocation).toEqual({ status: "arrived", place: "TGD", locality: "Podgorica", since: "12:55" });
    expect(plannedLocationText(s.context)).toBe("Planned arrival at TGD, Podgorica since 12:55.");
    expect(plannedLocationText(s.context)).not.toMatch(/Kotor/);
    expect([s.context.time, s.context.timezone, s.context.clock]).toEqual(["13:00", "Europe/Podgorica", "frozen_demo"]);
  });
  it("next item is the restaurant at 20:00, at risk (missing_transfer)", () => {
    expect(s.nextItem?.item.kind).toBe("restaurant");
    expect(s.nextItem?.item.start).toBe("20:00");
    expect(s.nextItem?.item.reference).toBe("R4417");
    expect(s.nextItem?.issues).toEqual(["missing_transfer"]);
    expect(s.nextItem?.minutesFromNow).toBe(420);
  });
});

describe("transfer", () => {
  const s = mapTrip(transfer.trip);
  it("next item is the transfer at 15:00", () => {
    expect([s.nextItem?.item.kind, s.nextItem?.item.start, s.nextItem?.item.reference, s.nextItem?.issues]).toEqual(["transfer", "15:00", "TR551", []]);
  });
  it("transfer detail from items", () => {
    const t = s.trip.bookings?.find((b) => b.reference === "TR551");
    expect(t?.facts).toEqual(expect.arrayContaining([
      { label: "Pickup", value: "TGD · Podgorica" }, { label: "Pickup time", value: "15:00" },
      { label: "Drop-off time", value: "16:00" }, { label: "Passengers", value: "2 passengers" },
    ]));
  });
});

describe("flight change", () => {
  const s = mapTrip(flight.trip);
  it("planned in transit LGW → TGD until 14:55", () => {
    expect(s.context.plannedLocation).toEqual({ status: "in_transit", from: "LGW", to: "TGD", until: "14:55" });
    expect(plannedLocationText(s.context)).toBe("Planned in transit LGW → TGD until 14:55.");
  });
  it("next transfer carries impossible_connection", () => {
    expect([s.nextItem?.item.kind, s.nextItem?.issues]).toEqual(["transfer", ["impossible_connection"]]);
  });
  it("18 Oct 16:30–19:30 on hold from disruption.windows_no_longer_provable", () => {
    expect(s.trip.heldWindows).toHaveLength(1);
    expect(s.trip.heldWindows?.[0]).toMatchObject({ status: "on_hold", start: "16:30", end: "19:30", durationMinutes: 180, holdReason: "Atlas can no longer prove when you reach the hotel.", to: { noun: "dinner" } });
  });
  it("later 19 Oct free window stays valid and separate", () => {
    expect(s.trip.freeWindow).toMatchObject({ status: "valid", day: "19 Oct" });
  });
  it("flight detail comes from items, revision and changes from disruption", () => {
    const f = s.trip.bookings?.find((b) => b.kind === "flight");
    expect([f?.start, f?.end, f?.reference]).toEqual(["11:15", "14:55", "DMX7Q2"]);
    expect(f?.changes).toEqual([{ field: "Arrives", from: "12:55", to: "14:55" }, { field: "Departs", from: "09:15", to: "11:15" }]);
    expect(f?.facts).toEqual(expect.arrayContaining([
      { label: "Flight", value: "DM1432" }, { label: "Cabin", value: "Economy" }, { label: "Passengers", value: "2 passengers" },
      { label: "From", value: "LGW · London · Europe/London" }, { label: "Duration", value: "160 min" },
      { label: "Revision", value: "Revision 2 of 2 · changed arrives, departs" },
    ]));
  });
});

describe("privacy and provenance", () => {
  it("passenger names never appear in live mode", () => {
    for (const r of [sample, transfer, flight, trip]) expect(JSON.stringify(mapTrip(r.trip))).not.toMatch(/Ana|Marko/);
  });
  it("live booking detail never borrows local fixture data", () => {
    const live = JSON.stringify(mapTrip(flight.trip).trip.bookings);
    const local = JSON.stringify(demoStates.FLIGHT_CHANGED.trip.items.flatMap((i) => i.facts ?? []).filter((f) => !live.includes(f.value)));
    expect(local.length).toBeGreaterThan(2); // demo has richer facts the live view must not show
    expect(live).not.toMatch(/Example|Ana|Marko/);
  });
  it("GET /demo/trip maps the same v2 context", () => {
    expect(mapTrip(trip.trip).context.plannedLocation?.status).toBeDefined();
  });
});

describe("v1 compatibility", () => {
  it("all v2 fields optional", () => {
    const s = mapTrip(legacy.trip);
    expect(s.nextItem).toBeUndefined();
    expect(s.trip.heldWindows).toBeUndefined();
    expect(s.context.plannedLocation).toBeUndefined();
    expect(s.trip.bookings).toHaveLength(3);
  });
});

describe("unreadable", () => {
  afterEach(() => vi.unstubAllGlobals());
  it("exact trust state", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(unreadable), { status: 200 })));
    expect(await ApiAtlasProvider.importBooking({ text: "hello" })).toMatchObject({ ok: false, message: "Atlas can't read this booking format yet.", detail: "No trip data was changed." });
  });
});

describe("no component makes booking/trip decisions", () => {
  it("screens never touch raw API fields", () => {
    const dirs = ["src/routes", "src/components/atlas"];
    const raw = /next_item|planned_location|windows_no_longer_provable|minutes_from_now|disruption\b|\.issues_added/;
    for (const d of dirs) for (const f of readdirSync(d, { recursive: true }) as string[])
      if (/\.tsx?$/.test(f)) expect(readFileSync(join(d, f), "utf8"), f).not.toMatch(raw);
  });
});
