import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiAtlasProvider } from "./api";

afterEach(() => {
  window.sessionStorage.clear();
  vi.unstubAllGlobals();
});

describe("Atlas cancellation API", () => {
  it("reads cancellation terms without consent", async () => {
    window.sessionStorage.setItem("atlas.session", "test-session");

    const fetchMock = vi.fn(async (_url: RequestInfo | URL, _init?: RequestInit) =>
      new Response(JSON.stringify({
        session: "test-session",
        cancellation: {
          transaction_id: "txn-123",
          transaction_status: "accepted",
          cancellable: true,
          policy: { status: "unknown", fee: null, deadline: null },
          provider: "Atlas Sandbox Transfer",
          sandbox: true,
        },
      }), { status: 200 }),
    );

    vi.stubGlobal("fetch", fetchMock);

    const terms = await ApiAtlasProvider.getTransferCancellation!(
      "plan-123",
    );

    expect(terms?.transactionId).toBe("txn-123");
    expect(terms?.policy.status).toBe("unknown");

    const [url, init] = fetchMock.mock.calls[0]!;
    expect(url).toBe("/demo/plan/transfer-cancellation");
    expect(JSON.parse(String(init?.body))).toEqual({
      session: "test-session",
      plan_key: "plan-123",
    });
  });

  it("binds explicit consent to the transaction and preserves pending", async () => {
    window.sessionStorage.setItem("atlas.session", "test-session");

    const fetchMock = vi.fn(async (_url: RequestInfo | URL, _init?: RequestInit) =>
      new Response(JSON.stringify({
        session: "test-session",
        reason: "cancellation_pending",
        transaction: {
          id: "txn-123",
          status: "accepted",
          provider: "Test Provider",
          real_world: false,
        },
        trip: {
          route: [], items: [], bookings: [],
          attention: [], days: [], next: [],
        },
      }), { status: 202 }),
    );

    vi.stubGlobal("fetch", fetchMock);

    const result = await ApiAtlasProvider.cancelTransfer!(
      "plan-123",
      "txn-123",
    );

    expect(result.pending).toBe(true);
    expect(result.transaction.status).toBe("accepted");

    const [url, init] = fetchMock.mock.calls[0]!;
    expect(url).toBe("/demo/plan/transfer-cancel");
    expect(JSON.parse(String(init?.body))).toEqual({
      session: "test-session",
      plan_key: "plan-123",
      transaction_id: "txn-123",
      consent: true,
    });
  });
});
