import { describe, expect, it } from "vitest";
import { demoStates, sampleImport } from "./demo-data";
import { headline, headlineText, nextItem } from "./summary";

const h = (k: keyof typeof demoStates) => headlineText(headline(demoStates[k]));
const item = (k: keyof typeof demoStates, id: string) => demoStates[k].trip.items.find((i) => i.id === id);

describe("Today headline follows the trip state", () => {
  it("ON_PLAN", () => expect(h("ON_PLAN")).toBe("You have 3 hours before dinner."));
  it("FLIGHT_CHANGED", () => expect(h("FLIGHT_CHANGED")).toBe("Your transfer needs attention."));
  it("MISSING_TRANSPORT", () => expect(h("MISSING_TRANSPORT")).toBe("There is no transport after you land."));
  it("NO_PLAN", () => expect(h("NO_PLAN")).toBe("Your afternoon is open."));
});

describe("FLIGHT_CHANGED", () => {
  it("flight 09:15 → 11:15, arrival 12:55 → 14:55", () => {
    const f = item("FLIGHT_CHANGED", "flt-dm1432");
    expect([f?.previousStart, f?.start, f?.previousEnd, f?.end]).toEqual(["09:15", "11:15", "12:55", "14:55"]);
  });
  it("free window on hold, item shows on hold not planned", () => {
    expect(demoStates.FLIGHT_CHANGED.trip.freeWindow?.status).toBe("on_hold");
    expect(item("FLIGHT_CHANGED", "free-pre-dinner")?.status).toBe("paused");
  });
  it("hotel and dinner stay confirmed", () => {
    expect(item("FLIGHT_CHANGED", "htl-hk20931")?.status).toBe("confirmed");
    expect(item("FLIGHT_CHANGED", "rst-r4417")?.status).toBe("confirmed");
  });
});

describe("MISSING_TRANSPORT", () => {
  it("has no free window and keeps later bookings", () => {
    expect(demoStates.MISSING_TRANSPORT.trip.freeWindow).toBeUndefined();
    expect(item("MISSING_TRANSPORT", "rst-r4417")?.status).toBe("confirmed");
  });
});

describe("Fixture fidelity", () => {
  it("only backend discovery entity names", () => {
    const allowed = ["Maritime Museum", "Old Town Cathedral", "City Walls Lookout", "Icon Gallery", "Palace Collection", "Mountain Mausoleum"];
    for (const s of Object.values(demoStates)) for (const d of s.trip.freeWindow?.suggestions ?? []) expect(allowed).toContain(d.name);
  });
  it("transfer is Demo Transfers TR551", () => {
    const t = item("ON_PLAN", "trf-tr551");
    expect([t?.provider, t?.reference, t?.start, t?.end]).toEqual(["Demo Transfers", "TR551", "15:00", "16:00"]);
  });
  it("import returns DM1432 with DMX7Q2", () => {
    expect(sampleImport).toHaveLength(1);
    expect(sampleImport[0]?.facts.find((f) => f.label === "Booking reference")?.value).toBe("DMX7Q2");
  });
  it("next booking follows the provider clock", () => {
    expect(nextItem(demoStates.ON_PLAN)?.id).toBe("rst-r4417");
    expect(nextItem(demoStates.FLIGHT_CHANGED)?.id).toBe("flt-dm1432");
  });
});
