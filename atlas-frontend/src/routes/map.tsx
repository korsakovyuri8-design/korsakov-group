import { createFileRoute } from "@tanstack/react-router";
import { useState } from "react";
import { prefetchTrips } from "@/lib/atlas/scenario";
import { nextItem, plannedLocationText } from "@/lib/atlas/summary";
import { AtlasMap } from "@/components/atlas/map/AtlasMap";
import { useAtlas } from "@/components/atlas/status";
import type { MapPlace } from "@/lib/atlas/domain";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/map")({
  head: () => ({
    meta: [
      { title: "Map — Korsakov Atlas" },
      { name: "description", content: "Your hotel, bookings and suggestions that fit, on one map." },
      { property: "og:title", content: "Journey map — Korsakov Atlas" },
      { property: "og:description", content: "One view of the journey: where you stay, where you're due next." },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary_large_image" },
    ],
  }),
  loader: ({ context }) => prefetchTrips(context.queryClient),
  component: MapPage,
});

const travel = (m: number | null) => (m == null ? "travel time unknown" : `${m} min`);

function MapPage() {
  const atlas = useAtlas();
  const { trip, context } = atlas;
  const [layer, setLayer] = useState<"today" | "fits">("today");
  const [selectedId, setSelectedId] = useState<string>();
  const w = trip.freeWindow;
  const fits = w?.status === "valid" ? w.suggestions : [];
  const selected = fits.find((s) => s.id === selectedId);
  const suggestionPlaces: MapPlace[] = fits.map((s) => ({ id: s.id, label: s.name, role: "suggestion", sketch: s.sketch, coords: s.coords }));
  const places = layer === "fits" ? [...trip.places, ...suggestionPlaces] : trip.places;
  const next = atlas.nextItem ? atlas.nextItem.item : nextItem(atlas);

  return (
    <div className="relative h-[calc(100dvh-13rem)] min-h-[30rem] md:h-dvh md:p-6">
      <AtlasMap places={places} path={trip.mapPath} selectedId={layer === "fits" ? selectedId : undefined} className="size-full rounded-none md:rounded-2xl" />
      <div className="absolute inset-x-3 bottom-3 rounded-2xl border border-border bg-card/95 p-4 shadow-sheet backdrop-blur md:inset-x-auto md:bottom-10 md:left-10 md:w-80">
        <div className="mx-auto mb-3 h-1 w-10 rounded-full bg-border md:hidden" />
        <p className="eyebrow">{trip.title} · {context.day} · <span className="time normal-case tracking-normal">{context.time}</span></p>
        <p className="mt-1 text-xs text-muted-foreground">{plannedLocationText(context) ?? "No planned location yet."}</p>

        <div className="mt-3 grid grid-cols-2 rounded-lg bg-secondary p-1 text-sm">
          {(["today", "fits"] as const).map((l) => (
            <button key={l} onClick={() => setLayer(l)} className={cn("rounded-md py-1.5", layer === l ? "bg-card font-medium shadow-lift" : "text-muted-foreground")}>
              {l === "today" ? "Today" : "Things that fit"}
            </button>
          ))}
        </div>

        {layer === "fits" && (
          w?.status === "on_hold" ? <p className="mt-3 text-sm text-muted-foreground">On hold — your arrival is not proven.</p>
          : fits.length === 0 ? <p className="mt-3 text-sm text-muted-foreground">Nothing that fits yet.</p>
          : (
            <ul className="mt-3 space-y-1">
              {fits.map((s) => (
                <li key={s.id}>
                  <button onClick={() => setSelectedId(s.id === selectedId ? undefined : s.id)} aria-pressed={s.id === selectedId}
                    className={cn("flex w-full items-baseline justify-between gap-3 rounded-lg px-2 py-1.5 text-left text-sm", s.id === selectedId ? "bg-free-soft" : "hover:bg-secondary")}>
                    <span className="min-w-0 truncate">{s.name}</span>
                    <span className="time shrink-0 text-xs text-muted-foreground">{s.visit.start}–{s.visit.end}</span>
                  </button>
                </li>
              ))}
            </ul>
          )
        )}
        {layer === "fits" && selected && w && (
          <p className="time mt-2 text-xs text-muted-foreground">{w.from.place} → {travel(selected.travel.fromMinutes)} → {selected.name} → {travel(selected.travel.toMinutes)} → {w.to.place}</p>
        )}

        <p className="mt-3 min-w-0 border-t border-border pt-3 text-sm">
          {next?.start ? <>Next: <span className="font-medium">{next.title}</span> at <span className="time">{next.start}</span></> : "Nothing else confirmed today."}
        </p>
      </div>
    </div>
  );
}
