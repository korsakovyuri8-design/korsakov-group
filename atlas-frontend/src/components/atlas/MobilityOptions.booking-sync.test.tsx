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
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from "vitest";
import type {
  ConfirmedTransport,
  PlanningNeed,
  RoutePlan,
} from "@/lib/atlas/domain";

const api = vi.hoisted(() => ({
  restore: vi.fn(async () => ({
    id: "quote-A",
    code: "QA",
    status: "offered",
    provider: "Atlas Sandbox Transfer",
    sandbox: true,
    serviceType: "intercity_transfer",
    amount: "65.00",
    currency: "EUR",
    validUntil: "2026-10-30T12:00:00Z",
    request: { party_size: 2 },
    bookingCreated: false,
    transactionCreated: false,
  })),
  confirm: vi.fn(async (
    _qc: unknown,
    _plan: string,
    _quote: string,
  ) => ({
    id: "transaction-A",
    status: "accepted",
    provider: "Atlas Sandbox Transfer",
    providerReference: "REF-A",
    sandbox: true,
    amount: "65.00",
    currency: "EUR",
  })),
  preview: vi.fn(async () => ({
    transactionId: "transaction-A",
    transactionStatus: "accepted",
    cancellable: true,
    policy: { status: "free" },
    provider: "Atlas Sandbox Transfer",
    sandbox: true,
  })),
  cancel: vi.fn(async () => ({
    pending: false,
    transaction: { status: "cancelled" },
  })),
}));

vi.mock("@/lib/atlas/api", () => ({
  answerPartySize: vi.fn(),
  canCompareMobility: true,
  canConfirmTransfer: true,
  canGetActiveTransferQuote: true,
  canGetTransferQuote: true,
  canReviewTransferCancellation: true,
  canCancelTransfer: true,
  compareMobility: vi.fn(),
  getActiveTransferQuote: api.restore,
  confirmTransfer: api.confirm,
  getTransferQuote: vi.fn(),
  getTransferCancellation: api.preview,
  cancelTransfer: api.cancel,
}));

// This test concerns the parent card, not the Change panel.
vi.mock("./TransferChangePanel", () => ({
  TransferChangePanel: () => null,
}));

import { MobilityOptions } from "./MobilityOptions";

const route: RoutePlan = {
  status: "estimated",
  mode: "driving",
  origin: { name: "Podgorica" },
  destination: { name: "Kotor" },
  distanceKm: 72,
  routeDurationMinutes: 90,
  planningBufferMinutes: 30,
  arriveByLocal: "2026-10-30T18:00",
  leaveByLocal: "2026-10-30T16:00",
  timezone: "Europe/Podgorica",
  trafficLive: false,
  providerGuarantee: false,
};

const bookingA: ConfirmedTransport = {
  status: "accepted",
  provider: "Atlas Sandbox Transfer",
  providerReference: "REF-A",
  partySize: 2,
  amount: "65.00",
  currency: "EUR",
  sandbox: true,
};

const bookingB: ConfirmedTransport = {
  ...bookingA,
  providerReference: "REF-B",
  partySize: 3,
  amount: "70.00",
};

const transportNeed: PlanningNeed = {
  id: "need-transport",
  code: "arrival_transport_unconfirmed",
  category: "transport",
  state: "unconfirmed",
  planKey: "plan-A",
};

function mount(
  initial?: ConfirmedTransport,
  initialNeeds?: PlanningNeed[],
) {
  const qc = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });

  const view = (
    confirmedTransport?: ConfirmedTransport,
    needs?: PlanningNeed[],
  ) => (
    <QueryClientProvider client={qc}>
      <MobilityOptions
        planKey="plan-A"
        route={route}
        confirmedTransport={confirmedTransport}
        needs={needs}
      />
    </QueryClientProvider>
  );

  const rendered = render(view(initial, initialNeeds));

  return {
    show: (
      confirmedTransport?: ConfirmedTransport,
      needs?: PlanningNeed[],
    ) => rendered.rerender(view(confirmedTransport, needs)),
  };
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("Atlas authoritative booking state", () => {
  it("shows B rather than locally cached A after replacement", async () => {
    const app = mount(undefined, [transportNeed]);

    fireEvent.click(await screen.findByRole("button", {
      name: "Confirm 65.00 EUR",
    }));

    await waitFor(() => {
      expect(api.confirm).toHaveBeenCalledTimes(1);
    });

    app.show(bookingA);

    expect(await screen.findByText("REF-A"))
      .toBeInTheDocument();

    app.show(bookingB);

    expect(await screen.findByText("REF-B"))
      .toBeInTheDocument();

    expect(screen.queryByText("REF-A"))
      .not.toBeInTheDocument();
  });

  it("shows a new booking after the previous one was cancelled", async () => {
    const app = mount(bookingA);

    fireEvent.click(screen.getByRole("button", {
      name: "Review cancellation terms",
    }));

    await screen.findByText("Cancellation terms");

    fireEvent.click(screen.getByRole("checkbox", {
      name: "I consent to cancelling this transfer",
    }));

    fireEvent.click(screen.getByRole("button", {
      name: "Confirm cancellation",
    }));

    expect(await screen.findByText(
      /Transfer cancellation confirmed by the provider/,
    )).toBeInTheDocument();

    // Server reports no current booking after the cancellation.
    app.show(undefined, [transportNeed]);

    // Subsequently, the server confirms another booking.
    app.show(bookingB);

    expect(await screen.findByText("REF-B"))
      .toBeInTheDocument();

    expect(screen.getByRole("button", {
      name: "Review cancellation terms",
    })).toBeInTheDocument();

    expect(screen.queryByText(
      /Transfer cancellation confirmed by the provider/,
    )).not.toBeInTheDocument();
  });
});
