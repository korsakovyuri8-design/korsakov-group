// @vitest-environment node
import { describe, expect, it } from "vitest";
import { sitemapResponse, transferSitemap } from "@/lib/revenue/transferRoutes";

describe("sitemap HTTP entry", () => {
  it("serves XML, HEAD and correct method status without the application router", async () => {
    const response = sitemapResponse(new Request("http://localhost/sitemap.xml"))!;
    expect(response.status).toBe(200);
    expect(response.headers.get("content-type")).toBe("application/xml; charset=utf-8");
    expect(await response.text()).toBe(transferSitemap());
    const head = sitemapResponse(new Request("http://localhost/sitemap.xml", { method: "HEAD" }))!;
    expect(head.status).toBe(200);
    expect(await head.text()).toBe("");
    expect(
      sitemapResponse(new Request("http://localhost/sitemap.xml", { method: "POST" }))!.status,
    ).toBe(405);
    expect(sitemapResponse(new Request("http://localhost/trip"))).toBeUndefined();
  });
});
