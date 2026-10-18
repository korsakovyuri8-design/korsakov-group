import { createFileRoute, Link } from "@tanstack/react-router";
import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Check, FileText, FileUp, Inbox, Loader2, Mail, ShieldCheck } from "lucide-react";
import { canStartSample, importBooking, sampleImportText, startSample } from "@/lib/atlas/api";
import type { ImportResult } from "@/lib/atlas/domain";
import { Button } from "@/components/ui/button";
import { kindIcon } from "@/components/atlas/Timeline";

export const Route = createFileRoute("/import")({
  head: () => ({
    meta: [
      { title: "Add to trip — Korsakov Atlas" },
      { name: "description", content: "Tell Atlas your plans or forward a booking. Atlas keeps stated plans tentative and confirmed evidence separate." },
      { property: "og:title", content: "Forward your bookings. Atlas handles the rest." },
      { property: "og:description", content: "Describe a plan or paste a confirmation. Atlas connects only what it can prove to your journey." },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary_large_image" },
    ],
  }),
  component: ImportPage,
});

function ImportPage() {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<ImportResult | null>(null);

  const qc = useQueryClient();
  async function submit() {
    setBusy(true);
    setResult(null);
    setResult(await importBooking(qc, { text }));
    setBusy(false);
  }
  // Live: ask Atlas for its sample trip. Offline: fill the fixture text.
  async function useSample() {
    if (!canStartSample) { setText(sampleImportText ?? ""); setResult(null); return; }
    setBusy(true);
    setResult(null);
    setResult(await startSample(qc));
    setBusy(false);
  }

  return (
    <div className="max-w-2xl">
      <header className="px-5 pt-8 md:px-10 md:pt-12">
        <p className="eyebrow">Import</p>
        <h1 className="font-display mt-2 text-[2.4rem] leading-[1.02] md:text-5xl">Tell Atlas your plans.<br /><em>Forward your bookings.</em></h1>
      </header>

      <div className="mx-5 mt-8 md:mx-10">
        <div className="flex items-baseline justify-between gap-3">
          <label htmlFor="conf" className="eyebrow">Plan or booking</label>
          {(canStartSample || sampleImportText) && <button className="text-xs text-muted-foreground underline-offset-2 hover:underline disabled:opacity-50" disabled={busy} onClick={useSample}>Use demo sample</button>}
        </div>
        <textarea
          id="conf"
          value={text}
          onChange={(e) => { setText(e.target.value); setResult(null); }}
          rows={7}
          placeholder="Paste a booking confirmation, or tell Atlas where and when you plan to go…"
          className="time mt-2 w-full resize-y rounded-xl border border-input bg-card p-4 text-sm leading-relaxed placeholder:font-sans placeholder:text-muted-foreground/70 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        />
        <Button className="mt-3 h-12 w-full text-base md:w-auto md:px-8" disabled={!text.trim() || busy} onClick={submit}>
          {busy ? <><Loader2 className="size-4 animate-spin" /> Reading…</> : "Add to trip"}
        </Button>

        <div role="status" aria-live="polite" className="mt-5">
          {!result && !busy && (
            <div className="flex gap-3 rounded-xl border border-dashed border-border p-4 text-sm text-muted-foreground">
              <Inbox className="size-4 shrink-0" /> Waiting for a booking or plan. Atlas shows you exactly what it understood before anything changes.
            </div>
          )}
          {result?.ok && (
            <div className="animate-rise rounded-xl border border-confirmed/30 bg-card p-4">
              <p className="flex items-center gap-2 font-medium"><Check className="size-4 text-confirmed" /> Atlas understood {result.bookings.length} {result.bookings.every((b) => b.isBooking === false) ? `plan${result.bookings.length === 1 ? "" : "s"}` : `booking${result.bookings.length === 1 ? "" : "s"}`}.</p>
              {result.bookings.map((b, i) => {
                const Icon = b.kind ? kindIcon[b.kind] : FileText;
                return (
                  <section key={i} className="mt-4 border-t border-border pt-3">
                    <p className="flex items-center gap-2 text-sm"><Icon className="size-4 shrink-0 text-muted-foreground" /><span className="font-display text-xl">{b.title ?? "Booking"}</span>{b.outcome === "updated" && <span className="text-xs font-medium text-changed">Updated</span>}</p>
                    {b.changes && b.changes.length > 0 && <ul className="mt-2 space-y-0.5 text-sm text-changed">{b.changes.map((c) => <li key={c}>{c}</li>)}</ul>}
                    {b.facts.length > 0 && (
                      <dl className="mt-2 divide-y divide-border text-sm">
                        {b.facts.map((f) => (
                          <div key={f.label} className="flex justify-between gap-4 py-2"><dt className="text-muted-foreground">{f.label}</dt><dd className="min-w-0 break-words text-right font-medium">{f.value}</dd></div>
                        ))}
                      </dl>
                    )}
                  </section>
                );
              })}
              <p className="mt-3 text-xs text-muted-foreground">"Only what you supplied. Bookings are never invented; stated plans stay tentative."</p>
              {result.state && <Link to="/" className="mt-3 inline-block text-sm font-medium text-primary underline-offset-2 hover:underline">See your trip</Link>}
            </div>
          )}
          {result && !result.ok && (
            <div className="animate-rise rounded-xl border border-border bg-secondary/60 p-4">
              <p className="font-medium">{result.message}</p>
              {result.detail && <p className="mt-1 text-sm text-muted-foreground">{result.detail}</p>}
              {result.reason === "unreachable" && <p className="mt-3 text-sm text-muted-foreground">Try again in a moment — your text is still here.</p>}
              {result.reason === "ended" && <Link to="/" className="mt-3 inline-block text-sm font-medium text-primary">Start again</Link>}
            </div>
          )}
        </div>

        <div className="mt-8 grid gap-2 sm:grid-cols-2">
          <div className="surface flex items-center gap-3 p-4 opacity-70"><FileUp className="size-5 shrink-0 text-muted-foreground" /><div><p className="text-sm font-medium">Upload a document</p><p className="text-xs text-muted-foreground">Coming soon</p></div></div>
          <div className="surface flex items-center gap-3 p-4 opacity-70"><Mail className="size-5 shrink-0 text-muted-foreground" /><div><p className="text-sm font-medium">Connect email</p><p className="text-xs text-muted-foreground">Coming soon</p></div></div>
        </div>

        <p className="mt-8 flex gap-2 text-sm text-muted-foreground"><ShieldCheck className="size-4 shrink-0" /> Atlas never invents a booking. A plan you state stays tentative until there is evidence to confirm it.</p>
      </div>
    </div>
  );
}
