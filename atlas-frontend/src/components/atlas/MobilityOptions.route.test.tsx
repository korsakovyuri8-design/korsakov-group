import { render } from "@testing-library/react";
import {
  QueryClient,
  QueryClientProvider,
} from "@tanstack/react-query";
import { describe, expect, it } from "vitest";

import { MobilityOptions } from "./MobilityOptions";

describe("MobilityOptions after route calculation", () => {
  it("does not crash while party size is still missing", () => {
    const queryClient = new QueryClient({
      defaultOptions: {
        queries: { retry: false },
        mutations: { retry: false },
      },
    });

    const { container } = render(
      <QueryClientProvider client={queryClient}>
        <MobilityOptions
          route={{
            status: "estimated",
            mode: "driving",
            origin: {
              name: "Podgorica",
              locality: "Podgorica",
            },
            destination: {
              name: "Kotor",
              locality: "Kotor",
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

    // Mobility comparison is not ready yet, but rendering must be safe.
    expect(container.textContent).toBe("");
  });
});
