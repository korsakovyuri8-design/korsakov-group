import { useState } from "react";
import { Link } from "@tanstack/react-router";
import { ChevronDown, CircleDashed, Clock, TriangleAlert } from "lucide-react";
import type { TripIssue } from "@/lib/atlas/domain";
import { Button } from "@/components/ui/button";
import { TransportAffiliateCta } from "@/components/atlas/TransportAffiliateCta";
import { cn } from "@/lib/utils";

const iconFor = (k: TripIssue["kind"]) => (k === "missing_transport" ? CircleDashed : k === "unknown_status" ? Clock : TriangleAlert);

export function IssueCard({ issue, defaultOpen = false }: { issue: TripIssue; defaultOpen?: boolean }) {
  const [open, setOpen] = useState(defaultOpen);
  const Icon = iconFor(issue.kind);
  const quiet = issue.kind === "unknown_status";
  return (
    <article className={cn("rounded-2xl border", quiet ? "border-border bg-card" : "border-attention/40 bg-attention-soft")}>
      <button onClick={() => setOpen((o) => !o)} aria-expanded={open} className="flex w-full items-start gap-3 p-4 text-left">
        <Icon className={cn("mt-0.5 size-4 shrink-0", quiet ? "text-muted-foreground" : "text-attention")} aria-hidden />
        <div className="min-w-0 flex-1">
          <h3 className="font-medium">{issue.title}</h3>
          {issue.lines?.length
            ? <div className="mt-0.5 space-y-0.5 text-sm text-muted-foreground">{issue.lines.map((l) => <p key={l}>{l}</p>)}</div>
            : <p className="mt-0.5 text-sm text-muted-foreground">{issue.what}</p>}
        </div>
        <ChevronDown className={cn("size-4 shrink-0 text-muted-foreground transition-transform", open && "rotate-180")} />
      </button>
      {open && (issue.why || issue.action || issue.kind === "missing_transport") && (
        <div className="animate-grow px-4 pb-4 pl-11">
          {issue.why && (
            <div className="border-t border-border/70 pt-3 text-sm">
              <p className="eyebrow">Why it matters</p>
              <p className="mt-1">{issue.why}</p>
            </div>
          )}
          {issue.action && (
            <Button size="sm" className="mt-4" asChild>
              <Link to="/booking/$id" params={{ id: issue.action.itemId }}>{issue.action.label}</Link>
            </Button>
          )}
          {issue.kind === "missing_transport" && (
            <TransportAffiliateCta />
          )}
        </div>
      )}
    </article>
  );
}
