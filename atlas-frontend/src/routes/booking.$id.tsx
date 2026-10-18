import { createFileRoute, Link, notFound } from "@tanstack/react-router";
import { ArrowLeft, FileText } from "lucide-react";
import { prefetchTrips } from "@/lib/atlas/scenario";
import { kindIcon } from "@/components/atlas/Timeline";
import { StatusTag, useAtlas } from "@/components/atlas/status";

export const Route = createFileRoute("/booking/$id")({
  loader: async ({ context, params }) => {
    const states = await prefetchTrips(context.queryClient);
    if (!states.length) return { title: "Booking" }; // live session: resolved in the browser
    const item = states.flatMap((s) => [...s.trip.items, ...(s.trip.bookings ?? [])]).find((i) => i.id === params.id && i.isBooking);
    if (!item) throw notFound();
    return { title: item.title };
  },
  head: ({ loaderData }) => ({
    meta: [
      { title: loaderData ? `${loaderData.title} — Korsakov Atlas` : "Booking — Korsakov Atlas" },
      { name: "description", content: "Status, changes and source for this part of your trip." },
      { property: "og:title", content: loaderData?.title ?? "Booking — Korsakov Atlas" },
      { property: "og:description", content: "Status, changes and source for this part of your trip." },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary" },
    ],
  }),
  notFoundComponent: () => <p className="p-10">Atlas has no booking with this reference.</p>,
  component: Detail,
});

function Detail() {
  const { id } = Route.useParams();
  const { trip } = useAtlas();
  const item = [...trip.items, ...(trip.bookings ?? [])].find((i) => i.id === id);
  if (!item) return <p className="p-10">This booking isn’t part of the trip in its current state.</p>;
  const Icon = kindIcon[item.kind];
  const issue = trip.issues.find((i) => i.relatedIds.includes(item.id));
  const record = [["Provider", item.provider], ["Reference", item.reference]].filter((f): f is [string, string] => !!f[1]);

  return (
    <div className="max-w-2xl px-5 pt-6 pb-10 md:px-10 md:pt-10">
      <Link to="/trip" className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"><ArrowLeft className="size-4" /> Trip</Link>

      <div className="mt-6 flex items-center gap-2 text-muted-foreground"><Icon className="size-4" /><span className="eyebrow">{item.kind} · {item.day}</span></div>
      <h1 className="font-display mt-2 break-words text-4xl md:text-5xl">{item.title}</h1>
      {item.subtitle && <p className="mt-1 text-muted-foreground">{item.subtitle}</p>}

      {item.changes && item.changes.length > 0 ? (
        <section className="mt-7 overflow-hidden rounded-2xl border border-changed/30" aria-label="What changed">
          {item.changes.map((c) => (
            <div key={c.field} className="grid grid-cols-[minmax(0,1fr)_auto_auto_auto] items-baseline gap-3 border-b border-changed/20 px-4 py-3 last:border-0">
              <p className="text-sm font-medium">{c.field}</p>
              <p className="text-right"><span className="eyebrow block">Original</span><span className="time text-xl text-muted-foreground line-through decoration-changed">{c.from}</span></p>
              <span className="text-muted-foreground" aria-hidden>→</span>
              <p className="text-right"><span className="eyebrow block text-changed">Current</span><span className="time text-xl">{c.to}</span></p>
            </div>
          ))}
        </section>
      ) : item.start ? (
        <p className="time mt-6 text-3xl">{item.start}{item.end && <span className="text-muted-foreground"> – {item.end}</span>}</p>
      ) : (
        <p className="mt-6 text-muted-foreground">Time not available.</p>
      )}

      {issue && (
        <div className="mt-6 rounded-xl border border-attention/40 bg-attention-soft p-4 text-sm">
          <p className="font-medium">{issue.title}</p>
          <p className="mt-1 text-muted-foreground">{issue.why}</p>
        </div>
      )}

      <dl className="mt-8 divide-y divide-border border-y border-border text-sm">
        <div className="flex items-center justify-between gap-4 py-3"><dt className="text-muted-foreground">Status</dt><dd><StatusTag status={item.status} className="text-sm" /></dd></div>
        {record.map(([k, v]) => (
          <div key={k} className="flex justify-between gap-4 py-3"><dt className="text-muted-foreground">{k}</dt><dd className="time min-w-0 break-words text-right font-medium">{v}</dd></div>
        ))}
        {(item.facts ?? []).map((f) => (
          <div key={f.label} className="flex justify-between gap-4 py-3"><dt className="text-muted-foreground">{f.label}</dt><dd className="min-w-0 break-words text-right font-medium">{f.value}</dd></div>
        ))}
      </dl>

      {item.source && (
        <p className="mt-6 flex items-center gap-2 text-xs text-muted-foreground">
          <FileText className="size-3.5 shrink-0" /> From your {item.source.label}{item.source.receivedAt ? ` · ${item.source.receivedAt}` : ""}
        </p>
      )}
    </div>
  );
}
