import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { ArrowRight, CarFront, Clock3 } from "lucide-react";

import type {
  PlanningNeed,
  RoutePlan,
  RoutePlanPoint,
} from "@/lib/atlas/domain";
import {
  canPlanRoute,
  planRoute,
} from "@/lib/atlas/api";

function hm(value: string) {
  return value.slice(11, 16);
}

function duration(minutes: number) {
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;

  if (!h) return `${m} min`;
  if (!m) return `${h} h`;

  return `${h} h ${m} min`;
}

function geographySources(route: RoutePlan) {
  const sources = new Map<string, RoutePlanPoint>();

  for (const point of [route.origin, route.destination]) {
    if (!point.sourceAttribution) continue;

    const key = JSON.stringify([
      point.sourceAttribution,
      point.sourceUrl,
      point.sourceLicense,
      point.sourceLicenseUrl,
      point.sourceModified,
    ]);
    sources.set(key, point);
  }

  return [...sources.entries()];
}

function sourceLink(label: string | undefined, value: string | undefined) {
  if (!label) return null;
  if (!value) return label;

  let url: URL;
  try {
    url = new URL(value);
  } catch {
    return label;
  }

  if (url.protocol !== "https:" && url.protocol !== "http:") return label;

  return (
    <a href={url.href} target="_blank" rel="noopener noreferrer" className="underline underline-offset-2">
      {label}
    </a>
  );
}

export function RoutePlanCard({
  route,
  needs,
}: {
  route?: RoutePlan | undefined;
  needs?: PlanningNeed[] | undefined;
}) {
  const qc = useQueryClient();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const transportNeed = needs?.find(
    (n) => n.code === "arrival_transport_unconfirmed",
  );

  async function calculate() {
    if (!transportNeed?.planKey || !canPlanRoute) return;

    setBusy(true);
    setError(null);

    try {
      await planRoute(qc, transportNeed.planKey);
    } catch (e) {
      setError(
        e instanceof Error
          ? e.message
          : "Atlas couldn't calculate the road route right now.",
      );
    } finally {
      setBusy(false);
    }
  }

  if (!route) {
    if (!transportNeed?.planKey || !canPlanRoute) return null;

    return (
      <section className="mx-5 mt-9 md:mx-10">
        <h2 className="eyebrow mb-3">Route to your plan</h2>

        <div className="surface p-5">
          <div className="flex gap-3">
            <div className="flex size-9 shrink-0 items-center justify-center rounded-full bg-secondary">
              <CarFront className="size-4 text-muted-foreground" />
            </div>

            <div>
              <p className="font-display text-2xl">
                Atlas can calculate the road route.
              </p>

              <p className="mt-2 text-sm leading-relaxed text-muted-foreground">
                You have supplied an origin, destination and arrival deadline.
                The estimate will not be treated as a transport booking.
              </p>

              <button
                type="button"
                disabled={busy}
                onClick={() => void calculate()}
                className="mt-4 rounded-xl bg-foreground px-4 py-2 text-sm font-medium text-background disabled:opacity-50"
              >
                {busy ? "Calculating…" : "Calculate route"}
              </button>
            </div>
          </div>

          {error && (
            <p className="mt-3 text-xs text-attention">{error}</p>
          )}
        </div>
      </section>
    );
  }

  const sources = geographySources(route);
  const hasCityLocations = [route.origin, route.destination].some(
    (point) => point.coordinatePrecision === "city_centroid",
  );

  return (
    <section className="mx-5 mt-9 md:mx-10">
      <h2 className="eyebrow mb-3">Route to your plan</h2>

      <div className="surface p-5">
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <span>{route.origin.name}</span>
          <ArrowRight className="size-4" />
          <span>{route.destination.name}</span>
        </div>

        <div className="mt-5 grid grid-cols-2 gap-4 border-y border-border py-5">
          <div>
            <p className="eyebrow">Leave by</p>
            <p className="time mt-1 text-3xl">
              {hm(route.leaveByLocal)}
            </p>
          </div>

          <div>
            <p className="eyebrow">Arrive by</p>
            <p className="time mt-1 text-3xl">
              {hm(route.arriveByLocal)}
            </p>
          </div>
        </div>

        <div className="mt-4 flex flex-wrap gap-x-5 gap-y-2 text-sm text-muted-foreground">
          <span>{route.distanceKm.toFixed(1)} km</span>

          <span className="inline-flex items-center gap-1.5">
            <Clock3 className="size-3.5" />
            {duration(route.routeDurationMinutes)}
          </span>

          <span>
            +{route.planningBufferMinutes} min Atlas buffer
          </span>
        </div>

        <div className="mt-4 border-t border-border pt-3 text-xs text-muted-foreground">
          Estimate · {route.trafficLive ? "live traffic" : "no live traffic"}
          {route.providerGuarantee ? "" : " · not guaranteed by provider"}
        </div>

        {(hasCityLocations || sources.length > 0) && (
          <div className="mt-2 space-y-1 text-xs text-muted-foreground">
            {hasCityLocations && <p>City locations are approximate.</p>}
            {sources.map(([key, source]) => (
              <p key={key}>
                Geographic data: {sourceLink(source.sourceAttribution, source.sourceUrl)}
                {source.sourceLicense && <> · {sourceLink(source.sourceLicense, source.sourceLicenseUrl)}</>}
                {source.sourceModified === true && " · adapted by Atlas"}
              </p>
            ))}
          </div>
        )}

        {transportNeed && (
          <div className="mt-4 rounded-xl bg-secondary px-3 py-2 text-sm">
            <strong>Transport is still not booked.</strong>{" "}
            Atlas calculated a route; it did not create a reservation.
          </div>
        )}
      </div>
    </section>
  );
}
