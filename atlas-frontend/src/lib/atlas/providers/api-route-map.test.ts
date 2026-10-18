import {
  describe,
  expect,
  it,
} from "vitest";

import { mapTrip } from "./api";

describe("Atlas route plan mapping", () => {
  it("keeps route estimate separate from transport confirmation", () => {
    const state = mapTrip({
      route: [],
      dates: null,
      bookings: [],
      attention: [],
      days: [],
      activities: null,
      next: [],
      off_timeline: [],
      items: [
        {
          key: "plan|conference",
          kind: "plan",
          status: "stated",
          revision: {
            current: 4,
            count: 4,
            changed_fields: [],
          },
          detail: {
            event: "Investment Conference",
            place: "One&Only Portonovi",
            date_text: "20–21 October",
            planning: {
              status: "needs_planning",
              needs: [
                {
                  code: "arrival_transport_unconfirmed",
                  category: "transport",
                  state: "unconfirmed",
                },
              ],
            },
            route_plan: {
              status: "estimated",
              mode: "driving",
              origin: {
                name: "Podgorica",
                locality: "Podgorica",
                coordinates: {
                  lat: 42.44123775,
                  lon: 19.26308552,
                },
              },
              destination: {
                name: "One&Only Portonovi",
                locality: "Kumbor",
                coordinates: {
                  lat: 42.435287,
                  lon: 18.59857,
                },
              },
              distance_km: 96.4,
              route_duration_minutes: 129,
              planning_buffer_minutes: 30,
              arrive_by_local: "2026-10-20T09:00",
              leave_by_local: "2026-10-20T06:21",
              timezone: "Europe/Podgorica",
              traffic_live: false,
              provider: "osrm_demo",
              provider_guarantee: false,
              policy_version: "2026-10-17.1",
            },
          },
        },
      ],
    });

    expect(state.trip.routePlan).toEqual(
      expect.objectContaining({
        distanceKm: 96.4,
        routeDurationMinutes: 129,
        planningBufferMinutes: 30,
        arriveByLocal: "2026-10-20T09:00",
        leaveByLocal: "2026-10-20T06:21",
        trafficLive: false,
        providerGuarantee: false,
      }),
    );

    expect(
      state.trip.planningNeeds?.map((x) => x.code),
    ).toEqual(["arrival_transport_unconfirmed"]);
  });
});
