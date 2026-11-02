import { ExternalLink } from "lucide-react";
import { RevenueOutboundLink } from "./RevenueOutboundLink";

type RevenueOffer = {
  id: "kiwitaxi" | "discovercars" | "viator" | "concierge";
  title: string;
  description: string;
  url: string;
  affiliate: boolean;
};

function safeHttpsUrl(raw: unknown): string | undefined {
  if (typeof raw !== "string" || !raw.trim()) return undefined;

  try {
    const url = new URL(raw.trim());
    if (url.protocol !== "https:" || url.username || url.password) return undefined;
    return url.toString();
  } catch {
    return undefined;
  }
}

declare const __ATLAS_REVENUE__: Record<
  string,
  string | undefined
>;

function revenueEnv(name: string): string | undefined {
  const viteValue = (
    import.meta.env as Record<string, string | undefined>
  )[name];

  if (
    typeof viteValue === "string"
    && viteValue.trim()
  ) {
    return viteValue;
  }

  const buildValue =
    typeof __ATLAS_REVENUE__ !== "undefined"
      ? __ATLAS_REVENUE__[name]
      : undefined;

  if (
    typeof buildValue === "string"
    && buildValue.trim()
  ) {
    return buildValue;
  }

  return undefined;
}

export function configuredRevenueOffers(): RevenueOffer[] {
  const specs: Array<{
    id: RevenueOffer["id"];
    title: string;
    description: string;
    raw: string | undefined;
    affiliate: boolean;
  }> = [
    {
      id: "kiwitaxi",
      title: "Check transfer prices",
      description:
        "Continue to Kiwitaxi to check current transfer availability and pricing.",
      raw: revenueEnv("VITE_ATLAS_AFFILIATE_KIWITAXI_URL"),
      affiliate: true,
    },
    {
      id: "discovercars",
      title: "Compare rental cars",
      description:
        "Continue to DiscoverCars to compare current rental-car offers.",
      raw: revenueEnv("VITE_ATLAS_AFFILIATE_DISCOVERCARS_URL"),
      affiliate: true,
    },
    {
      id: "viator",
      title: "Find experiences",
      description:
        "Continue to Viator to browse tours, tickets and activities.",
      raw: revenueEnv("VITE_ATLAS_AFFILIATE_VIATOR_URL"),
      affiliate: true,
    },
    {
      id: "concierge",
      title: "Ask Atlas Concierge",
      description:
        "Get a personal trip review and booking-support session.",
      raw: revenueEnv("VITE_ATLAS_CONCIERGE_URL"),
      affiliate: false,
    },
  ];

  return specs.flatMap((spec) => {
    const url = safeHttpsUrl(spec.raw);

    if (!url) return [];

    return [{
      id: spec.id,
      title: spec.title,
      description: spec.description,
      url,
      affiliate: spec.affiliate,
    }];
  });
}

/**
 * Approved route-specific Kiwitaxi links. Only two exact catalog slugs have
 * generated deep links. All other routes and the generic revenue panel retain
 * the existing approved partner URL. Never invent or synthesize deep links.
 */
const KIWITAXI_ROUTE_OVERRIDE: Record<string, {
  env: string;
  path: string;
  channel: string;
}> = {
  "podgorica-kotor": {
    env: "VITE_ATLAS_AFFILIATE_KIWITAXI_MNE_URL",
    path: "/en/montenegro/podgorica-kotor",
    channel: "atlas_mne",
  },
  "tashkent-airport-samarkand": {
    env: "VITE_ATLAS_AFFILIATE_KIWITAXI_UZ_URL",
    path: "/en/uzbekistan/tashkent-airport-samarkand",
    channel: "atlas_uz",
  },
};

export function configuredTransferKiwitaxiOffer(routeSlug: string): RevenueOffer | undefined {
  const generic = configuredRevenueOffers().find((offer) => offer.id === "kiwitaxi");
  if (!generic) return undefined;

  const spec = KIWITAXI_ROUTE_OVERRIDE[routeSlug];
  if (!spec) return generic;

  const override = safeHttpsUrl(revenueEnv(spec.env));
  if (!override) return generic;

  const url = new URL(override);
  const query = url.searchParams;
  const keys = [...query.keys()].sort();
  const pap = query.get("pap");

  // The generator may accept a URL which later fails on the partner website.
  // Validate that the link at least carries precisely the expected partner
  // attribution and route. Actual partner-page behavior must be checked by
  // an operator in Chrome before configuring the production build.
  if (
    url.hostname !== "kiwitaxi.com" || url.port || url.hash ||
    url.pathname !== spec.path ||
    keys.join(",") !== "chan,pap" ||
    query.get("chan") !== spec.channel ||
    !pap || !/^[A-Za-z0-9_-]{6,64}$/.test(pap)
  ) return generic;

  return { ...generic, url: url.toString() };
}

export function RevenuePanel() {
  const offers = configuredRevenueOffers();

  if (!offers.length) return null;

  const hasAffiliate = offers.some((offer) => offer.affiliate);

  return (
    <div className="surface p-5">
      <p className="eyebrow">
        Continue with a partner
      </p>

      <p className="mt-2 max-w-xl text-sm leading-relaxed text-muted-foreground">
        Atlas can hand you off to external services for live prices,
        availability and booking. The partner site is authoritative for
        anything you buy there.
      </p>

      <div className="mt-4 grid gap-3 sm:grid-cols-2">
        {offers.map((offer) => (
          <RevenueOutboundLink
            key={offer.id}
            href={offer.url}
            provider={offer.id}
            placement="revenue_panel"
            affiliate={offer.affiliate}
            className="rounded-xl border border-border px-4 py-4 transition-colors hover:bg-secondary"
          >
            <div className="flex items-start justify-between gap-3">
              <div>
                <p className="font-medium">
                  {offer.title}
                </p>

                <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
                  {offer.description}
                </p>
              </div>

              <ExternalLink
                aria-hidden="true"
                className="mt-0.5 size-4 shrink-0 text-muted-foreground"
              />
            </div>
          </RevenueOutboundLink>
        ))}
      </div>

      {hasAffiliate && (
        <p className="mt-4 text-xs leading-relaxed text-muted-foreground">
          Atlas may earn a commission if you book through an affiliate link.
          This does not change the price Atlas displays because Atlas does not
          set or confirm the partner&apos;s live price.
        </p>
      )}
    </div>
  );
}
