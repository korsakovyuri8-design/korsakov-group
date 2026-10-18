import { createFileRoute, Link } from "@tanstack/react-router";
import { ArrowRight, CirclePause } from "lucide-react";
import { prefetchTrips } from "@/lib/atlas/scenario";
import { formatDuration, headline, minutes, nextItem, plannedLocationText, restOfToday, splitHeadline } from "@/lib/atlas/summary";
import { NextSteps } from "@/components/atlas/NextSteps";
import { IssueCard } from "@/components/atlas/IssueCard";
import { Timeline, kindIcon } from "@/components/atlas/Timeline";
import { StatusTag, statusMeta, useAtlas } from "@/components/atlas/status";
import { AtlasMap } from "@/components/atlas/map/AtlasMap";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/")({
  head: () => ({
    meta: [
      { title: "Today — Korsakov Atlas" },
      { name: "description", content: "Your entire trip, understood. What's next, what changed, and what fits before your next booking." },
      { property: "og:title", content: "Korsakov Atlas — Your entire trip, understood" },
      { property: "og:description", content: "Forward your bookings. Atlas handles the rest." },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary_large_image" },
    ],
  }),
  loader: ({ context }) => prefetchTrips(context.queryClient),
  component: Today,
});

function Today() {
  const atlas = useAtlas();
  const { trip, context } = atlas;
  const h = headline(atlas);
  // Live: the backend's next item. Demo fixtures: formatted from the provider's clock.
  const next = atlas.nextItem ? atlas.nextItem.item : nextItem(atlas);
  const atRisk = (atlas.nextItem?.issues.length ?? 0) > 0;
  const inMins = atlas.nextItem ? atlas.nextItem.minutesFromNow : next?.start && context.time ? minutes(next.start) - minutes(context.time) : undefined;
  const rest = restOfToday(atlas);
  const statedPlans = trip.items.filter((i) => !i.isBooking && i.status === "tentative" && i.kind === "activity");
  const w = trip.freeWindow;
  const NextIcon = next ? kindIcon[next.kind] : null;
  const city = rest[0]?.city;

  return (
    <div className="lg:grid lg:grid-cols-[minmax(0,1fr)_24rem] lg:gap-10 lg:pr-10">
      <div className="min-w-0">
        <header className="px-5 pt-7 md:px-10 md:pt-12">
          <p className="eyebrow">
            {[context.day, city].filter(Boolean).join(" · ")}
            {context.time && <> · <span className="time normal-case tracking-normal">{context.time}</span></>}
            {context.clock === "frozen_demo" && <span className="normal-case tracking-normal"> · demo time</span>}
          </p>
          <h1 className="font-display mt-3 break-words text-[2.6rem] leading-[1.04] md:text-6xl">
            {h.lead}
            {h.emphasis && <em className={h.tone === "attention" ? "text-attention" : "text-free"}>{h.emphasis}</em>}
            {h.tail}
          </h1>
          <p className="mt-3 text-sm text-muted-foreground">
            {plannedLocationText(context) ?? "No planned location yet."} Atlas doesn’t track where you physically are.
          </p>
        </header>

        {next?.start && NextIcon && (
          <section className="mx-5 mt-7 md:mx-10" aria-labelledby="next">
            <Link to="/booking/$id" params={{ id: next.id }} className="surface-ink block p-5 shadow-lift transition-transform hover:-translate-y-0.5">
              <p id="next" className="text-[0.68rem] font-semibold uppercase tracking-[0.16em] text-ink-muted">Next</p>
              <div className="mt-3 grid grid-cols-[minmax(0,1fr)_auto] items-end gap-4">
                <div className="min-w-0">
                  <p className="font-display break-words text-3xl">{next.title}</p>
                  {next.subtitle && <p className="mt-1 text-sm text-ink-muted">{next.subtitle}</p>}
                </div>
                <div className="text-right">
                  {next.previousStart && <p className="time text-sm text-ink-muted line-through">{next.previousStart}</p>}
                  <p className="time text-4xl">{next.start}</p>
                  {inMins !== undefined && <p className="text-xs text-ink-muted">in {formatDuration(inMins)}</p>}
                </div>
              </div>
              <div className="mt-5 flex items-center gap-2 border-t border-ink-foreground/15 pt-3 text-xs text-ink-muted">
                <NextIcon className="size-3.5" /> {atRisk ? <span className="font-semibold text-attention">At risk · </span> : null}{statusMeta[next.status].label}{next.provider ? ` · ${next.provider}` : ""}{next.reference ? ` · ${next.reference}` : ""}
              </div>
            </Link>
          </section>
        )}

        {trip.issues.length > 0 && (
          <section className="mx-5 mt-9 md:mx-10" aria-labelledby="att">
            <div className="mb-3 flex items-baseline justify-between">
              <h2 id="att" className="eyebrow">Needs attention · {trip.issues.length}</h2>
              <Link to="/attention" className="text-sm text-muted-foreground hover:text-foreground">All</Link>
            </div>
            <div className="space-y-2">
              {trip.issues.slice(0, 2).map((i, n) => <IssueCard key={i.id} issue={i} defaultOpen={n === 0} />)}
            </div>
          </section>
        )}

        {atlas.next && atlas.next.length > 0 && <NextSteps steps={atlas.next} />}

        {statedPlans.length > 0 && (
          <section className="mx-5 mt-9 md:mx-10" aria-labelledby="stated-plan">
            <h2 id="stated-plan" className="eyebrow mb-3">Your stated plan</h2>
            <div className="space-y-2">
              {statedPlans.map((p) => (
                <div key={p.id} className="surface p-5">
                  <div className="flex items-start justify-between gap-4">
                    <div className="min-w-0">
                      <p className="font-display break-words text-2xl">
                        {p.title === "Stated plan" ? (p.city || p.title) : p.title}
                      </p>
                      {p.title !== "Stated plan" && p.city && (
                        <p className="mt-1 text-sm text-muted-foreground">{p.city}</p>
                      )}
                      {p.subtitle && <p className="mt-1 text-sm text-muted-foreground">{p.subtitle}</p>}
                    </div>
                    <StatusTag status="tentative" />
                  </div>
                  <p className="mt-3 text-sm text-muted-foreground">You told Atlas this plan. It is not treated as a confirmed booking.</p>
                </div>
              ))}
            </div>
          </section>
        )}

        {trip.heldWindows?.map((hw) => (
          <section key={hw.id} className="mx-5 mt-9 md:mx-10" aria-label={`Free time before ${hw.to.noun} on hold`}>
            <h2 className="eyebrow mb-3">Free time before {hw.to.noun}</h2>
            <div className="rounded-2xl border border-dashed border-muted-foreground/40 p-5">
              <StatusTag status="paused" />
              <p className="time mt-2 text-sm text-muted-foreground">{hw.start} → {hw.end}</p>
              <p className="font-display mt-1 text-2xl leading-snug">Your free time before {hw.to.noun} is on hold.</p>
              {hw.holdReason && <p className="mt-2 text-sm text-muted-foreground">{hw.holdReason}</p>}
            </div>
          </section>
        ))}

        {w && (
          <section className="mx-5 mt-9 md:mx-10" aria-labelledby="free">
            <h2 id="free" className="eyebrow mb-3">{w.day ? `Free time · ${w.day}` : `Free time before ${w.to.noun}`}</h2>
            {w.status === "valid" ? (
              <Link to="/discover" className="surface group block p-5 hover:shadow-lift">
                <p className="time text-sm text-free">{w.start} → {w.end}</p>
                <p className="font-display mt-1 text-2xl leading-snug">
                  {w.headline ? (splitHeadline(w.headline)[1] ?? w.headline) : w.suggestions.length === 0 ? "Nothing found that fits yet." : w.suggestions.length === 1 ? "1 thing that actually fits." : `${w.suggestions.length} things that actually fit.`}
                </p>
                <ul className="mt-4 divide-y divide-border">
                  {w.suggestions.map((s) => (
                    <li key={s.id} className="flex items-center justify-between gap-3 py-2.5 text-sm">
                      <span className="min-w-0 truncate">{s.name}</span>
                      <span className="time shrink-0 text-xs text-muted-foreground">{s.visit.start}–{s.visit.end}</span>
                    </li>
                  ))}
                </ul>
                <span className="mt-3 inline-flex items-center gap-1 text-sm font-medium text-free">See the route <ArrowRight className="size-4 transition group-hover:translate-x-0.5" /></span>
              </Link>
            ) : (
              <div className="rounded-2xl border border-dashed border-muted-foreground/40 p-5">
                <StatusTag status="paused" />
                <p className="font-display mt-2 text-2xl leading-snug">Your free time before {w.to.noun} is on hold.</p>
                {w.holdReason && <p className="mt-2 text-sm text-muted-foreground">{w.holdReason}</p>}
              </div>
            )}
          </section>
        )}

        {(rest.length > 0 || statedPlans.length === 0) && (
          <section className="mx-5 mt-10 md:mx-10" aria-labelledby="rest">
            <div className="mb-2 flex items-baseline justify-between">
              <h2 id="rest" className="eyebrow">Rest of today</h2>
              <Link to="/trip" className="text-sm text-muted-foreground hover:text-foreground">Whole trip</Link>
            </div>
            <Timeline items={rest} />
          </section>
        )}
      </div>

      <aside className="hidden lg:block">
        <div className="sticky top-12 mt-12 space-y-4">
          <AtlasMap
            places={[...trip.places, ...(w?.status === "valid" ? w.suggestions.map((s) => ({ id: s.id, label: s.name, role: "suggestion" as const, sketch: s.sketch, coords: s.coords })) : [])]}
            path={trip.mapPath}
            className={cn("aspect-[4/5]")}
          />
          {w?.status === "on_hold" && <p className="flex gap-2 text-xs text-muted-foreground"><CirclePause className="size-3.5 shrink-0" /> Suggestions are hidden while your arrival time is not proven.</p>}
        </div>
      </aside>
    </div>
  );
}
