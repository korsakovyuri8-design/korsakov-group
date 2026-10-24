import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it } from "vitest";

import type { RoutePlan } from "@/lib/atlas/domain";
import { mapTrip } from "@/lib/atlas/providers/api";
import { RoutePlanCard } from "./RoutePlanCard";

const GEOGRAPHY = {
  coordinate_precision: "city_centroid",
  resolver: "atlas_geonames_snapshot",
  source_attribution: "GeoNames",
  source_url: "https://www.geonames.org/",
  source_license: "CC BY 4.0",
  source_license_url: "https://creativecommons.org/licenses/by/4.0/",
  source_modified: true,
  source_sha256: "catalog-hash-kept-in-backend",
};

function mappedRoute(geography: Record<string, unknown> = {}): RoutePlan {
  const state = mapTrip({
    items: [{
      key: "plan|geography",
      kind: "plan",
      detail: {
        place: "Munich, Germany",
        origin_text: "Berlin, Germany",
        route_plan: {
          origin: {
            name: "Berlin, Germany",
            coordinates: { lat: 52.52, lon: 13.405 },
            ...geography,
          },
          destination: {
            name: "Munich, Germany",
            coordinates: { lat: 48.137, lon: 11.575 },
            ...geography,
          },
          distance_km: 580,
          route_duration_minutes: 360,
          planning_buffer_minutes: 30,
          arrive_by_local: "2026-11-20T18:00",
          leave_by_local: "2026-11-20T11:30",
          timezone: "Europe/Berlin",
          traffic_live: false,
          provider_guarantee: false,
        },
      },
    }],
  });

  const route = state.trip.routePlan;
  if (!route) throw new Error("Expected a mapped route plan");
  return route;
}

function showRoute(route: RoutePlan) {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });

  return render(
    <QueryClientProvider client={client}>
      <RoutePlanCard route={route} />
    </QueryClientProvider>,
  );
}

describe("Route geography attribution", () => {
  it("carries API provenance into visible credit once for both endpoints", () => {
    const route = mappedRoute(GEOGRAPHY);

    expect(route.origin).toMatchObject({
      coordinatePrecision: "city_centroid",
      sourceAttribution: "GeoNames",
      sourceUrl: "https://www.geonames.org/",
      sourceLicense: "CC BY 4.0",
      sourceLicenseUrl: "https://creativecommons.org/licenses/by/4.0/",
      sourceModified: true,
    });
    expect(route.origin).not.toHaveProperty("sourceSha256");

    showRoute(route);

    const sources = screen.getAllByRole("link", { name: "GeoNames" });
    expect(sources).toHaveLength(1);
    expect(sources[0]).toHaveAttribute("href", "https://www.geonames.org/");
    expect(screen.getByRole("link", { name: "CC BY 4.0" })).toHaveAttribute(
      "href", "https://creativecommons.org/licenses/by/4.0/",
    );
    expect(screen.getByText(/adapted by Atlas/)).toBeInTheDocument();
    expect(screen.getByText("City locations are approximate.")).toBeInTheDocument();
  });

  it("leaves missing provenance absent without inventing a source or precision", () => {
    const route = mappedRoute();

    expect(route.origin).not.toHaveProperty("coordinatePrecision");
    expect(route.origin).not.toHaveProperty("sourceAttribution");
    expect(route.origin).not.toHaveProperty("sourceModified");

    showRoute(route);

    expect(screen.queryByText(/Geographic data:/)).not.toBeInTheDocument();
    expect(screen.queryByText("City locations are approximate.")).not.toBeInTheDocument();
  });

  it("preserves false modification evidence and displays unsafe source URLs as text", () => {
    const route = mappedRoute({
      ...GEOGRAPHY,
      source_modified: false,
      source_url: "javascript:alert('source')",
    });

    expect(route.origin.sourceModified).toBe(false);
    showRoute(route);

    expect(screen.queryByRole("link", { name: "GeoNames" })).not.toBeInTheDocument();
    expect(screen.getByText(/Geographic data: GeoNames/)).toBeInTheDocument();
    expect(screen.queryByText(/adapted by Atlas/)).not.toBeInTheDocument();
  });
});
