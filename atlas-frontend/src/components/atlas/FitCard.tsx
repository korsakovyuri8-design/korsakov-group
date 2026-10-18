import { ShieldCheck, ShieldQuestion } from "lucide-react";
import type { DiscoverySuggestion, FreeWindow } from "@/lib/atlas/domain";
import { cn } from "@/lib/utils";

// Shown only when the source exposes opening-hours evidence; Atlas already used hours to compute the visit.
const hoursText = (h: NonNullable<DiscoverySuggestion["hours"]>) =>
  `${h.label ?? "Opening hours"} · ${h.confidence === "verified" ? "verified" : "not verified"}`;

function Stop({ kicker, place, time, accent, last }: { kicker: string; place: string; time?: string | undefined; accent?: boolean; last?: boolean }) {
  return (
    <li className="grid grid-cols-[1rem_minmax(0,1fr)_auto] items-center gap-x-3">
      <span className="flex justify-center" aria-hidden>
        <span className={cn("rounded-full border-2", accent ? "size-3.5 border-free bg-free" : last ? "size-2.5 border-primary bg-primary" : "size-2.5 border-primary bg-card")} />
      </span>
      <div className="min-w-0">
        <p className="eyebrow text-[0.6rem]">{kicker}</p>
        <p className={cn("break-words", accent ? "font-display text-xl leading-tight" : "text-sm")}>{place}</p>
      </div>
      {time && <span className={cn("time text-sm", accent ? "text-free" : "text-muted-foreground")}>{time}</span>}
    </li>
  );
}

function Leg({ mins, mode }: { mins: number | null; mode?: "walk" | undefined }) {
  const unknown = mins == null;
  const text = unknown ? "Travel time unknown" : `${mins} min${mode === "walk" ? " walk" : ""}`;
  return (
    <li className="grid grid-cols-[1rem_minmax(0,1fr)] gap-x-3" aria-label={text}>
      <span className="flex justify-center" aria-hidden>
        <span className={cn("my-1 h-7 w-px", unknown ? "rule-dashed" : "bg-primary/40")} />
      </span>
      <span className={cn("self-center text-xs", unknown ? "text-attention" : "text-muted-foreground")}>
        <span aria-hidden>↓ </span><span className="time">{text}</span>
      </span>
    </li>
  );
}

/** A recommendation drawn as a miniature route: START → TRAVEL → VISIT → TRAVEL → NEXT. */
export function FitCard({ s, w, index }: { s: DiscoverySuggestion; w: FreeWindow; index: number }) {
  return (
    <article className="surface animate-rise overflow-hidden" style={{ animationDelay: `${index * 90}ms` }}>
      {s.category && <p className="eyebrow px-4 pt-4">{s.category}</p>}
      <ol className="space-y-0 p-4" aria-label={`Route via ${s.name}`}>
        <Stop kicker={w.from.noun} place={w.from.place} time={s.leaveAt ? `leave ${s.leaveAt}` : `from ${w.start}`} />
        <Leg mins={s.travel.fromMinutes} mode={s.travel.mode} />
        <Stop kicker="Visit" place={s.name} time={`${s.visit.start}–${s.visit.end}`} accent />
        <Leg mins={s.travel.toMinutes} mode={s.travel.mode} />
        <Stop kicker={w.to.noun} place={w.to.place} time={s.arriveNextAt ? `arrive ${s.arriveNextAt}` : w.to.time} last />
      </ol>

      {(s.why || s.hours) && (
        <div className="border-t border-border bg-secondary/50 px-4 py-3">
          {s.why && <p className="text-sm"><span className="font-medium">Why it fits.</span> {s.why}</p>}
          {s.hours && (
            <p className={cn("inline-flex items-center gap-1 text-xs text-muted-foreground", s.why && "mt-1.5")}>
              {s.hours.confidence === "verified" ? <ShieldCheck className="size-3.5" /> : <ShieldQuestion className="size-3.5" />}
              {hoursText(s.hours)}
            </p>
          )}
        </div>
      )}
    </article>
  );
}
