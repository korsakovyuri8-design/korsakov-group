import { render, screen } from "@testing-library/react";
import {
  QueryClient,
  QueryClientProvider,
} from "@tanstack/react-query";
import { describe, expect, it } from "vitest";

import { RoutePlanCard } from "./RoutePlanCard";

describe("RoutePlanCard successful route", () => {
  it("renders a real Atlas road-route result without crashing", () => {
    const queryClient = new QueryClient({
      defaultOptions: {
        queries: { retry: false },
        mutations: { retry: false },
      },
    });

    render(
      <QueryClientProvider client={queryClient}>
        <RoutePlanCard
          route={{
            status: "estimated",
            mode: "driving",
            origin: {
              name: "Podgorica",
              locality: "Podgorica",
              coords: {
                lat: 42.44123775,
                lng: 19.26308552,
              },
            },
            destination: {
              name: "Kotor",
              locality: "Kotor",
              coords: {
                lat: 42.4278,
                lng: 18.7686,
              },
            },
            distanceKm: 72.4,
            routeDurationMinutes: 91,
            planningBufferMinutes: 30,
            arriveByLocal: "2026-10-30T18:00",
            leaveByLocal: "2026-10-30T15:59",
            timezone: "Europe/Podgorica",
            trafficLive: false,
            provider: "osrm_demo",
            providerGuarantee: false,
            policyVersion: "2026-10-17.1",
            basis:
              "external_route_estimate_plus_atlas_policy_buffer",
            generatedAt:
              "2026-10-18T00:01:41+00:00",
          }}
          needs={[
            {
              id: "party",
              code: "transport_party_size_unknown",
              category: "clarification",
              state: "needs_confirmation",
              planKey: "plan|test",
            },
          ]}
        />
      </QueryClientProvider>,
    );

    expect(screen.getByText("Podgorica")).toBeTruthy();
    expect(screen.getByText("Kotor")).toBeTruthy();
    expect(screen.getByText("15:59")).toBeTruthy();
    expect(screen.getByText("18:00")).toBeTruthy();
    expect(screen.getByText("72.4 km")).toBeTruthy();
  });
});
