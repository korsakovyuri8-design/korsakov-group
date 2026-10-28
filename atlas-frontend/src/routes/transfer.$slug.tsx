import { createFileRoute, notFound } from "@tanstack/react-router";
import { ArrowRight, ExternalLink } from "lucide-react";
import { configuredRevenueOffers } from "@/components/atlas/RevenuePanel";
import { RevenueOutboundLink } from "@/components/atlas/RevenueOutboundLink";
import {
  findTransferRoute,
  transferHead,
  transferStructuredData,
  type TransferRoute,
} from "@/lib/revenue/transferRoutes";

export function loadTransferRoute(slug: string): TransferRoute {
  const route = findTransferRoute(slug);
  if (!route) throw notFound();
  return route;
}

export const Route = createFileRoute("/transfer/$slug")({
  loader: ({ params }) => loadTransferRoute(params.slug),
  head: ({ loaderData }) =>
    loaderData
      ? transferHead(loaderData)
      : {
          meta: [
            { title: "Transfer guide not found — Atlas" },
            { name: "robots", content: "noindex, follow" },
          ],
        },
  component: () => <TransferPage route={Route.useLoaderData()} />,
});

export function TransferPage({ route }: { route: TransferRoute }) {
  const kiwitaxi = configuredRevenueOffers().find((item) => item.id === "kiwitaxi");
  return (
    <main className="min-h-dvh bg-background">
      <header className="border-b border-border px-5 py-5 md:px-10">
        <a href="/" className="inline-flex items-baseline gap-1.5" aria-label="Korsakov Atlas home">
          <span className="text-[0.7rem] font-semibold tracking-[0.28em]">KORSAKOV</span>
          <span className="font-display text-lg italic leading-none">Atlas</span>
        </a>
      </header>
      <section className="mx-auto max-w-5xl px-5 py-12 md:px-10 md:py-20">
        <p className="eyebrow">{route.country} transfer planning</p>
        <h1 className="font-display mt-3 max-w-4xl text-5xl leading-[0.98] md:text-7xl">
          {route.origin}
          <span className="mx-3 text-muted-foreground">→</span>
          {route.destination}
        </h1>
        <p className="mt-6 max-w-2xl text-base leading-relaxed text-muted-foreground md:text-lg">
          {route.intro}
        </p>
        <div className="mt-8 flex flex-wrap gap-3">
          {kiwitaxi && (
            <RevenueOutboundLink
              href={kiwitaxi.url}
              provider="kiwitaxi"
              placement="route_landing"
              routeSlug={route.slug}
              affiliate
              className="inline-flex items-center gap-2 rounded-xl bg-foreground px-5 py-3 text-sm font-medium text-background"
            >
              Check live transfer prices
              <ExternalLink aria-hidden="true" className="size-4" />
            </RevenueOutboundLink>
          )}
          <a
            href="/"
            className="inline-flex items-center gap-2 rounded-xl border border-border px-5 py-3 text-sm font-medium"
          >
            Open Atlas
            <ArrowRight aria-hidden="true" className="size-4" />
          </a>
        </div>
        {kiwitaxi && (
          <p className="mt-4 max-w-2xl text-xs leading-relaxed text-muted-foreground">
            Kiwitaxi is an external partner. Its site is authoritative for live price, availability
            and booking. Atlas may earn a commission if you book through this link. This link opens
            the partner’s general site; check whether it serves your route and date there.
          </p>
        )}
        <div className="mt-12 grid gap-4 md:grid-cols-3">
          {route.sections.map((section) => (
            <article key={section.title} className="surface p-6">
              <h2 className="font-display text-2xl">{section.title}</h2>
              <p className="mt-3 text-sm leading-relaxed text-muted-foreground">{section.body}</p>
            </article>
          ))}
        </div>
        <section className="mt-10 surface p-6" aria-labelledby="transfer-checklist">
          <h2 id="transfer-checklist" className="font-display text-3xl">
            Before you choose a transfer
          </h2>
          <ul className="mt-4 list-disc space-y-2 pl-5 text-sm">
            {route.checklist.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
          <p className="mt-4 text-sm leading-relaxed text-muted-foreground">
            Atlas keeps stated plans, confirmed bookings and transport gaps separate. Opening a
            partner link does not book a transfer or confirm that a provider serves this route.
            Review the external offer and terms before paying; rely on the provider’s confirmation
            for booking status.
          </p>
        </section>
        <nav className="mt-10" aria-label="Related transfer guides">
          <h2 className="font-display text-3xl">More transfer planning guides</h2>
          <ul className="mt-4 grid gap-3 sm:grid-cols-2">
            {route.related.map((slug) => {
              const related = findTransferRoute(slug);
              return related ? (
                <li key={slug}>
                  <a href={`/transfer/${slug}`} className="text-sm underline underline-offset-4">
                    {related.origin} → {related.destination}
                  </a>
                </li>
              ) : null;
            })}
          </ul>
        </nav>
      </section>
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{
          __html: JSON.stringify(transferStructuredData(route)).replace(/</g, "\\u003c"),
        }}
      />
    </main>
  );
}
