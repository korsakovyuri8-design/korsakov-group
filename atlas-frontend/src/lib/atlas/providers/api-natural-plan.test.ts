import { describe, expect, it } from "vitest";
import { mapTrip } from "./api";

function rawPlan(detail: Record<string, unknown>) {
  return {
    route: [],
    dates: null,
    bookings: [],
    attention: [],
    days: [],
    activities: null,
    next: [],
    off_timeline: [],
    policy_version: "test",
    context: {
      now: {
        local: "2026-10-17T21:00",
        timezone: "Europe/Podgorica",
        clock: "live",
      },
      planned_location: {
        basis: "planned",
        status: "unknown",
        reason: "trip_not_started",
      },
    },
    next_item: null,
    disruption: null,
    items: [
      {
        key: "plan|natural-test",
        kind: "plan",
        provider: null,
        reference: null,
        status: "stated",
        revision: {
          current: 1,
          count: 1,
          changed_fields: [],
        },
        detail,
      },
    ],
  };
}

describe("natural stated plans", () => {
  it("preserves duration and note for a partial weekend plan", () => {
    const state = mapTrip(
      rawPlan({
        place: "Podgorica",
        duration_text: "a weekend",
        note: "without a Kolashin",
        basis: "guest",
      }),
    );

    const [plan] = state.trip.items;

    expect(plan.city).toBe("Podgorica");
    expect(plan.subtitle).toBe("a weekend");
    expect(plan.isBooking).toBe(false);

    expect(plan.facts).toEqual(
      expect.arrayContaining([
        { label: "Place", value: "Podgorica" },
        { label: "Duration", value: "a weekend" },
        { label: "Note", value: "without a Kolashin" },
        { label: "Status", value: "Stated" },
      ]),
    );
  });

  it("preserves a real date without contaminating the place", () => {
    const state = mapTrip(
      rawPlan({
        place: "Podgorica",
        date_text: "30–31 October",
        basis: "guest",
      }),
    );

    const [plan] = state.trip.items;

    expect(plan.city).toBe("Podgorica");
    expect(plan.subtitle).toBe("30–31 October");
    expect(plan.city).not.toContain("for");
  });
});
