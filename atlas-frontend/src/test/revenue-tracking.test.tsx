import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { RevenueOutboundLink } from "@/components/atlas/RevenueOutboundLink";
import { RevenuePanel } from "@/components/atlas/RevenuePanel";
import { TransportAffiliateCta } from "@/components/atlas/TransportAffiliateCta";
import { TransferPage, loadTransferRoute } from "@/routes/transfer.$slug";
import { captureRevenueAttribution, sanitizeAttribution } from "@/lib/revenue/attribution";

const href = "https://kiwitaxi.com/en?pap=test123";
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
  vi.unstubAllEnvs();
  vi.restoreAllMocks();
});

function renderLink() {
  render(
    <RevenueOutboundLink
      href={href}
      provider="kiwitaxi"
      placement="route_landing"
      routeSlug="podgorica-kotor"
      affiliate
    >
      Continue
    </RevenueOutboundLink>,
  );
  return screen.getByRole("link", { name: "Continue" });
}
async function beaconBody() {
  const blob = beacon.mock.calls[0]![1] as Blob;
  const text = await new Promise<string>((resolve) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.readAsText(blob);
  });
  expect(blob.type).toBe("application/json");
  return JSON.parse(text);
}

describe("outbound activation", () => {
  it("retains a real safe link and sends one JSON beacon without cancelling navigation", async () => {
    window.history.replaceState(
      {},
      "",
      "/transfer/podgorica-kotor?utm_source=instagram&utm_campaign=montenegro_launch&email=private@example.com",
    );
    const link = renderLink();
    expect(link).toHaveAttribute("href", href);
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer sponsored");
    expect(beacon).not.toHaveBeenCalled();
    expect(fireEvent.click(link)).toBe(true);
    expect(beacon).toHaveBeenCalledTimes(1);
    expect(beacon.mock.calls[0]![0]).toBe("/revenue/click");
    expect(await beaconBody()).toEqual({
      provider: "kiwitaxi",
      placement: "route_landing",
      route_slug: "podgorica-kotor",
      utm_source: "instagram",
      utm_campaign: "montenegro_launch",
    });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it.each(["false", "throws", "missing"])("uses one keepalive fallback when beacon %s", (mode) => {
    if (mode === "false") beacon.mockReturnValue(false);
    if (mode === "throws")
      beacon.mockImplementation(() => {
        throw Error("queue unavailable");
      });
    if (mode === "missing") vi.stubGlobal("navigator", {});
    expect(fireEvent.click(renderLink())).toBe(true);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock).toHaveBeenCalledWith(
      "/revenue/click",
      expect.objectContaining({
        method: "POST",
        keepalive: true,
        credentials: "omit",
        referrerPolicy: "no-referrer",
        headers: { "Content-Type": "application/json" },
      }),
    );
  });

  it.each(["reject", "throw", "http-error"])(
    "keeps navigation available when fetch fails: %s",
    async (mode) => {
      beacon.mockReturnValue(false);
      if (mode === "reject") fetchMock.mockRejectedValue(Error("offline"));
      if (mode === "throw")
        fetchMock.mockImplementation(() => {
          throw Error("disabled");
        });
      if (mode === "http-error") fetchMock.mockResolvedValue({ ok: false, status: 503 });
      const link = renderLink();
      expect(fireEvent.click(link)).toBe(true);
      expect(link).toHaveAttribute("href", href);
      await Promise.resolve();
      expect(fetchMock).toHaveBeenCalledTimes(1);
    },
  );

  it("counts middle-click and keyboard-generated click, not hover, focus or context menus", () => {
    const link = renderLink();
    fireEvent.mouseOver(link);
    fireEvent.focus(link);
    fireEvent.contextMenu(link);
    fireEvent(link, new MouseEvent("auxclick", { bubbles: true, button: 2 }));
    expect(beacon).not.toHaveBeenCalled();
    fireEvent(link, new MouseEvent("auxclick", { bubbles: true, button: 1 }));
    expect(beacon).toHaveBeenCalledTimes(1);
    fireEvent.click(link, { detail: 0, button: 0 }); // browser's keyboard activation
    expect(beacon).toHaveBeenCalledTimes(2);
  });

  it("does not add sponsored to concierge or render unsafe URLs", () => {
    const { rerender } = render(
      <RevenueOutboundLink
        href="https://example.com/contact"
        provider="concierge"
        placement="revenue_panel"
        affiliate={false}
      >
        Contact
      </RevenueOutboundLink>,
    );
    expect(screen.getByRole("link")).toHaveAttribute("rel", "noopener noreferrer");
    for (const url of [
      "http://example.com",
      "javascript:alert(1)",
      "https://user:password@example.com",
    ]) {
      rerender(
        <RevenueOutboundLink href={url} provider="kiwitaxi" placement="revenue_panel" affiliate>
          Contact
        </RevenueOutboundLink>,
      );
      expect(screen.queryByRole("link")).toBeNull();
    }
  });

  it.each(["missing_transport", "route_landing", "revenue_panel"] as const)(
    "wires the %s placement",
    async (placement) => {
      vi.stubEnv("VITE_ATLAS_AFFILIATE_KIWITAXI_URL", href);
      if (placement === "missing_transport") render(<TransportAffiliateCta />);
      if (placement === "revenue_panel") render(<RevenuePanel />);
      if (placement === "route_landing")
        render(<TransferPage route={loadTransferRoute("tivat-airport-kotor")} />);
      fireEvent.click(screen.getByRole("link", { name: /check.*transfer prices/i }));
      expect(await beaconBody()).toEqual({
        provider: "kiwitaxi",
        placement,
        ...(placement === "route_landing" ? { route_slug: "tivat-airport-kotor" } : {}),
      });
      expect(beacon).toHaveBeenCalledTimes(1);
    },
  );
});

describe("privacy-light attribution", () => {
  it("keeps only bounded reviewed labels, with no free-form names, phone numbers or tokens", () => {
    expect(
      sanitizeAttribution({
        utm_source: " Instagram ",
        utm_medium: "organic",
        utm_campaign: "john_smith",
        utm_content: "reel_01",
        utm_term: "guest@example.com",
        session: "secret",
      }),
    ).toEqual({ utm_source: "instagram", utm_medium: "organic", utm_content: "reel_01" });
    for (const value of [
      "+38267000000",
      "1234567890",
      "private-token",
      "reel_123456789",
      "https://example.com",
      "a".repeat(49),
      {},
      null,
    ]) {
      expect(sanitizeAttribution({ utm_content: value })).toEqual({});
    }
  });
  it("carries sanitized landing labels across internal navigation within the tab", () => {
    window.history.replaceState(
      {},
      "",
      "/transfer/podgorica-kotor?utm_source=instagram&utm_content=reel_01&token=private",
    );
    expect(captureRevenueAttribution()).toEqual({
      utm_source: "instagram",
      utm_content: "reel_01",
    });
    window.history.replaceState({}, "", "/");
    expect(captureRevenueAttribution()).toEqual({
      utm_source: "instagram",
      utm_content: "reel_01",
    });
    expect(sessionStorage.length).toBe(1);
    expect(sessionStorage.getItem(sessionStorage.key(0)!)).not.toMatch(
      /token|private|https|transfer/,
    );
    window.history.replaceState({}, "", "/?utm_source=someone@example.com");
    expect(captureRevenueAttribution()).toEqual({}); // invalid new campaign does not inherit old labels
  });
  it("works with unavailable storage and ignores corrupt stored attribution", () => {
    sessionStorage.setItem("atlas.revenue.attribution.v1", "not JSON");
    expect(captureRevenueAttribution()).toEqual({});
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw Error("blocked");
    });
    window.history.replaceState({}, "", "/?utm_source=youtube");
    expect(captureRevenueAttribution()).toEqual({ utm_source: "youtube" });
  });
});
