import { render, screen } from "@testing-library/react";
import {
  QueryClient,
  QueryClientProvider,
} from "@tanstack/react-query";
import {
  describe,
  expect,
  it,
  vi,
} from "vitest";

vi.mock("@/lib/atlas/api", () => ({
  answerPartySize: vi.fn(),
  canCompareMobility: true,
  canConfirmTransfer: true,
  canGetActiveTransferQuote: false,
  canGetTransferQuote: true,
  compareMobility: vi.fn(),
  getActiveTransferQuote: vi.fn(),
  confirmTransfer: vi.fn(),
  getTransferQuote: vi.fn(),
}));

import { MobilityOptions } from "./MobilityOptions";

describe("MobilityOptions after route calculation", () => {
  it("allows transport comparison while party size is still missing", () => {
    const queryClient = new QueryClient({
      defaultOptions: {
        queries: { retry: false },
        mutations: { retry: false },
      },
    });

    render(
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
              id: "transport",
              code: "arrival_transport_unconfirmed",
              category: "transport",
              state: "unconfirmed",
              planKey: "plan|test",
            },
          ]}
        />
      </QueryClientProvider>,
    );

    expect(
      screen.getByText("Compare ways to get there."),
    ).toBeInTheDocument();

    expect(
      screen.getByRole("button", {
        name: "Compare transport",
      }),
    ).toBeInTheDocument();

    expect(
      screen.queryByLabelText(
        "Number of travellers for quote",
      ),
    ).not.toBeInTheDocument();
  });
});
