import { Link } from "@tanstack/react-router";
import { BedDouble, Car, ChevronRight, Hourglass, Plane, Utensils, MapPin } from "lucide-react";
import type { ItemKind, TripItem } from "@/lib/atlas/domain";
import { cn } from "@/lib/utils";
import { StatusTag, statusMeta } from "./status";

export const kindIcon: Record<ItemKind, typeof Plane> = {
  flight: Plane,
  transfer: Car,
  hotel: BedDouble,
  free: Hourglass,
  restaurant: Utensils,
  activity: MapPin,
};

function Row({ item, last }: { item: TripItem; last: boolean }) {
  const Icon = kindIcon[item.kind];
  const m = statusMeta[item.status];
  const isFree = item.kind === "free";
  const isMissing = item.status === "missing";
  const paused = item.status === "paused";
  const cancelled = item.status === "cancelled";
  const linkable = item.isBooking;

  const body = (
    <div
      className={cn(
        "group flex items-start gap-3 rounded-xl px-3 py-3 transition-colors",
        isFree && !paused && "bg-free-soft",
        paused && "border border-dashed border-muted-foreground/40 bg-transparent",
        isMissing && "border border-dashed border-attention bg-attention-soft/50",
        item.status === "attention" && "bg-attention-soft/60 ring-1 ring-attention/40",
        linkable && "hover:bg-secondary",
        cancelled && "opacity-55",
      )}
    >
      <Icon className={cn("mt-0.5 size-4 shrink-0", m.tone)} strokeWidth={1.75} aria-hidden />
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline justify-between gap-2">
          <p className={cn("min-w-0 break-words text-[0.95rem] font-medium", (cancelled || paused) && "text-muted-foreground", cancelled && "line-through")}>{item.title}</p>
          {linkable && <ChevronRight className="size-4 shrink-0 text-muted-foreground opacity-0 transition group-hover:opacity-100" />}
        </div>
        {item.subtitle && <p className="break-words text-sm text-muted-foreground">{item.subtitle}</p>}
        <StatusTag status={item.status} className="mt-1.5" />
      </div>
    </div>
  );

  return (
    <li className="relative grid grid-cols-[3.25rem_1rem_minmax(0,1fr)] gap-x-2 animate-rise">
      <div className="pt-3 text-right">
        {item.previousStart && <span className="time block text-xs text-muted-foreground line-through decoration-changed" aria-label={`Originally ${item.previousStart}`}>{item.previousStart}</span>}
        {item.start
          ? <span className={cn("time block text-sm font-medium", item.previousStart && "text-changed", (cancelled || paused) && "text-muted-foreground", cancelled && "line-through")}>{item.start}</span>
          : <span className="time block text-sm text-muted-foreground" aria-label="Time unknown">—</span>}
        {item.end && isFree && <span className="time block text-xs text-muted-foreground">{item.end}</span>}
      </div>
      <div className="relative flex justify-center">
        {!last && <span className={cn("absolute top-6 bottom-[-0.75rem] w-px", isMissing || paused ? "rule-dashed" : "bg-border")} aria-hidden />}
        <span className={cn("relative z-10 mt-4 size-2.5 rounded-full border-2", m.dot)} aria-hidden />
      </div>
      <div className="pb-1">
        {linkable ? (
          <Link to="/booking/$id" params={{ id: item.id }} className="block rounded-xl focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
            {body}
          </Link>
        ) : body}
      </div>
    </li>
  );
}

export function Timeline({ items, groupByCity = true, endNote }: { items: TripItem[]; groupByCity?: boolean; endNote?: string | undefined }) {
  if (items.length === 0) return <p className="px-3 py-6 text-sm text-muted-foreground">Nothing planned here yet.</p>;
  const groups: { key: string; city: string; day: string; items: TripItem[] }[] = [];
  for (const it of items) {
    const last = groups[groups.length - 1];
    if (groupByCity && last && last.city === it.city && last.day === it.day) last.items.push(it);
    else groups.push({ key: `${it.day}-${it.city}-${groups.length}`, city: it.city, day: it.day, items: [it] });
  }
  let prevDay = "";
  return (
    <div className="space-y-5">
      {groups.map((g, gi) => {
        const showDay = g.day !== prevDay;
        prevDay = g.day;
        return (
          <section key={g.key} aria-label={`${g.city}, ${g.day}`}>
            {g.city
              ? <>{showDay && gi > 0 && <p className="eyebrow mb-3 mt-8">{g.day}</p>}<h3 className="font-display mb-1 break-words pl-[4.75rem] text-2xl">{g.city}</h3></>
              : showDay && <h3 className={`font-display mb-1 break-words pl-[4.75rem] text-2xl ${gi > 0 ? "mt-8" : ""}`}>{g.day}</h3>}
            <ol>
              {g.items.map((it, i) => (
                <Row key={it.id} item={it} last={gi === groups.length - 1 && i === g.items.length - 1} />
              ))}
            </ol>
          </section>
        );
      })}
      {endNote && <p className="pl-[4.75rem] text-sm text-muted-foreground">{endNote}</p>}
    </div>
  );
}
