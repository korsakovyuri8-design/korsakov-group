import { captureRevenueAttribution } from "@/lib/revenue/attribution";
import { findTransferRoute } from "@/lib/revenue/transferRoutes";

/** A hydrated public landing, not a unique person or a guaranteed delivery.
 * Navigation never depends on this best-effort request.
 */
export function trackRevenuePageView(slug: string): void {
  if (typeof window === "undefined") return;
  const route = findTransferRoute(slug);
  if (!route || !route.indexable) return;
  try {
    const body = JSON.stringify({ route_slug: slug, ...captureRevenueAttribution() });
    try {
      if (typeof navigator.sendBeacon === "function" &&
        navigator.sendBeacon("/revenue/view", new Blob([body], { type: "application/json" })))
        return;
    } catch {
      /* use one keepalive fetch if the beacon was not queued */
    }
    void fetch("/revenue/view", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body,
      keepalive: true,
      credentials: "omit",
      referrerPolicy: "no-referrer",
    }).catch(() => {
      /* telemetry must never block the route */
    });
  } catch {
    /* unavailable browser APIs are best-effort, not an app error */
  }
}
