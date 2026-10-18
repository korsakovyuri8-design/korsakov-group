import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiAtlasProvider } from "./api";

afterEach(() => {
  window.sessionStorage.clear();
  vi.unstubAllGlobals();
});

describe("party size clarification", () => {
  it("sends party size to Python Atlas", async () => {
    window.sessionStorage.setItem("atlas.session", "session-test");

    const fetchMock = vi.fn(async () =>
      new Response(
        JSON.stringify({
          session: "session-test",
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
          headers: { "Content-Type": "application/json" },
        },
      ),
    );

    vi.stubGlobal("fetch", fetchMock);

    expect(ApiAtlasProvider.answerPartySize).toBeTypeOf("function");

    await ApiAtlasProvider.answerPartySize!("plan|conference", 1);

    const [url, init] = fetchMock.mock.calls[0]!;

    expect(String(url)).toBe("/demo/plan/party-size");

    expect(JSON.parse(String(init?.body))).toEqual({
      session: "session-test",
      plan_key: "plan|conference",
      value: 1,
    });
  });
});
