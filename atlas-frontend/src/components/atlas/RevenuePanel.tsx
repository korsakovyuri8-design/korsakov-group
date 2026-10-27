import { ExternalLink } from "lucide-react";

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
    if (url.protocol !== "https:") return undefined;
    return url.toString();
  } catch {
    return undefined;
  }
}

function revenueEnv(name: string): string | undefined {
  const viteValue = (
    import.meta.env as Record<string, string | undefined>
  )[name];

  if (typeof viteValue === "string" && viteValue.trim()) {
    return viteValue;
  }

  if (typeof process !== "undefined") {
    const runtimeValue = process.env?.[name];

    if (
      typeof runtimeValue === "string"
      && runtimeValue.trim()
    ) {
      return runtimeValue;
    }
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
          <a
            key={offer.id}
            href={offer.url}
            target="_blank"
            rel={
              offer.affiliate
                ? "noopener noreferrer sponsored"
                : "noopener noreferrer"
            }
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
          </a>
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
