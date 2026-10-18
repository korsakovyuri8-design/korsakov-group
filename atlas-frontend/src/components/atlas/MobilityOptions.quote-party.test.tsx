import {
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
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

const api = vi.hoisted(() => ({
  answerPartySize: vi.fn(async () => undefined),
  compareMobility: vi.fn(async () => ({
    request: {
      origin: "Podgorica",
      destination: "Kotor",
      arriveByLocal: "2026-10-30T18:00:00+01:00",
      leaveByLocal: "2026-10-30T15:59:00+01:00",
      timezone: "Europe/Podgorica",
    },
    modes: [
      {
        mode: "private_transfer" as const,
        serviceType: "intercity_transfer",
        status: "available" as const,
        reason: "provider_candidate_found",
        providers: [
          {
            id: "sandbox",
            slug: "atlas-sandbox-transfer",
            name: "Atlas Sandbox Transfer",
            sandbox: true,
          },
        ],
        quoteCreated: false,
        bookingCreated: false,
      },
      {
        mode: "scheduled_bus" as const,
        serviceType: "bus_ticket",
        status: "unavailable" as const,
        reason: "not_offered",
        providers: [],
        quoteCreated: false,
        bookingCreated: false,
      },
    ],
  })),
  getTransferQuote: vi.fn(async () => ({
    id: "quote-1",
    code: "TEST",
    status: "offered",
    provider: "Atlas Sandbox Transfer",
    sandbox: true,
    serviceType: "intercity_transfer",
    amount: "65.00",
    currency: "EUR",
    validUntil: "2026-10-18T17:00:00Z",
    request: {
      pickup: "Podgorica",
      destination: "Kotor",
      party_size: 2,
      pickup_time: "2026-10-30T15:59:00+01:00",
    },
    bookingCreated: false,
    transactionCreated: false,
  })),
}));

vi.mock("@/lib/atlas/api", () => ({
  answerPartySize: api.answerPartySize,
  canCompareMobility: true,
  canConfirmTransfer: true,
  canGetActiveTransferQuote: false,
  canGetTransferQuote: true,
  compareMobility: api.compareMobility,
  getActiveTransferQuote: vi.fn(),
  confirmTransfer: vi.fn(),
  getTransferQuote: api.getTransferQuote,
}));

import { MobilityOptions } from "./MobilityOptions";

describe("MobilityOptions quote party size", () => {
  it("asks for travellers only after comparison and before pricing", async () => {
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
            origin: { name: "Podgorica" },
            destination: { name: "Kotor" },
            distanceKm: 72.4,
            routeDurationMinutes: 91,
            planningBufferMinutes: 30,
            arriveByLocal: "2026-10-30T18:00",
            leaveByLocal: "2026-10-30T15:59",
            timezone: "Europe/Podgorica",
            trafficLive: false,
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
      screen.queryByLabelText(
        "Number of travellers for quote",
      ),
    ).not.toBeInTheDocument();

    fireEvent.click(
      screen.getByRole("button", {
        name: "Compare transport",
      }),
    );

    const input = await screen.findByLabelText(
      "Number of travellers for quote",
    );

    expect(
      screen.getByText("Not connected"),
    ).toBeInTheDocument();

    expect(
      api.answerPartySize,
    ).not.toHaveBeenCalled();

    fireEvent.change(input, {
      target: { value: "2" },
    });

    fireEvent.click(
      screen.getByRole("button", {
        name: "Get quote",
      }),
    );

    await waitFor(() => {
      expect(api.answerPartySize).toHaveBeenCalledWith(
        expect.anything(),
        "plan|test",
        2,
      );

      expect(api.getTransferQuote).toHaveBeenCalledWith(
        "plan|test",
      );
    });

    expect(
      api.answerPartySize.mock.invocationCallOrder[0],
    ).toBeLessThan(
      api.getTransferQuote.mock.invocationCallOrder[0],
    );

    await screen.findByText("65.00 EUR");

    expect(
      screen.queryByText(
        "Confirmation will be added in the next transaction step.",
      ),
    ).not.toBeInTheDocument();
  });
});
