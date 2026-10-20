import { createFileRoute, Link } from "@tanstack/react-router";
import { ChevronRight } from "lucide-react";
import { prefetchTrips } from "@/lib/atlas/scenario";
import { PageHeader } from "@/components/atlas/AppShell";
import { PlanningNeeds } from "@/components/atlas/PlanningNeeds";
import { RoutePlanCard } from "@/components/atlas/RoutePlanCard";
import { MobilityOptions } from "@/components/atlas/MobilityOptions";
import { Timeline, kindIcon } from "@/components/atlas/Timeline";
import { StatusTag, useAtlas } from "@/components/atlas/status";
import type { ItemStatus } from "@/lib/atlas/domain";

export const Route = createFileRoute("/trip")({
  head: () => ({
    meta: [
      { title: "Trip — Korsakov Atlas" },
      { name: "description", content: "Every flight, transfer, hotel and dinner as one connected journey." },
      { property: "og:title", content: "Your trip — Korsakov Atlas" },
      { property: "og:description", content: "One connected journey, with every change kept in history." },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary_large_image" },
    ],
  }),
  loader: ({ context }) => prefetchTrips(context.queryClient),
  component: TripPage,
});

const legend: ItemStatus[] = ["confirmed", "changed", "attention", "paused", "planned", "tentative", "missing", "cancelled"];

function TripPage() {
  const { trip } = useAtlas();
  return (
    <div className="max-w-3xl">
      <PageHeader eyebrow={trip.dates} title={trip.route.join(" → ")} />
      <details className="mx-5 mt-5 md:mx-10">
        <summary className="cursor-pointer text-sm text-muted-foreground">How Atlas marks things</summary>
        <div className="mt-3 flex flex-wrap gap-x-4 gap-y-2">{legend.map((s) => <StatusTag key={s} status={s} />)}</div>
      </details>
      {trip.bookings && trip.bookings.length > 0 && (
        <section className="mx-5 mt-7 md:mx-10" aria-labelledby="bk">
          <h2 id="bk" className="eyebrow mb-2">{trip.bookings.length} booking{trip.bookings.length === 1 ? "" : "s"}</h2>
          <ul className="surface divide-y divide-border">
            {trip.bookings.map((b) => {
              const Icon = kindIcon[b.kind];
              return (
                <li key={b.id}>
                  <Link to="/booking/$id" params={{ id: b.id }} className="flex items-center gap-3 px-4 py-3 hover:bg-secondary">
                    <Icon className="size-4 shrink-0 text-muted-foreground" strokeWidth={1.75} />
                    <span className="min-w-0 flex-1"><span className="block break-words text-sm font-medium">{b.title}</span>{b.subtitle && <span className="time block text-xs text-muted-foreground">{b.subtitle}</span>}</span>
                    <StatusTag status={b.status} />
                    <ChevronRight className="size-4 shrink-0 text-muted-foreground" />
                  </Link>
                </li>
              );
            })}
          </ul>
        </section>
      )}
      <PlanningNeeds needs={trip.planningNeeds} />
      <RoutePlanCard route={trip.routePlan} needs={trip.planningNeeds} />
      <MobilityOptions
        planKey={trip.transportPlanKey}
        route={trip.routePlan}
        needs={trip.planningNeeds}
        confirmedTransport={trip.confirmedTransport}
        transportChange={trip.transportChange}
      />

      <div className="mx-3 mt-8 md:mx-8">
        <Timeline items={trip.items} endNote={trip.items.length ? "Nothing else is planned yet. Add another plan or booking when you're ready." : undefined} />
      </div>
    </div>
  );
}
