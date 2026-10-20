import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiAtlasProvider } from "./api";

afterEach(() => {
  window.sessionStorage.clear();
  vi.unstubAllGlobals();
});

const trip = {
  route: [],
  bookings: [],
  attention: [],
  days: [],
  next: [],
  items: [{
    key: "plan-change",
    kind: "plan",
    status: "stated",
    detail: {
      confirmed_transport: {
        status: "accepted",
        provider: "Atlas Sandbox Transfer",
        sandbox: true,
        party_size: 3,
      },
      transport_change: {
        status: "needs_attention",
        reason: "old_cancellation_not_confirmed",
        old_transaction_status: "accepted",
        new_transaction_status: "accepted",
        old_provider_reference: "OLD-REF",
      },
      planning: { needs: [] },
    },
  }],
};

describe("Atlas change transaction truth", () => {
  it("preserves both booking states after HTTP 202", async () => {
    window.sessionStorage.setItem("atlas.session", "test-session");

    const fetchMock = vi.fn(
      async (_url: RequestInfo | URL, _init?: RequestInit) =>
        new Response(JSON.stringify({
          session: "test-session",
          transaction: {
            id: "new-transaction",
            status: "accepted",
            sandbox: true,
            real_world: false,
          },
          change: {
            status: "needs_attention",
            reason: "old_cancellation_not_confirmed",
          },
          trip,
        }), { status: 202 }),
    );

    vi.stubGlobal("fetch", fetchMock);

    const result = await ApiAtlasProvider.confirmTransfer!(
      "plan-change",
      "replacement-quote",
    );

    expect(result.transaction.status).toBe("accepted");
    expect(result.state.trip.transportChange?.status)
      .toBe("needs_attention");
    expect(result.state.trip.transportChange?.oldTransactionStatus)
      .toBe("accepted");
    expect(result.state.trip.transportChange?.newTransactionStatus)
      .toBe("accepted");

    const [url, init] = fetchMock.mock.calls[0]!;

    expect(String(url)).toBe("/demo/plan/transfer-confirm");
    expect(JSON.parse(String(init?.body))).toMatchObject({
      plan_key: "plan-change",
      quote_id: "replacement-quote",
      consent: true,
    });
  });

  it("restores the unresolved change from the trip API", async () => {
    window.sessionStorage.setItem("atlas.session", "test-session");

    vi.stubGlobal("fetch", vi.fn(
      async (_url: RequestInfo | URL, _init?: RequestInit) =>
        new Response(JSON.stringify({ trip }), { status: 200 }),
    ));

    const state = await ApiAtlasProvider.getState();

    expect(state.trip.transportPlanKey).toBe("plan-change");
    expect(state.trip.transportChange?.status)
      .toBe("needs_attention");
    expect(state.trip.transportChange?.oldProviderReference)
      .toBe("OLD-REF");
  });

  it("honours fail-closed response when trip change is absent", async () => {
    window.sessionStorage.setItem("atlas.session", "test-session");

    vi.stubGlobal("fetch", vi.fn(
      async (_url: RequestInfo | URL, _init?: RequestInit) =>
        new Response(JSON.stringify({
          session: "test-session",
          transaction: { id: "new", status: "accepted" },
          change: {
            status: "needs_attention",
            reason: "change_state_unavailable",
          },
          trip: {
            ...trip,
            items: [{
              ...trip.items[0],
              detail: {
                confirmed_transport:
                  trip.items[0]!.detail.confirmed_transport,
                planning: { needs: [] },
              },
            }],
          },
        }), { status: 202 }),
    ));

    const result = await ApiAtlasProvider.confirmTransfer!(
      "plan-change",
      "replacement-quote",
    );

    expect(result.state.trip.transportChange?.status)
      .toBe("needs_attention");
    expect(result.state.trip.transportChange?.reason)
      .toBe("change_state_unavailable");
  });
});
