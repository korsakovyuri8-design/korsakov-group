import { ExternalLink } from "lucide-react";

import { configuredRevenueOffers } from "@/components/atlas/RevenuePanel";
import { RevenueOutboundLink } from "./RevenueOutboundLink";

export function TransportAffiliateCta() {
  const offer = configuredRevenueOffers().find(
    (item) => item.id === "kiwitaxi",
  );

  if (!offer) return null;

  return (
    <div className="mt-4 border-t border-border/70 pt-4">
      <RevenueOutboundLink
        href={offer.url}
        provider="kiwitaxi"
        placement="missing_transport"
        affiliate
        className="inline-flex items-center gap-2 rounded-xl bg-foreground px-4 py-2 text-sm font-medium text-background"
      >
        Check live transfer prices
        <ExternalLink aria-hidden="true" className="size-3.5" />
      </RevenueOutboundLink>

      <p className="mt-2 max-w-md text-xs leading-relaxed text-muted-foreground">
        Live price, availability and booking are confirmed on Kiwitaxi.
        Atlas may earn a commission if you book through this link.
      </p>
    </div>
  );
}
