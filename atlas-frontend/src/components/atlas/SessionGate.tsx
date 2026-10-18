import { useState, type ReactNode } from "react";
import { Link } from "@tanstack/react-router";
import { useQueryClient } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";
import { canStartSample, startAgain, startSample } from "@/lib/atlas/api";
import type { AtlasState } from "@/lib/atlas/domain";
import { Button } from "@/components/ui/button";

/** Calm states for a live session that has no trip yet, or has ended. */
export function SessionGate({ session, children }: { session: AtlasState["session"]; children: ReactNode }) {
  const qc = useQueryClient();
  const [busy, setBusy] = useState(false);
  if (session !== "none" && session !== "ended") return <>{children}</>;
  const ended = session === "ended";
  async function go() {
    setBusy(true);
    if (ended) await startAgain(qc);
    else await startSample(qc);
    setBusy(false);
  }
  return (
    <div className="max-w-2xl px-5 pt-10 md:px-10 md:pt-14">
      <p className="eyebrow">{ended ? "Session" : "Your trip"}</p>
      <h1 className="font-display mt-2 break-words text-[2.4rem] leading-[1.04] md:text-5xl">
        {ended ? "This Atlas session has ended." : <>Forward your bookings.<br /><em>Atlas handles the rest.</em></>}
      </h1>
      <p className="mt-3 text-muted-foreground">{ended ? "Nothing from it is kept here." : "Atlas has no bookings for this trip yet."}</p>
      <div className="mt-6 flex flex-wrap gap-2">
        {(ended || canStartSample) && (
          <Button disabled={busy} onClick={go}>{busy ? <Loader2 className="size-4 animate-spin" /> : ended ? "Start again" : "Use demo sample"}</Button>
        )}
        {!ended && <Button variant="outline" asChild><Link to="/import">Import a booking</Link></Button>}
      </div>
    </div>
  );
}
