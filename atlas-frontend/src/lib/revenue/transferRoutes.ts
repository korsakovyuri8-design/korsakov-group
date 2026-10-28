import catalog from "./transferRoutes.json";

export const ATLAS_ORIGIN = "https://atlas.korsakovgroup.com";
export type TransferRoute = (typeof catalog)[number];
export const transferRoutes: readonly TransferRoute[] = catalog;

export function findTransferRoute(slug: string): TransferRoute | undefined {
  return transferRoutes.find((route) => route.slug === slug);
}

export function transferUrl(route: TransferRoute): string {
  return `${ATLAS_ORIGIN}/transfer/${route.slug}`;
}

export function transferStructuredData(route: TransferRoute) {
  return {
    "@context": "https://schema.org",
    "@type": "WebPage",
    name: route.title,
    description: route.description,
    url: transferUrl(route),
    inLanguage: "en",
    isPartOf: { "@type": "WebSite", name: "Korsakov Atlas", url: `${ATLAS_ORIGIN}/` },
  };
}

export function transferHead(route: TransferRoute) {
  return {
    meta: [
      { title: route.title },
      { name: "description", content: route.description },
      { name: "robots", content: route.indexable ? "index, follow" : "noindex, follow" },
      { property: "og:title", content: route.title },
      { property: "og:description", content: route.description },
      { property: "og:type", content: "website" },
      { property: "og:url", content: transferUrl(route) },
      { property: "og:site_name", content: "Korsakov Atlas" },
      { name: "twitter:card", content: "summary" },
      { name: "twitter:title", content: route.title },
      { name: "twitter:description", content: route.description },
    ],
    links: [{ rel: "canonical", href: transferUrl(route) }],
  };
}

const xmlEscape = (value: string) =>
  value.replace(
    /[<>&"']/g,
    (char) =>
      ({
        "<": "&lt;",
        ">": "&gt;",
        "&": "&amp;",
        '"': "&quot;",
        "'": "&apos;",
      })[char]!,
  );

export function transferSitemap(routes: readonly TransferRoute[] = transferRoutes): string {
  const urls = [`${ATLAS_ORIGIN}/`, ...routes.filter((r) => r.indexable).map(transferUrl)];
  return (
    '<?xml version="1.0" encoding="UTF-8"?>\n' +
    '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n' +
    urls.map((url) => `  <url><loc>${xmlEscape(url)}</loc></url>`).join("\n") +
    "\n</urlset>\n"
  );
}

/** Handled before the app router so crawlers never enter the private shell. */
export function sitemapResponse(request: Request): Response | undefined {
  if (new URL(request.url).pathname !== "/sitemap.xml") return undefined;
  if (!["GET", "HEAD"].includes(request.method)) {
    return new Response(null, { status: 405, headers: { Allow: "GET, HEAD" } });
  }
  return new Response(request.method === "HEAD" ? null : transferSitemap(), {
    headers: {
      "Content-Type": "application/xml; charset=utf-8",
      "Cache-Control": "public, max-age=3600",
      "X-Content-Type-Options": "nosniff",
    },
  });
}
