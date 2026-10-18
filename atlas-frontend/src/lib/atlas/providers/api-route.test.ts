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

describe("Atlas route planning action", () => {
  it("asks Python Atlas to calculate a route", async () => {
    window.sessionStorage.setItem(
      "atlas.session",
      "session-test",
    );

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
          headers: {
            "Content-Type": "application/json",
          },
        },
      ),
    );

    vi.stubGlobal("fetch", fetchMock);

    expect(ApiAtlasProvider.planRoute).toBeTypeOf("function");

    await ApiAtlasProvider.planRoute!("plan|conference");

    const [url, init] = fetchMock.mock.calls[0]!;

    expect(String(url)).toBe("/demo/plan/route");

    expect(JSON.parse(String(init?.body))).toEqual({
      session: "session-test",
      plan_key: "plan|conference",
    });
  });
});
