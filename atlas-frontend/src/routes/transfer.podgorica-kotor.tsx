import { createFileRoute } from "@tanstack/react-router";
import { ArrowRight, ExternalLink, ShieldCheck } from "lucide-react";

import { configuredRevenueOffers } from "@/components/atlas/RevenuePanel";

export const Route = createFileRoute(
  "/transfer/podgorica-kotor",
)({
  head: () => ({
    meta: [
      { title: "Podgorica to Kotor Transfer — Atlas" },
      {
        name: "description",
        content:
          "Plan Podgorica to Kotor ground transport with Atlas and check live private-transfer prices with a partner.",
      },
      {
        property: "og:title",
        content: "Podgorica to Kotor Transfer — Atlas",
      },
      {
        property: "og:description",
        content:
          "Plan the route, then check live transfer prices with a partner.",
      },
      { property: "og:type", content: "website" },
    ],
    links: [
      {
        rel: "canonical",
        href:
          "https://atlas.korsakovgroup.com/transfer/podgorica-kotor",
      },
    ],
  }),
  component: PodgoricaKotorTransferPage,
});

export function PodgoricaKotorTransferPage() {
  const kiwitaxi = configuredRevenueOffers().find(
    (item) => item.id === "kiwitaxi",
  );

  return (
    <main className="min-h-dvh bg-background">
      <header className="border-b border-border px-5 py-5 md:px-10">
        <a href="/" className="inline-flex items-baseline gap-1.5">
          <span className="text-[0.7rem] font-semibold tracking-[0.28em]">
            KORSAKOV
          </span>
          <span className="font-display text-lg italic leading-none">
            Atlas
          </span>
        </a>
      </header>

      <section className="mx-auto max-w-5xl px-5 py-12 md:px-10 md:py-20">
        <p className="eyebrow">Montenegro transfer</p>

        <h1 className="font-display mt-3 max-w-3xl text-5xl leading-[0.98] md:text-7xl">
          Podgorica
          <span className="mx-3 text-muted-foreground">→</span>
          Kotor
        </h1>

        <p className="mt-6 max-w-2xl text-base leading-relaxed text-muted-foreground md:text-lg">
          Keep the transfer connected to the rest of your trip in Atlas.
          When you are ready to buy, check current private-transfer prices
          and availability with the partner.
        </p>

        <div className="mt-8 flex flex-wrap gap-3">
          {kiwitaxi && (
            <a
              href={kiwitaxi.url}
              target="_blank"
              rel="noopener noreferrer sponsored"
              className="inline-flex items-center gap-2 rounded-xl bg-foreground px-5 py-3 text-sm font-medium text-background"
            >
              Check live transfer prices
              <ExternalLink aria-hidden="true" className="size-4" />
            </a>
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
            Kiwitaxi is an external partner. Its site is authoritative for
            live price, availability and booking. Atlas may earn a commission
            if you book through this link.
          </p>
        )}

        <div className="mt-14 grid gap-4 md:grid-cols-2">
          <article className="surface p-6">
            <p className="eyebrow">Atlas</p>
            <h2 className="font-display mt-2 text-3xl">
              Keep transport connected to the trip.
            </h2>
            <p className="mt-3 text-sm leading-relaxed text-muted-foreground">
              Atlas keeps stated plans, confirmed bookings and transport gaps
              separate, so missing transport is never silently treated as booked.
            </p>
          </article>

          <article className="surface p-6">
            <div className="flex items-center gap-2">
              <ShieldCheck aria-hidden="true" className="size-4" />
              <p className="eyebrow">Partner checkout</p>
            </div>

            <h2 className="font-display mt-2 text-3xl">
              Buy only after the partner confirms the offer.
            </h2>
            <p className="mt-3 text-sm leading-relaxed text-muted-foreground">
              Atlas does not claim a live fare or reservation before the
              external provider confirms it.
            </p>
          </article>
        </div>
      </section>
    </main>
  );
}
