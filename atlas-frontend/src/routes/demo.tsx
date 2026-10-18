import { createFileRoute, Link } from "@tanstack/react-router";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Timeline } from "@/components/atlas/Timeline";
import { Wordmark } from "@/components/atlas/AppShell";
import { demoStory } from "@/lib/atlas/demo-data";

export const Route = createFileRoute("/demo")({
  head: () => ({
    meta: [
      { title: "The 90-second story — Korsakov Atlas" },
      { name: "description", content: "Watch Atlas understand a Montenegro trip, find a gap, and catch a flight change." },
      { property: "og:title", content: "Korsakov Atlas in 90 seconds" },
      { property: "og:description", content: "London → Podgorica → Kotor, understood." },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary_large_image" },
    ],
  }),
  component: Demo,
});

function Demo() {
  const [i, setI] = useState(0);
  const s = demoStory[i] ?? demoStory[0]!;
  const last = i >= demoStory.length - 1;
  return (
    <div className="mx-auto min-h-dvh max-w-2xl px-5 py-6">
      <div className="flex items-center justify-between">
        <Link to="/"><Wordmark /></Link>
        <span className="eyebrow">{i + 1} / {demoStory.length}</span>
      </div>
      <div key={i} className="animate-rise mt-10">
        <h1 className="font-display break-words text-4xl leading-tight">{s.title}</h1>
        <p className="mt-3 text-muted-foreground">{s.body}</p>
      </div>
      <div className="mt-8"><Timeline items={s.items} /></div>
      <div className="sticky bottom-4 mt-10 flex gap-2">
        <Button variant="outline" disabled={i === 0} onClick={() => setI(i - 1)}>Back</Button>
        {last
          ? <Button className="flex-1" asChild><Link to="/">Open Atlas</Link></Button>
          : <Button className="flex-1" onClick={() => setI(i + 1)}>{s.cta ?? "Continue"}</Button>}
      </div>
    </div>
  );
}
