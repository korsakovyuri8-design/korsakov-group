import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";
import { importBooking } from "@/lib/atlas/api";
import type { NextStep } from "@/lib/atlas/domain";
import { Button } from "@/components/ui/button";

/** Next steps offered by Atlas. Each one is sent to the backend; the returned trip replaces the view. */
export function NextSteps({ steps }: { steps: NextStep[] }) {
  const qc = useQueryClient();
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  async function run(id: string) {
    setBusy(id);
    setError(null);
    const r = await importBooking(qc, { sample: id });
    if (!r.ok && r.reason !== "ended") setError(`${r.message} ${r.detail ?? ""}`.trim());
    setBusy(null);
  }
  return (
    <section className="mx-5 mt-6 space-y-2 md:mx-10" aria-label="Next steps">
      {steps.map((s) => (
        <div key={s.id} className="surface flex flex-col gap-3 p-4 sm:flex-row sm:items-center sm:justify-between">
          {s.hint && <p className="text-sm text-muted-foreground">{s.hint}</p>}
          <Button className="shrink-0" disabled={!!busy} onClick={() => run(s.id)}>
            {busy === s.id ? <><Loader2 className="size-4 animate-spin" /> Atlas is updating…</> : s.label}
          </Button>
        </div>
      ))}
      {error && <p role="status" className="text-sm text-muted-foreground">{error}</p>}
    </section>
  );
}
