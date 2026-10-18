import { Check, CircleDashed, CircleDot, CirclePause, Clock, History, TriangleAlert, X } from "lucide-react";
import { useSuspenseQuery } from "@tanstack/react-query";
import { atlasQuery } from "@/lib/atlas/api";
import { useScenario } from "@/lib/atlas/scenario";
import type { ItemStatus } from "@/lib/atlas/domain";
import { cn } from "@/lib/utils";

export const statusMeta: Record<ItemStatus, { label: string; icon: typeof Check; tone: string; dot: string }> = {
  confirmed: { label: "Confirmed", icon: Check, tone: "text-confirmed", dot: "bg-confirmed border-confirmed" },
  planned: { label: "Planned", icon: CircleDot, tone: "text-free", dot: "bg-free-soft border-free" },
  tentative: { label: "Not confirmed", icon: Clock, tone: "text-muted-foreground", dot: "bg-card border-muted-foreground border-dotted" },
  changed: { label: "Changed", icon: History, tone: "text-changed", dot: "bg-changed border-changed" },
  cancelled: { label: "Cancelled", icon: X, tone: "text-cancelled", dot: "bg-card border-cancelled" },
  missing: { label: "Missing", icon: CircleDashed, tone: "text-attention", dot: "bg-background border-attention border-dashed" },
  attention: { label: "May no longer fit", icon: TriangleAlert, tone: "text-attention", dot: "bg-attention border-attention" },
  listed: { label: "", icon: CircleDot, tone: "text-muted-foreground", dot: "bg-card border-primary" },
  paused: { label: "On hold", icon: CirclePause, tone: "text-muted-foreground", dot: "bg-background border-muted-foreground border-dashed" },
};

export function StatusTag({ status, className }: { status: ItemStatus; className?: string }) {
  const m = statusMeta[status];
  if (!m.label) return null;
  const Icon = m.icon;
  return (
    <span className={cn("inline-flex items-center gap-1 text-[0.72rem] font-medium", m.tone, className)}>
      <Icon className="size-3.5" strokeWidth={2} aria-hidden />
      {m.label}
    </span>
  );
}

/** The provider's current AtlasState: { state, context, trip }. */
export function useAtlas() {
  return useSuspenseQuery(atlasQuery(useScenario())).data;
}
