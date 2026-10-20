import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiAtlasProvider } from "./api";

const EMPTY_TRIP = {
  route: [],
  dates: null,
  bookings: [],
  attention: [],
  days: [],
  activities: null,
  next: [],
  off_timeline: [],
  items: [],
};

afterEach(() => {
  window.sessionStorage.clear();
  vi.unstubAllGlobals();
});

function mockFetch() {
  const fn = vi.fn(async (_url: RequestInfo | URL, _init?: RequestInit) =>
    new Response(
      JSON.stringify({
        session: "session-test",
        changed: true,
        trip: EMPTY_TRIP,
      }),
      {
        status: 200,
        headers: { "Content-Type": "application/json" },
      },
    ),
  );

  vi.stubGlobal("fetch", fn);
  return fn;
}

describe("Atlas plan clarification actions", () => {
  it("sends the place-role answer to Python Atlas", async () => {
    window.sessionStorage.setItem("atlas.session", "session-test");
    const fetchMock = mockFetch();

    await ApiAtlasProvider.answerPlaceRole?.(
      "plan|conference",
      "venue",
    );

    const [url, init] = fetchMock.mock.calls[0]!;

    expect(String(url)).toBe("/demo/plan/role");

    expect(JSON.parse(String(init?.body))).toEqual({
      session: "session-test",
      plan_key: "plan|conference",
      role: "venue",
    });
  });

  it("sends stay intent to Python Atlas", async () => {
    window.sessionStorage.setItem("atlas.session", "session-test");
    const fetchMock = mockFetch();

    await ApiAtlasProvider.answerStayIntent?.(
      "plan|conference",
      false,
    );

    const [url, init] = fetchMock.mock.calls[0]!;

    expect(String(url)).toBe("/demo/plan/stay-intent");

    expect(JSON.parse(String(init?.body))).toEqual({
      session: "session-test",
      plan_key: "plan|conference",
      value: false,
    });
  });

  it("sends stated origin to Python Atlas", async () => {
    window.sessionStorage.setItem("atlas.session", "session-test");
    const fetchMock = mockFetch();

    await ApiAtlasProvider.answerOrigin?.(
      "plan|conference",
      "Podgorica",
    );

    const [url, init] = fetchMock.mock.calls[0]!;

    expect(String(url)).toBe("/demo/plan/origin");

    expect(JSON.parse(String(init?.body))).toEqual({
      session: "session-test",
      plan_key: "plan|conference",
      origin: "Podgorica",
    });
  });
});
