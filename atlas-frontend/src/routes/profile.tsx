import { createFileRoute } from "@tanstack/react-router";
import { PageHeader } from "@/components/atlas/AppShell";

export const Route = createFileRoute("/profile")({
  head: () => ({
    meta: [
      { title: "Travel preferences — Korsakov Atlas" },
      { name: "description", content: "Pace, walking, food and interests that shape what Atlas suggests." },
      { property: "og:title", content: "Travel preferences — Korsakov Atlas" },
      { property: "og:description", content: "Tell Atlas how you like to travel." },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary" },
    ],
  }),
  component: Profile,
});

const prefs = [
  ["Walking", ["Up to 10 min", "Up to 20 min", "I love walking"]],
  ["Pace", ["Slow", "Balanced", "Full days"]],
  ["Interests", ["History", "Food", "Views", "Sea", "Nightlife"]],
  ["Food", ["Local", "Seafood", "Vegetarian"]],
] as const;

function Profile() {
  return (
    <div className="max-w-2xl">
      <PageHeader eyebrow="Preferences" title="How you like to travel.">These will shape what Atlas suggests in your free time. Not yet active in this beta.</PageHeader>
      <div className="mx-5 mt-8 space-y-7 md:mx-10">
        {prefs.map(([label, opts]) => (
          <fieldset key={label}>
            <legend className="eyebrow">{label}</legend>
            <div className="mt-2 flex flex-wrap gap-2">
              {opts.map((o) => (
                <label key={o} className="cursor-pointer rounded-full border border-border px-3.5 py-1.5 text-sm has-[:checked]:border-primary has-[:checked]:bg-primary has-[:checked]:text-primary-foreground">
                  <input type="checkbox" className="sr-only" />{o}
                </label>
              ))}
            </div>
          </fieldset>
        ))}
      </div>
    </div>
  );
}
