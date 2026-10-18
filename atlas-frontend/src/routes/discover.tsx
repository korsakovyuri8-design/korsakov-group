import { createFileRoute, Link } from "@tanstack/react-router";
import { prefetchTrips } from "@/lib/atlas/scenario";
import { formatDuration, formatHours, isClock, minutes, splitHeadline } from "@/lib/atlas/summary";
import { FitCard } from "@/components/atlas/FitCard";
import { StatusTag, useAtlas } from "@/components/atlas/status";
import { Button } from "@/components/ui/button";

export const Route = createFileRoute("/discover")({
  head: () => ({
    meta: [
      { title: "Things that fit — Korsakov Atlas" },
      { name: "description", content: "Free time between two confirmed bookings, and what actually fits inside it." },
      { property: "og:title", content: "Things that actually fit — Korsakov Atlas" },
      { property: "og:description", content: "Recommendations checked against travel time, opening hours and your next booking." },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary_large_image" },
    ],
  }),
  loader: ({ context }) => prefetchTrips(context.queryClient),
  component: Discover,
});

function Discover() {
  const { trip } = useAtlas();
  const w = trip.freeWindow;

  if (!w) {
    return (
      <div className="max-w-3xl px-5 pt-8 md:px-10 md:pt-12">
        <p className="eyebrow">Free time</p>
        <h1 className="font-display mt-2 text-[2.4rem] leading-[1.02]">No free window yet.</h1>
        <p className="mt-3 text-muted-foreground">Atlas suggests things only between two proven places.</p>
      </div>
    );
  }

  if (w.status === "on_hold") {
    const action = trip.issues.find((i) => i.action)?.action;
    return (
      <div className="max-w-3xl px-5 pt-8 md:px-10 md:pt-12">
        <p className="eyebrow">Free time · before {w.to.noun}</p>
        <h1 className="font-display mt-2 text-[2.4rem] leading-[1.02] md:text-5xl">Your free time before {w.to.noun} is on hold.</h1>
        <div className="mt-6 rounded-2xl border border-dashed border-muted-foreground/40 p-5">
          <StatusTag status="paused" />
          {w.holdReason && <p className="mt-2">{w.holdReason}</p>}
          <p className="mt-2 text-sm text-muted-foreground">Atlas won’t suggest anything until it knows where you’ll be.</p>
          {action && <Button className="mt-4" asChild><Link to="/booking/$id" params={{ id: action.itemId }}>{action.label}</Link></Button>}
        </div>
      </div>
    );
  }

  const fromT = w.from.time ?? w.start;
  const toT = w.to.time ?? w.end;
  const span = Math.max(1, minutes(toT) - minutes(fromT));
  const [title, sub] = w.headline ? splitHeadline(w.headline) : [undefined, undefined];
  const left = ((minutes(w.start) - minutes(fromT)) / span) * 100;
  const right = ((minutes(toT) - minutes(w.end)) / span) * 100;
  const n = w.suggestions.length;

  return (
    <div className="max-w-3xl">
      <header className="px-5 pt-8 md:px-10 md:pt-12">
        <p className="eyebrow">Free time{w.day ? ` · ${w.day}` : ""} · {w.from.place}</p>
        <h1 className="font-display mt-2 break-words text-[2.4rem] leading-[1.02] md:text-5xl">{title ?? `You have ${formatHours(w.durationMinutes)} before ${w.to.noun}.`}</h1>
        <p className="mt-2 text-lg text-muted-foreground">
          {sub ?? (n === 0 ? "Atlas hasn’t found anything that fits yet." : n === 1 ? "Here is 1 thing that actually fits." : `Here are ${n} things that actually fit.`)}
        </p>
        {w.considered != null && w.leftOut != null && (
          <p className="mt-1 text-sm text-muted-foreground">Atlas checked {w.considered} places; {w.leftOut} didn’t fit this window.</p>
        )}
      </header>

      <div className="mx-5 mt-7 md:mx-10" aria-label="Free time window">
        <div className="flex justify-between gap-3 text-xs text-muted-foreground">
          <span className="min-w-0 truncate">{w.from.place} {w.from.time && <span className="time">{w.from.time}</span>}</span>
          <span className="min-w-0 truncate text-right">{w.to.place} {w.to.time && <span className="time">{w.to.time}</span>}</span>
        </div>
        <div className="relative mt-2 h-10 rounded-lg bg-secondary">
          <div className="absolute inset-y-0 rounded-md border border-free/40 bg-free-soft" style={{ left: `${left}%`, right: `${right}%` }} />
          <span className="time absolute top-1/2 -translate-y-1/2 pl-2 text-xs text-free" style={{ left: `${left}%` }}>{w.start}</span>
          <span className="time absolute top-1/2 -translate-y-1/2 pr-2 text-xs text-free" style={{ right: `${right}%` }}>{w.end}</span>
        </div>
        {w.endBufferMinutes != null
          ? <p className="mt-2 text-xs text-muted-foreground">Ends {formatDuration(w.endBufferMinutes)} before your {w.to.noun}.</p>
          : isClock(w.to.time) && <p className="mt-2 text-xs text-muted-foreground">Ends {formatDuration(minutes(w.to.time) - minutes(w.end))} before {w.to.noun}.</p>}
      </div>

      <div className="mx-5 mt-8 space-y-4 md:mx-10">
        {w.suggestions.map((s, i) => <FitCard key={s.id} s={s} w={w} index={i} />)}
      </div>
    </div>
  );
}
