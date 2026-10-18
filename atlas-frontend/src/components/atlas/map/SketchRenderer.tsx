// DESIGN PLACEHOLDER — illustrative, not geographic. Uses place.sketch positions.
// If Atlas has no resolved geographic/map evidence, never imply a location.
import { MapPin } from "lucide-react";
import { cn } from "@/lib/utils";
import type { MapRendererProps } from "./AtlasMap";

export function hasMapEvidence(places: MapRendererProps["places"]) {
  return places.some((p) => Boolean(p.sketch || p.coords));
}

export function SketchRenderer({ places, path = [], selectedId, className }: MapRendererProps) {
  if (!hasMapEvidence(places)) {
    return (
      <div
        className={cn(
          "relative flex items-center justify-center overflow-hidden rounded-2xl border border-border bg-secondary/40",
          className,
        )}
        role="img"
        aria-label="Location not resolved yet"
      >
        <div className="max-w-64 px-6 text-center">
          <MapPin className="mx-auto size-5 text-muted-foreground" />
          <p className="font-display mt-3 text-xl">Location not resolved yet.</p>
          <p className="mt-2 text-xs leading-relaxed text-muted-foreground">
            Atlas will map this trip after a place is geographically established.
          </p>
        </div>
      </div>
    );
  }

  const pts = path
    .map((id) => places.find((p) => p.id === id)?.sketch)
    .filter((p): p is { x: number; y: number } => !!p);

  return (
    <div
      className={cn("relative overflow-hidden rounded-2xl bg-sea-soft", className)}
      role="img"
      aria-label="Illustrative map of the resolved trip area"
    >
      <svg viewBox="0 0 100 100" preserveAspectRatio="none" className="absolute inset-0 size-full">
        <path d="M0 0 H100 V18 C80 22 76 10 62 16 C50 22 40 12 28 20 C16 28 10 18 0 24 Z" className="fill-stone" />
        <path d="M0 100 V70 C14 64 22 78 36 72 C48 66 56 80 70 74 C84 68 90 80 100 76 V100 Z" className="fill-stone" />
        <path d="M58 0 C60 20 72 30 100 34 V0 Z" className="fill-border" opacity="0.6" />
        {pts.length > 1 && (
          <polyline
            points={pts.map((p) => `${p.x},${p.y}`).join(" ")}
            fill="none"
            className="stroke-primary"
            strokeWidth="0.6"
            strokeLinecap="round"
            strokeDasharray="1.2 1.2"
          />
        )}
      </svg>

      <span className="eyebrow absolute left-3 top-3">Boka Kotorska</span>
      <span className="absolute right-3 top-3 rounded-full bg-card/80 px-2 py-0.5 text-[0.62rem] text-muted-foreground">
        Illustrative map
      </span>

      {places.filter((p) => p.sketch).map((p) => (
        <div
          key={p.id}
          className="absolute -translate-x-1/2 -translate-y-1/2 transition-all duration-700"
          style={{ left: `${p.sketch!.x}%`, top: `${p.sketch!.y}%` }}
        >
          <span
            className={cn(
              "block rounded-full border-2 border-card",
              p.role === "stay" && "size-3.5 bg-primary",
              p.role === "booking" && "size-3 bg-primary",
              p.role === "next" && "size-3 bg-confirmed",
              p.role === "suggestion" && "size-3 bg-free",
              selectedId === p.id && "size-4 ring-4 ring-free/30",
            )}
          />
          <span
            className={cn(
              "absolute top-1/2 max-w-[9rem] -translate-y-1/2 truncate rounded bg-card/90 px-1.5 py-0.5 text-[0.65rem] font-medium shadow-lift",
              p.sketch!.x > 60 ? "right-4" : "left-4",
            )}
          >
            {p.label}
          </span>
        </div>
      ))}
    </div>
  );
}
