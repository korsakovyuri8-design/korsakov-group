import {
  afterEach,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from "vitest";

import { ApiAtlasProvider } from "./api";

beforeEach(() => {
  window.sessionStorage.setItem(
    "atlas.session",
    "session-test",
  );
});

afterEach(() => {
  window.sessionStorage.clear();
  vi.unstubAllGlobals();
});

describe("Atlas route API errors", () => {
  it("preserves the backend route reason", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            code: "origin_unresolved",
            message: "Atlas cannot resolve the origin yet.",
          }),
          {
            status: 422,
            headers: {
              "Content-Type": "application/json",
            },
          },
        ),
      ),
    );

    await expect(
      ApiAtlasProvider.planRoute!("plan|test"),
    ).rejects.toThrow(
      "Atlas cannot resolve the origin yet.",
    );
  });

  it("falls back to HTTP status when the error body is not JSON", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          "bad gateway",
          {
            status: 502,
          },
        ),
      ),
    );

    await expect(
      ApiAtlasProvider.planRoute!("plan|test"),
    ).rejects.toThrow(
      "Atlas HTTP 502",
    );
  });
});
