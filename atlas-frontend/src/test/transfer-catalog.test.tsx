import { readFileSync } from "node:fs";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { isNotFound } from "@tanstack/react-router";
import { TransferPage, loadTransferRoute } from "@/routes/transfer.$slug";
import {
  ATLAS_ORIGIN,
  findTransferRoute,
  transferHead,
  transferRoutes,
  transferSitemap,
  transferStructuredData,
  transferUrl,
} from "@/lib/revenue/transferRoutes";

afterEach(() => vi.unstubAllEnvs());

describe("transfer catalog", () => {
  it("has exactly nine unique canonical definitions, original useful copy and reciprocal links", () => {
    expect(transferRoutes.map((r) => r.slug).sort()).toEqual(
      [
        "podgorica-kotor",
        "tivat-airport-kotor",
        "podgorica-airport-kotor",
        "podgorica-airport-budva",
        "tivat-airport-budva",
        "dubrovnik-airport-kotor",
        "podgorica-budva",
        "tashkent-airport-tashkent",
        "tashkent-airport-samarkand",
      ].sort(),
    );
    for (const field of ["title", "description", "intro"] as const)
      expect(new Set(transferRoutes.map((r) => r[field])).size).toBe(9);
    expect(new Set(transferRoutes.flatMap((r) => r.sections.map((s) => s.body))).size).toBe(27);
    for (const route of transferRoutes) {
      expect(route.sections).toHaveLength(3);
      expect(route.checklist).toHaveLength(3);
      expect(route.related.length).toBeGreaterThan(0);
      for (const slug of route.related)
        expect(findTransferRoute(slug)?.related).toContain(route.slug);
    }
  });
  it.each(transferRoutes)(
    "renders $slug content and honest partner disclosure in server markup",
    (route) => {
      vi.stubEnv("VITE_ATLAS_AFFILIATE_KIWITAXI_URL", "https://kiwitaxi.com/en?pap=test123");
      const html = renderToStaticMarkup(<TransferPage route={route} />);
      const doc = new DOMParser().parseFromString(html, "text/html");
      expect(doc.querySelector("h1")?.textContent).toBe(`${route.origin}→${route.destination}`);
      expect(
        doc.querySelector('a[href="https://kiwitaxi.com/en?pap=test123"]')?.getAttribute("rel"),
      ).toBe("noopener noreferrer sponsored");
      expect(html).toContain("Atlas may earn a commission");
      expect(html).toContain("check whether it serves your route");
      expect(doc.body.textContent).toContain(route.intro);
      expect(
        JSON.parse(doc.querySelector('script[type="application/ld+json"]')!.textContent!),
      ).toEqual(transferStructuredData(route));
      expect(transferStructuredData(route)).toMatchObject({
        "@type": "WebPage",
        url: transferUrl(route),
      });
      expect(JSON.stringify(transferStructuredData(route))).not.toMatch(
        /Offer|Product|price|rating|bookingStatus/,
      );
      for (const slug of route.related)
        expect(doc.querySelector(`a[href="/transfer/${slug}"]`)).not.toBeNull();
      const head = transferHead(route);
      expect(head.links).toEqual([
        { rel: "canonical", href: `${ATLAS_ORIGIN}/transfer/${route.slug}` },
      ]);
      expect(head.meta).toContainEqual({ title: route.title });
      expect(head.meta).toContainEqual({ name: "description", content: route.description });
      expect(head.meta).toContainEqual({ property: "og:url", content: transferUrl(route) });
      expect(head.meta).toContainEqual({ name: "twitter:title", content: route.title });
    },
  );
  it("hides affiliate links without configuration while retaining useful content", () => {
    const route = transferRoutes[0]!;
    const html = renderToStaticMarkup(<TransferPage route={route} />);
    expect(html).not.toContain("kiwitaxi.com");
    expect(html).not.toContain("Check live transfer prices");
    expect(html).toContain(route.sections[0]!.body);
  });
  it.each(["unknown", "Podgorica-Kotor", "podgorica-kotor/", "__proto__"])(
    "throws a router 404 for %s",
    (slug) => {
      try {
        loadTransferRoute(slug);
        throw Error("expected 404");
      } catch (error) {
        expect(isNotFound(error)).toBe(true);
      }
    },
  );
  it("sitemap is deterministic, valid XML and excludes noindex pages", () => {
    const xml = transferSitemap();
    expect(xml).toBe(transferSitemap());
    const parsed = new DOMParser().parseFromString(xml, "application/xml");
    expect(parsed.querySelector("parsererror")).toBeNull();
    expect([...parsed.querySelectorAll("loc")].map((e) => e.textContent)).toEqual([
      `${ATLAS_ORIGIN}/`,
      ...transferRoutes.map(transferUrl),
    ]);
    const excluded = { ...transferRoutes[0]!, indexable: false };
    expect(transferSitemap([excluded])).not.toContain(transferUrl(excluded));
    expect(transferHead(excluded).meta).toContainEqual({
      name: "robots",
      content: "noindex, follow",
    });
    const robots = readFileSync("public/robots.txt", "utf8");
    expect(robots).toContain(`Sitemap: ${ATLAS_ORIGIN}/sitemap.xml`);
  });
});
