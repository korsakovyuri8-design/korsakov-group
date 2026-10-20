import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ConfirmedTransport, TransportChange } from "@/lib/atlas/domain";

const api = vi.hoisted(() => ({
  answerPartySize: vi.fn(async (_qc: unknown, _key: string, _party: number) => ({})),
  getTransferQuote: vi.fn(async (_key: string) => ({
    id: "quote-B",
    code: "BTEST",
    status: "offered",
    provider: "Atlas Sandbox Transfer",
    sandbox: true,
    serviceType: "intercity_transfer",
    amount: "70.00",
    currency: "EUR",
    validUntil: "2026-10-30T12:00:00Z",
    conditions: "Synthetic offer. No real vehicle.",
    request: { party_size: 3 },
    bookingCreated: false,
    transactionCreated: false,
  })),
  getActiveTransferQuote: vi.fn(async (_key: string) => undefined as undefined | {
    id: string;
    code: string;
    status: string;
    provider: string;
    sandbox: boolean;
    serviceType: string;
    amount: string;
    currency: string;
    validUntil: string;
    request: Record<string, unknown>;
    bookingCreated: boolean;
    transactionCreated: boolean;
  }),
  confirmTransfer: vi.fn(async (_qc: unknown, _key: string, _quote: string) => ({
    id: "transaction-B",
    status: "accepted",
    providerReference: "REF-B",
  })),
}));

vi.mock("@/lib/atlas/api", () => ({
  answerPartySize: api.answerPartySize,
  canConfirmTransfer: true,
  canGetActiveTransferQuote: true,
  canGetTransferQuote: true,
  confirmTransfer: api.confirmTransfer,
  getActiveTransferQuote: api.getActiveTransferQuote,
  getTransferQuote: api.getTransferQuote,
}));

import { TransferChangePanel } from "./TransferChangePanel";

const oldTransfer: ConfirmedTransport = {
  status: "accepted",
  provider: "Atlas Sandbox Transfer",
  providerReference: "REF-A",
  partySize: 2,
  amount: "65.00",
  currency: "EUR",
  sandbox: true,
};

function renderChange({
  needsGap = false,
  change,
}: {
  needsGap?: boolean;
  change?: TransportChange;
} = {}) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });

  return render(
    <QueryClientProvider client={qc}>
      <TransferChangePanel
        planKey="plan-A"
        confirmed={oldTransfer}
        needsGap={needsGap}
        change={change}
      />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("Atlas transfer Change UX", () => {
  it("requires a new quote and explicit consent before replacing", async () => {
    renderChange();

    fireEvent.click(screen.getByRole("button", { name: "Change transfer" }));

    const input = screen.getByRole("textbox", { name: "Travellers for replacement" });
    const quoteButton = screen.getByRole("button", { name: "Get replacement quote" });

    expect(quoteButton).toBeDisabled();

    fireEvent.change(input, { target: { value: "3" } });
    fireEvent.click(quoteButton);

    expect(await screen.findByText("70.00 EUR")).toBeInTheDocument();
    expect(api.answerPartySize).toHaveBeenCalledWith(expect.anything(), "plan-A", 3);
    expect(api.getTransferQuote).toHaveBeenCalledWith("plan-A");
    expect(api.answerPartySize.mock.invocationCallOrder[0]!)
      .toBeLessThan(api.getTransferQuote.mock.invocationCallOrder[0]!);
    expect(api.confirmTransfer).not.toHaveBeenCalled();

    const confirmButton = screen.getByRole("button", { name: "Confirm replacement" });
    expect(confirmButton).toBeDisabled();

    fireEvent.click(screen.getByRole("checkbox", {
      name: "I consent to replacing this transfer",
    }));
    fireEvent.click(confirmButton);

    await waitFor(() => {
      expect(api.confirmTransfer).toHaveBeenCalledWith(
        expect.anything(), "plan-A", "quote-B",
      );
    });

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "previous cancellation has not yet been verified",
    );
    expect(screen.queryByText(/Replacement completed/)).not.toBeInTheDocument();
  });

  it("restores an offered replacement after refresh without confirming it", async () => {
    api.getActiveTransferQuote.mockResolvedValueOnce({
      id: "quote-B",
      code: "BTEST",
      status: "offered",
      provider: "Atlas Sandbox Transfer",
      sandbox: true,
      serviceType: "intercity_transfer",
      amount: "70.00",
      currency: "EUR",
      validUntil: "2026-10-30T12:00:00Z",
      request: { party_size: 3 },
      bookingCreated: false,
      transactionCreated: false,
    });

    renderChange({ needsGap: true });

    expect(await screen.findByText("70.00 EUR")).toBeInTheDocument();
    expect(api.getActiveTransferQuote).toHaveBeenCalledWith("plan-A");
    expect(api.answerPartySize).not.toHaveBeenCalled();
    expect(api.confirmTransfer).not.toHaveBeenCalled();
  });

  it("does not allow another change while old cancellation is unresolved", () => {
    renderChange({
      change: {
        status: "needs_attention",
        oldTransactionStatus: "accepted",
        newTransactionStatus: "accepted",
      },
    });

    expect(screen.queryByRole("button", { name: "Change transfer" }))
      .not.toBeInTheDocument();
  });

  it("blocks repeated confirmation when the outcome is unknown", async () => {
    api.confirmTransfer.mockRejectedValueOnce(new Error("network interrupted"));
    renderChange();

    fireEvent.click(screen.getByRole("button", { name: "Change transfer" }));
    fireEvent.change(screen.getByRole("textbox", {
      name: "Travellers for replacement",
    }), { target: { value: "3" } });
    fireEvent.click(screen.getByRole("button", { name: "Get replacement quote" }));
    await screen.findByText("70.00 EUR");

    fireEvent.click(screen.getByRole("checkbox", {
      name: "I consent to replacing this transfer",
    }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm replacement" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Replacement outcome could not be verified",
    );
    expect(screen.queryByRole("button", { name: "Confirm replacement" }))
      .not.toBeInTheDocument();
    expect(api.confirmTransfer).toHaveBeenCalledTimes(1);
  });
});
