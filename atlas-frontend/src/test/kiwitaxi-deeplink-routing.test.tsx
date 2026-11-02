import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { configuredRevenueOffers, configuredTransferKiwitaxiOffer } from "@/components/atlas/RevenuePanel";
import { TransferPage, loadTransferRoute } from "@/routes/transfer.$slug";

const generic = "https://kiwitaxi.com/en?pap=testgeneric123";
const mne = "https://kiwitaxi.com/en/montenegro/podgorica-kotor?pap=testpartner123&chan=atlas_mne";
const uz = "https://kiwitaxi.com/en/uzbekistan/tashkent-airport-samarkand?pap=testpartner123&chan=atlas_uz";

function href(slug: string): string | null {
  const html = renderToStaticMarkup(<TransferPage route={loadTransferRoute(slug)} />);
  const doc = new DOMParser().parseFromString(html, "text/html");
  const link = doc.querySelector('a[rel~="sponsored"]');
  expect(link?.getAttribute("rel")).toBe("noopener noreferrer sponsored");
  expect(doc.body.textContent).toContain("check whether it serves your route");
  return link?.getAttribute("href") ?? null;
}

afterEach(() => vi.unstubAllEnvs());

describe("Kiwitaxi approved route-specific overrides", () => {
  it("uses exactly the two verified catalog overrides, with a general fallback on seven other routes", () => {
    vi.stubEnv("VITE_ATLAS_AFFILIATE_KIWITAXI_URL", generic);
    vi.stubEnv("VITE_ATLAS_AFFILIATE_KIWITAXI_MNE_URL", mne);
    vi.stubEnv("VITE_ATLAS_AFFILIATE_KIWITAXI_UZ_URL", uz);

    expect(href("podgorica-kotor")).toBe(mne);
    expect(href("tashkent-airport-samarkand")).toBe(uz);
    for (const slug of [
      "tivat-airport-kotor", "podgorica-airport-kotor", "podgorica-airport-budva",
      "tivat-airport-budva", "dubrovnik-airport-kotor", "podgorica-budva",
      "tashkent-airport-tashkent",
    ]) expect(href(slug)).toBe(generic);

    // Generic revenue panel and unknown/private trips must not assume a route.
    expect(configuredRevenueOffers().find((offer) => offer.id === "kiwitaxi")?.url).toBe(generic);
  });

  it.each([
    "http://kiwitaxi.com/en/montenegro/podgorica-kotor?pap=testpartner123&chan=atlas_mne",
    "https://evil.example/en/montenegro/podgorica-kotor?pap=testpartner123&chan=atlas_mne",
    "https://user:pass@kiwitaxi.com/en/montenegro/podgorica-kotor?pap=testpartner123&chan=atlas_mne",
    "https://kiwitaxi.com/en/uzbekistan/tashkent-airport-samarkand?pap=testpartner123&chan=atlas_mne",
    "https://kiwitaxi.com/en/montenegro/podgorica-kotor?chan=atlas_mne",
    "https://kiwitaxi.com/en/montenegro/podgorica-kotor?pap=testpartner123&chan=atlas_uz",
    "https://kiwitaxi.com/en/montenegro/podgorica-kotor?pap=testpartner123&chan=atlas_mne&session=private",
    "https://kiwitaxi.com/en/montenegro/podgorica-kotor?pap=testpartner123&pap=other123&chan=atlas_mne",
    "https://kiwitaxi.com/en/montenegro/podgorica-kotor?pap=testpartner123&chan=atlas_mne#fragment",
  ])("refuses malformed override and preserves base affiliate link: %s", (bad) => {
    vi.stubEnv("VITE_ATLAS_AFFILIATE_KIWITAXI_URL", generic);
    vi.stubEnv("VITE_ATLAS_AFFILIATE_KIWITAXI_MNE_URL", bad);
    expect(configuredTransferKiwitaxiOffer("podgorica-kotor")?.url).toBe(generic);
  });

  it("does not show affiliate CTA without its approved base configuration", () => {
    vi.stubEnv("VITE_ATLAS_AFFILIATE_KIWITAXI_URL", "");
    vi.stubEnv("VITE_ATLAS_AFFILIATE_KIWITAXI_MNE_URL", mne);
    expect(configuredTransferKiwitaxiOffer("podgorica-kotor")).toBeUndefined();
  });
});
