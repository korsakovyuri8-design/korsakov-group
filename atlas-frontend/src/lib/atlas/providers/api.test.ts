// Real responses captured from atlas.korsakovgroup.com (session token redacted).
import { afterEach, describe, expect, it, vi } from "vitest";
import sample from "../fixtures/api-sample.json";
import transfer from "../fixtures/api-transfer.json";
import flightChange from "../fixtures/api-flight-change.json";
import unreadable from "../fixtures/api-unreadable.json";
import { ApiAtlasProvider, mapTrip } from "./api";
import { headlineText, headline } from "../summary";

describe("GET /demo/sample", () => {
  const s = mapTrip(sample.trip);
  it("route, dates, 3 bookings", () => {
    expect(s.trip.route).toEqual(["London", "Podgorica", "Kotor"]);
    expect(s.trip.dates).toBe("18–21 October");
    expect(s.trip.bookings).toHaveLength(3);
  });
  it("missing transport, verbatim backend lines, no hotel arrival asserted", () => {
    expect(s.state.kind).toBe("MISSING_TRANSPORT");
    expect(s.trip.issues[0]?.lines).toEqual([
      "You land in Podgorica at 12:55.",
      "Your next confirmed place is dinner at Konoba Demo in Kotor at 20:00.",
      "There is no transport between them.",
    ]);
    expect(s.trip.items.some((i) => i.title.startsWith("Hotel arrival"))).toBe(false);
  });
  it("offers Arrange transfer", () => expect(s.next?.map((n) => n.id)).toEqual(["transfer"]));
});

describe("POST /demo/import sample=transfer", () => {
  const s = mapTrip(transfer.trip);
  it("3 hours before dinner, 3 things that fit", () => {
    expect(s.state.kind).toBe("ON_PLAN");
    expect(headlineText(headline(s))).toBe("You have 3 hours before dinner.");
    expect(s.trip.freeWindow?.suggestions.map((x) => x.name)).toEqual(["City Walls Lookout", "Maritime Museum", "Old Town Cathedral"]);
  });
  it("renders Atlas values, not recomputed ones", () => {
    const c = s.trip.freeWindow?.suggestions[0];
    expect([c?.travel.fromMinutes, c?.travel.toMinutes, c?.visit.start, c?.visit.end, c?.arriveNextAt]).toEqual([4, 7, "16:34", "18:04", "18:11"]);
    expect(c?.hours).toBeUndefined();
  });
});

describe("POST /demo/import sample=flight_change", () => {
  const s = mapTrip(flightChange.trip);
  it("flight changed with backend connection issue", () => {
    expect(s.state.kind).toBe("FLIGHT_CHANGED");
    expect(s.trip.issues[0]?.title).toBe("A connection may no longer work");
    expect(s.trip.items[0]?.start).toBe("11:15");
  });
});

describe("unreadable import", () => {
  afterEach(() => vi.unstubAllGlobals());
  it("shows the exact trust state and changes nothing", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(unreadable), { status: 200 })));
    const r = await ApiAtlasProvider.importBooking({ text: "hello" });
    expect(r).toMatchObject({ ok: false, reason: "unsupported", message: "Atlas can't read this booking format yet.", detail: "No trip data was changed." });
  });
});


describe("live import clock context", () => {
  afterEach(() => vi.unstubAllGlobals());
  it("sends the browser timezone with a real import", async () => {
    let sent: Record<string, unknown> | undefined;
    vi.stubGlobal("fetch", vi.fn(async (_url: string, init?: RequestInit) => {
      sent = JSON.parse(String(init?.body ?? "{}")) as Record<string, unknown>;
      return new Response(JSON.stringify(unreadable), { status: 200 });
    }));
    await ApiAtlasProvider.importBooking({ text: "hello" });
    expect(sent?.["client_timezone"]).toBe(Intl.DateTimeFormat().resolvedOptions().timeZone);
  });
});
