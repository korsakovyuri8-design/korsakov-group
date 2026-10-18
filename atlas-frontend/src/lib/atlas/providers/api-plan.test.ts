import { describe, expect, it } from "vitest";
import { mapTrip } from "./api";
import { headline, headlineText, plannedLocationText } from "../summary";

const raw = {
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
      local: "2026-10-16T11:00",
      timezone: "Europe/Podgorica",
      clock: "demo",
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
      key: "plan|test",
      kind: "plan",
      provider: null,
      reference: null,
      status: "stated",
      revision: {
        current: 1,
        count: 1,
        changed_fields: [],
      },
      detail: {
        place: "One&Only Portonovi",
        date_text: "20–21 October",
        basis: "guest",
      },
    },
  ],
};

describe("stated plan presentation", () => {
  const s = mapTrip(raw);

  it("shows a stated plan as the trip, but never as a booking", () => {
    expect(s.trip.title).toBe("One&Only Portonovi");
    expect(s.trip.dates).toBe("20–21 October");
    expect(s.trip.route).toEqual(["One&Only Portonovi"]);
    expect(s.trip.bookings).toEqual([]);

    expect(s.trip.items).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          kind: "activity",
          isBooking: false,
          status: "tentative",
          city: "One&Only Portonovi",
          title: "Stated plan",
          subtitle: "20–21 October",
        }),
      ]),
    );
  });

  it("does not call the trip empty or expose internal reason codes", () => {
    expect(headlineText(headline(s))).toBe("Your trip is taking shape.");
    expect(plannedLocationText(s.context)).toBe(
      "No planned location is established yet.",
    );
    expect(plannedLocationText(s.context)).not.toContain("trip_not_started");
  });
});
