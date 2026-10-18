import { Link, useRouterState } from "@tanstack/react-router";
import { CalendarRange, Compass, Map, Plus, Sun, UserRound } from "lucide-react";
import type { ReactNode } from "react";
import { useSuspenseQuery } from "@tanstack/react-query";
import { atlasQuery, scenarioOptions } from "@/lib/atlas/api";
import { setScenario, useScenario } from "@/lib/atlas/scenario";
import { cn } from "@/lib/utils";
import { SessionGate } from "./SessionGate";

const nav = [
  { to: "/", label: "Today", icon: Sun },
  { to: "/trip", label: "Trip", icon: CalendarRange },
  { to: "/discover", label: "Discover", icon: Compass },
  { to: "/map", label: "Map", icon: Map },
] as const;

export function Wordmark({ className }: { className?: string }) {
  return (
    <span className={cn("inline-flex items-baseline gap-1.5", className)}>
      <span className="text-[0.7rem] font-semibold tracking-[0.28em]">KORSAKOV</span>
      <span className="font-display text-lg italic leading-none">Atlas</span>
    </span>
  );
}

/** Demo-only control: pick one coherent moment of the journey. */
export function ScenarioSwitch({ className }: { className?: string }) {
  const scenario = useScenario();
  return (
    <div className={cn("flex items-center gap-2", className)}>
      <span className="eyebrow shrink-0">Demo</span>
      <div role="radiogroup" aria-label="Demo trip state" className="grid flex-1 grid-cols-4 rounded-lg bg-secondary p-0.5 text-[0.68rem] leading-tight">
        {scenarioOptions.map(({ id, label }) => (
          <button key={id} role="radio" aria-checked={scenario === id} onClick={() => setScenario(id)}
            className={cn("rounded-md px-1 py-1", scenario === id ? "bg-card font-medium shadow-lift" : "text-muted-foreground")}>{label}</button>
        ))}
      </div>
    </div>
  );
}

export function AppShell({ children }: { children: ReactNode }) {
  const path = useRouterState({ select: (s) => s.location.pathname });
  const { data } = useSuspenseQuery(atlasQuery(useScenario()));
  const trip = data.trip;
  const hasScenarios = scenarioOptions.length > 0;
  if (path.startsWith("/demo")) return <>{children}</>;

  return (
    <div className="min-h-dvh md:grid md:grid-cols-[15rem_1fr]">
      {/* Desktop rail */}
      <aside className="sticky top-0 hidden h-dvh flex-col border-r border-border px-5 py-6 md:flex">
        <Link to="/"><Wordmark /></Link>
        <div className="mt-10">
          <p className="eyebrow">Current trip</p>
          <p className="font-display mt-1 text-2xl">{trip.title}</p>
          {trip.dates && <p className="text-sm text-muted-foreground">{trip.dates}</p>}
          {hasScenarios && <ScenarioSwitch className="mt-4" />}
        </div>
        <nav className="mt-8 space-y-1" aria-label="Main">
          {nav.map((n) => (
            <Link
              key={n.to}
              to={n.to}
              activeOptions={{ exact: n.to === "/" }}
              className="flex items-center gap-3 rounded-lg px-3 py-2 text-sm text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground data-[status=active]:bg-secondary data-[status=active]:text-foreground data-[status=active]:font-medium"
            >
              <n.icon className="size-4" strokeWidth={1.75} /> {n.label}
            </Link>
          ))}
        </nav>
        <Link to="/import" className="mt-6 inline-flex items-center justify-center gap-2 rounded-lg bg-primary px-3 py-2.5 text-sm font-medium text-primary-foreground hover:bg-primary/90">
          <Plus className="size-4" /> Add to trip
        </Link>
        <div className="mt-auto space-y-1">
          <Link to="/demo" className="block rounded-lg px-3 py-2 text-sm text-muted-foreground hover:bg-secondary">See the 90-second story</Link>
          <Link to="/profile" className="flex items-center gap-3 rounded-lg px-3 py-2 text-sm text-muted-foreground hover:bg-secondary data-[status=active]:text-foreground">
            <UserRound className="size-4" strokeWidth={1.75} /> Preferences
          </Link>
        </div>
      </aside>

      <div className="min-w-0">
        {/* Mobile top bar */}
        <header className="sticky top-0 z-30 border-b border-border/60 bg-background/85 px-5 py-3 backdrop-blur md:hidden">
          <div className="flex items-center justify-between">
          <Link to="/"><Wordmark /></Link>
          <div className="flex items-center gap-1">
            <Link to="/import" aria-label="Add to trip" className="grid size-9 place-items-center rounded-full bg-primary text-primary-foreground"><Plus className="size-4" /></Link>
            <Link to="/profile" aria-label="Preferences" className="grid size-9 place-items-center rounded-full text-muted-foreground hover:bg-secondary"><UserRound className="size-4" /></Link>
          </div>
          </div>
          {hasScenarios && <ScenarioSwitch className="mt-2.5" />}
        </header>

        <main className="pb-32 md:pb-12">{path.startsWith("/import") ? children : <SessionGate session={data.session}>{children}</SessionGate>}</main>

        {/* Mobile tab bar — thumb zone */}
        <nav aria-label="Main" className="fixed inset-x-3 bottom-3 z-40 grid grid-cols-4 rounded-2xl border border-border bg-card/95 p-1 shadow-sheet backdrop-blur md:hidden">
          {nav.map((n) => (
            <Link
              key={n.to}
              to={n.to}
              activeOptions={{ exact: n.to === "/" }}
              className="flex flex-col items-center gap-0.5 rounded-xl py-2 text-[0.68rem] text-muted-foreground data-[status=active]:bg-secondary data-[status=active]:text-foreground"
            >
              <n.icon className="size-5" strokeWidth={1.6} />
              {n.label}
            </Link>
          ))}
        </nav>
      </div>
    </div>
  );
}

export function PageHeader({ eyebrow, title, children }: { eyebrow?: string; title: ReactNode; children?: ReactNode }) {
  return (
    <header className="px-5 pt-8 md:px-10 md:pt-12">
      {eyebrow && <p className="eyebrow">{eyebrow}</p>}
      <h1 className="font-display mt-2 break-words text-[2.4rem] leading-[1.02] md:text-5xl">{title}</h1>
      {children && <div className="mt-3 max-w-xl text-muted-foreground">{children}</div>}
    </header>
  );
}
