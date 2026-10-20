import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  preview: vi.fn(async () => ({
    transactionId: "transaction-A",
    transactionStatus: "accepted",
    cancellable: true,
    policy: { status: "free" },
    provider: "Atlas Sandbox Transfer",
    sandbox: true,
  })),
  cancel: vi.fn(async (
    _qc: unknown,
    _planKey: string,
    _transactionId: string,
  ) => ({
    pending: true,
    transaction: { status: "accepted" },
  })),
}));

vi.mock("@/lib/atlas/api", () => ({
  answerPartySize: vi.fn(),
  canCompareMobility: true,
  canConfirmTransfer: true,
  canGetActiveTransferQuote: false,
  canGetTransferQuote: true,
  canReviewTransferCancellation: true,
  getTransferCancellation: api.preview,
  canCancelTransfer: true,
  cancelTransfer: api.cancel,
  compareMobility: vi.fn(),
  getActiveTransferQuote: vi.fn(),
  confirmTransfer: vi.fn(),
  getTransferQuote: vi.fn(),
}));

import { MobilityOptions } from "./MobilityOptions";

function renderConfirmed() {
  const qc = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });

  return render(
    <QueryClientProvider client={qc}>
      <MobilityOptions
        planKey="plan-A"
        route={{
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
        }}
        confirmedTransport={{
          status: "accepted",
          provider: "Atlas Sandbox Transfer",
          providerReference: "MOCK-123",
          partySize: 2,
          amount: "65.00",
          currency: "EUR",
          sandbox: true,
        }}
      />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("Atlas cancellation UI", () => {
  it("never submits cancellation during review or without consent", async () => {
    renderConfirmed();

    fireEvent.click(screen.getByRole("button", {
      name: "Review cancellation terms",
    }));

    expect(await screen.findByText("Cancellation terms"))
      .toBeInTheDocument();

    expect(api.preview).toHaveBeenCalledWith("plan-A");
    expect(api.cancel).not.toHaveBeenCalled();

    const button = screen.getByRole("button", {
      name: "Confirm cancellation",
    });

    expect(button).toBeDisabled();

    fireEvent.click(screen.getByRole("checkbox", {
      name: "I consent to cancelling this transfer",
    }));

    expect(button).toBeEnabled();

    fireEvent.click(button);

    await waitFor(() => {
      expect(api.cancel).toHaveBeenCalledWith(
        expect.anything(),
        "plan-A",
        "transaction-A",
      );
    });

    expect(await screen.findByText(
      /awaiting provider confirmation/i,
    )).toBeInTheDocument();

    expect(screen.getByText("Transfer confirmed"))
      .toBeInTheDocument();
  });

  it("never claims cancellation after provider refusal", async () => {
    api.cancel.mockRejectedValueOnce(
      new Error("provider_cancellation_not_confirmed"),
    );

    renderConfirmed();

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

    expect(await screen.findByRole("alert"))
      .toHaveTextContent("Cancellation was not confirmed");

    expect(screen.getByText("Transfer confirmed"))
      .toBeInTheDocument();

    expect(screen.queryByText(
      /Transfer cancellation confirmed by the provider/,
    )).not.toBeInTheDocument();
  });

  it("shows cancellation success only after provider confirmation", async () => {
    api.cancel.mockResolvedValueOnce({
      pending: false,
      transaction: { status: "cancelled" },
    });

    renderConfirmed();

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

    expect(screen.queryByText("Transfer confirmed"))
      .not.toBeInTheDocument();
  });
});
