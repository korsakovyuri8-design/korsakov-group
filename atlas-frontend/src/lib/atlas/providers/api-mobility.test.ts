import {
  afterEach,
  describe,
  expect,
  it,
  vi,
} from "vitest";

import { ApiAtlasProvider } from "./api";

afterEach(() => {
  window.sessionStorage.clear();
  vi.unstubAllGlobals();
});

describe("Atlas mobility API", () => {
  it("compares transport without requesting a quote", async () => {
    window.sessionStorage.setItem(
      "atlas.session",
      "session-test",
    );

    const fetchMock = vi.fn(async () =>
      new Response(
        JSON.stringify({
          session: "session-test",
          mobility: {
            request: {
              origin: "Podgorica",
              destination: "One&Only Portonovi",
              party_size: null,
              arrive_by_local:
                "2026-10-20T09:00:00+02:00",
              leave_by_local:
                "2026-10-20T06:21:00+02:00",
              timezone: "Europe/Podgorica",
            },
            modes: [
              {
                mode: "private_transfer",
                service_type:
                  "intercity_transfer",
                status: "available",
                reason:
                  "provider_candidate_found",
                providers: [
                  {
                    id: "p1",
                    slug:
                      "atlas-sandbox-transfer",
                    name:
                      "Atlas Sandbox Transfer",
                    sandbox: true,
                  },
                ],
                quote_created: false,
                booking_created: false,
              },
            ],
          },
        }),
        {
          status: 200,
          headers: {
            "Content-Type":
              "application/json",
          },
        },
      ),
    );

    vi.stubGlobal("fetch", fetchMock);

    const result =
      await ApiAtlasProvider.compareMobility!(
        "plan|conference",
      );

    expect(result.modes[0]?.status).toBe(
      "available",
    );

    expect(result.request.partySize).toBeUndefined();

    expect(
      result.modes[0]?.quoteCreated,
    ).toBe(false);

    expect(
      String(fetchMock.mock.calls[0]?.[0]),
    ).toBe("/demo/plan/mobility");
  });

  it("requests a quote without creating a booking", async () => {
    window.sessionStorage.setItem(
      "atlas.session",
      "session-test",
    );

    const fetchMock = vi.fn(async () =>
      new Response(
        JSON.stringify({
          session: "session-test",
          quote: {
            id: "q1",
            code: "ABCD",
            status: "offered",
            provider:
              "Atlas Sandbox Transfer",
            sandbox: true,
            service_type:
              "intercity_transfer",
            amount: "60.00",
            currency: "EUR",
            description:
              "Sandbox private transfer",
            conditions:
              "Synthetic development offer.",
            valid_until:
              "2026-10-17T15:15:00+00:00",
            request: {
              pickup: "Podgorica",
              destination:
                "One&Only Portonovi",
              party_size: 1,
              pickup_time:
                "2026-10-20T06:21:00+02:00",
            },
            booking_created: false,
            transaction_created: false,
          },
        }),
        {
          status: 200,
          headers: {
            "Content-Type":
              "application/json",
          },
        },
      ),
    );

    vi.stubGlobal("fetch", fetchMock);

    const quote =
      await ApiAtlasProvider.getTransferQuote!(
        "plan|conference",
      );

    expect(quote.amount).toBe("60.00");
    expect(
      quote.transactionCreated,
    ).toBe(false);
    expect(quote.bookingCreated).toBe(false);

    expect(
      String(fetchMock.mock.calls[0]?.[0]),
    ).toBe(
      "/demo/plan/transfer-quote",
    );
  });
});

describe("Atlas mobility confirmation", () => {
  it("sends explicit consent and returns provider transaction state", async () => {
    window.sessionStorage.setItem(
      "atlas.session",
      "session-test",
    );

    const fetchMock = vi.fn(async () =>
      new Response(
        JSON.stringify({
          session: "session-test",
          transaction: {
            id: "txn-1",
            action_id: "action-1",
            quote_id: "q1",
            status: "accepted",
            provider:
              "Atlas Sandbox Transfer",
            provider_reference:
              "MOCK-12345678",
            service_type:
              "intercity_transfer",
            amount: "60.00",
            currency: "EUR",
            sandbox: true,
            transaction_created: true,
            provider_accepted: true,
            real_world: false,
          },
          trip: {
            route: [],
            dates: null,
            bookings: [],
            attention: [],
            days: [],
            activities: null,
            next: [],
            off_timeline: [],
            items: [],
          },
        }),
        {
          status: 200,
          headers: {
            "Content-Type":
              "application/json",
          },
        },
      ),
    );

    vi.stubGlobal("fetch", fetchMock);

    const result =
      await ApiAtlasProvider.confirmTransfer!(
        "plan|conference",
        "q1",
      );

    expect(result.transaction.status).toBe(
      "accepted",
    );

    expect(
      result.transaction.providerAccepted,
    ).toBe(true);

    expect(
      result.transaction.realWorld,
    ).toBe(false);

    const [url, init] =
      fetchMock.mock.calls[0]!;

    expect(String(url)).toBe(
      "/demo/plan/transfer-confirm",
    );

    expect(
      JSON.parse(String(init?.body)),
    ).toEqual({
      session: "session-test",
      plan_key: "plan|conference",
      quote_id: "q1",
      consent: true,
    });
  });
});
