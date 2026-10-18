import { describe, expect, it } from "vitest";
import { mapTrip } from "./api";

const raw = {
  route: [],
  dates: null,
  bookings: [],
  attention: [],
  days: [],
  activities: null,
  next: [],
  off_timeline: [],
  context: {
    now: {
      local: "2026-10-17T13:00",
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
      key: "plan|conference",
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
        event: "Investment Conference",
        place: "One&Only Portonovi",
        date_text: "20–21 October",
        basis: "guest",
        resolved_place: {
          status: "resolved",
          canonical_name: "One&Only Portonovi",
          locality: "Kumbor",
          municipality: "Herceg Novi",
          country: "Montenegro",
          timezone: "Europe/Podgorica",
          coordinates: {
            lat: 42.435287,
            lon: 18.59857,
          },
        },
        resolved_date: {
          status: "resolved",
          start_date: "2026-10-20",
          end_date: "2026-10-21",
          basis: "inferred_upcoming_date",
        },
        planning: {
          status: "needs_planning",
          needs: [
            {
              code: "place_role_unknown",
              category: "clarification",
              state: "needs_confirmation",
              basis: "event_and_place_stated_without_relationship",
            },
            {
              code: "accommodation_unconfirmed",
              category: "stay",
              state: "unconfirmed",
              basis: "no_confirmed_accommodation_covering_plan_dates",
            },
            {
              code: "arrival_transport_unconfirmed",
              category: "transport",
              state: "unconfirmed",
              basis: "no_confirmed_transfer_to_resolved_destination",
            },
          ],
        },
      },
    },
  ],
};

describe("Needs Planning adapter", () => {
  it("keeps planning gaps separate from Needs Attention", () => {
    const state = mapTrip(raw);

    expect(state.trip.issues).toEqual([]);
    expect(state.trip.planningNeeds?.map((n) => n.code)).toEqual([
      "place_role_unknown",
      "accommodation_unconfirmed",
      "arrival_transport_unconfirmed",
    ]);
  });

  it("preserves backend categories and states without deciding them in TypeScript", () => {
    const state = mapTrip(raw);

    expect(state.trip.planningNeeds).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          code: "place_role_unknown",
          category: "clarification",
          state: "needs_confirmation",
        }),
        expect.objectContaining({
          code: "accommodation_unconfirmed",
          category: "stay",
          state: "unconfirmed",
        }),
        expect.objectContaining({
          code: "arrival_transport_unconfirmed",
          category: "transport",
          state: "unconfirmed",
        }),
      ]),
    );
  });
});
