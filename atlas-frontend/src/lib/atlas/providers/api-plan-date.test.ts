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

describe("Atlas plan date clarification", () => {
  it("sends a date answer to Python Atlas", async () => {
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

    await ApiAtlasProvider.answerPlanDate?.(
      "plan|directional",
      "30-31 October",
    );

    const [url, init] = fetchMock.mock.calls[0]!;

    expect(String(url)).toBe("/demo/plan/date");
    expect(JSON.parse(String(init?.body))).toEqual({
      session: "session-test",
      plan_key: "plan|directional",
      value: "30-31 October",
    });
  });
});
