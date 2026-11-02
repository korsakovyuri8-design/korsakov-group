import { fireEvent, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { TransferPage, loadTransferRoute } from "@/routes/transfer.$slug";
import { trackRevenuePageView } from "@/lib/revenue/pageViews";

const beacon = vi.fn();
const fetchMock = vi.fn();

beforeEach(() => {
  sessionStorage.clear();
  window.history.replaceState({}, "", "/");
  beacon.mockReset().mockReturnValue(true);
  fetchMock.mockReset().mockResolvedValue({ ok: true });
  vi.stubGlobal("navigator", { sendBeacon: beacon });
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

async function readPayload(): Promise<Record<string, unknown>> {
  const blob = beacon.mock.calls[0]![1] as Blob;
  const data = await new Promise<string>((resolve) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.readAsText(blob);
  });
  expect(blob.type).toBe("application/json");
  return JSON.parse(data);
}

describe("privacy-light transfer pageviews", () => {
  it("records an approved, browser-hydrated public landing without identity", async () => {
    window.history.replaceState({}, "",
      "/transfer/podgorica-kotor?utm_source=google&utm_campaign=montenegro_launch&utm_content=link_004&email=private@example.com");
    trackRevenuePageView("podgorica-kotor");
    expect(beacon).toHaveBeenCalledTimes(1);
    expect(beacon.mock.calls[0]![0]).toBe("/revenue/view");
    expect(await readPayload()).toEqual({
      route_slug: "podgorica-kotor",
      utm_source: "google",
      utm_campaign: "montenegro_launch",
      utm_content: "link_004",
    });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("does not report unknown catalog routes", () => {
    trackRevenuePageView("private-trip");
    expect(beacon).not.toHaveBeenCalled();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("falls back once to keepalive fetch when browser declines a beacon", () => {
    beacon.mockReturnValue(false);
    trackRevenuePageView("podgorica-kotor");
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock).toHaveBeenCalledWith("/revenue/view", expect.objectContaining({
      method: "POST", keepalive: true, credentials: "omit", referrerPolicy: "no-referrer",
      headers: { "Content-Type": "application/json" },
    }));
  });

  it("sends once per route mount or slug change, without user click", () => {
    const a = loadTransferRoute("podgorica-kotor");
    const b = loadTransferRoute("tivat-airport-kotor");
    const { rerender } = render(<TransferPage route={a} />);
    expect(beacon).toHaveBeenCalledTimes(1);
    rerender(<TransferPage route={a} />);
    expect(beacon).toHaveBeenCalledTimes(1);
    rerender(<TransferPage route={b} />);
    expect(beacon).toHaveBeenCalledTimes(2);
  });
});
