import type { ReactNode } from "react";
import { captureRevenueAttribution } from "@/lib/revenue/attribution";

export type RevenueProvider = "kiwitaxi" | "discovercars" | "viator" | "concierge";
export type RevenuePlacement = "missing_transport" | "route_landing" | "revenue_panel";
type Props = {
  href: string;
  provider: RevenueProvider;
  placement: RevenuePlacement;
  routeSlug?: string;
  affiliate: boolean;
  className?: string;
  children: ReactNode;
};

export function trackRevenueActivation(
  provider: RevenueProvider,
  placement: RevenuePlacement,
  routeSlug?: string,
) {
  // Never awaited, never retried after an accepted beacon or HTTP error.
  // A queued beacon is not proof that the server stored the event.
  try {
    const body = JSON.stringify({
      provider,
      placement,
      ...(routeSlug ? { route_slug: routeSlug } : {}),
      ...captureRevenueAttribution(),
    });
    try {
      if (
        typeof navigator.sendBeacon === "function" &&
        navigator.sendBeacon("/revenue/click", new Blob([body], { type: "application/json" }))
      )
        return;
    } catch {
      /* try fetch if the browser refused to queue the beacon */
    }
    void fetch("/revenue/click", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body,
      keepalive: true,
      credentials: "omit",
      referrerPolicy: "no-referrer",
    }).catch(() => {
      /* navigation is independent of telemetry */
    });
  } catch {
    /* unavailable browser APIs must never break the link */
  }
}

export function RevenueOutboundLink({
  href,
  provider,
  placement,
  routeSlug,
  affiliate,
  className,
  children,
}: Props) {
  // Defense in depth: configuration also validates URLs before rendering CTAs.
  try {
    const url = new URL(href);
    if (url.protocol !== "https:" || url.username || url.password) return null;
  } catch {
    return null;
  }

  return (
    <a
      href={href}
      target="_blank"
      rel={affiliate ? "noopener noreferrer sponsored" : "noopener noreferrer"}
      className={className}
      onClick={(event) => {
        if (event.button === 0 && !event.defaultPrevented)
          trackRevenueActivation(provider, placement, routeSlug);
      }}
      onAuxClick={(event) => {
        if (event.button === 1 && !event.defaultPrevented)
          trackRevenueActivation(provider, placement, routeSlug);
      }}
    >
      {children}
    </a>
  );
}
