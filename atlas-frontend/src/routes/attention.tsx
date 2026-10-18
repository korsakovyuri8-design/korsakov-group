import { createFileRoute } from "@tanstack/react-router";
import { prefetchTrips } from "@/lib/atlas/scenario";
import { useAtlas } from "@/components/atlas/status";
import { PageHeader } from "@/components/atlas/AppShell";
import { IssueCard } from "@/components/atlas/IssueCard";

export const Route = createFileRoute("/attention")({
  head: () => ({
    meta: [
      { title: "Needs attention — Korsakov Atlas" },
      { name: "description", content: "Changes and gaps Atlas found in your trip — what happened, why it matters, what you can do." },
      { property: "og:title", content: "Needs attention — Korsakov Atlas" },
      { property: "og:description", content: "What happened, why it matters, what you can do." },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary_large_image" },
    ],
  }),
  loader: ({ context }) => prefetchTrips(context.queryClient),
  component: Attention,
});

function Attention() {
  const { trip } = useAtlas();
  return (
    <div className="max-w-2xl">
      <PageHeader eyebrow="Needs attention" title={trip.issues.length === 0 ? "Nothing needs attention." : trip.issues.length === 1 ? "One thing to look at." : `${trip.issues.length} things to look at.`}>
        Atlas says “may no longer fit” until something is proven broken. Nothing is changed without you.
      </PageHeader>
      <div className="mx-5 mt-8 space-y-3 md:mx-10">
        {trip.issues.map((i, n) => <IssueCard key={i.id} issue={i} defaultOpen={n === 0} />)}
      </div>
    </div>
  );
}
