import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  BedDouble,
  CarFront,
  CircleHelp,
  MapPin,
  Users,
  type LucideIcon,
} from "lucide-react";
import type { PlanningNeed, PlanPlaceRole } from "@/lib/atlas/domain";
import {
  answerArrivalTime,
  answerPlanDate,
  answerOrigin,
  answerPartySize,
  answerPlaceRole,
  answerStayIntent,
  canAnswerPlaceRole,
} from "@/lib/atlas/api";

type Copy = {
  title: string;
  body: string;
  Icon: LucideIcon;
};

const COPY: Record<string, Copy> = {
  date_unknown: {
    title: "When are you going?",
    body: "Atlas knows where you are going, but it will not guess the travel date.",
    Icon: CircleHelp,
  },
  place_role_unknown: {
    title: "What is this place for your trip?",
    body: "Atlas knows the place, but not whether it is the event venue, your stay, or both.",
    Icon: CircleHelp,
  },
  overnight_stay_unknown: {
    title: "Will you stay overnight?",
    body: "The plan spans more than one day, but Atlas will not assume you need accommodation.",
    Icon: BedDouble,
  },
  accommodation_unconfirmed: {
    title: "Stay is not confirmed",
    body: "You plan to stay overnight, but Atlas has no confirmed accommodation covering these dates.",
    Icon: BedDouble,
  },
  arrival_origin_unknown: {
    title: "Where will you be coming from?",
    body: "Atlas needs an origin before it can understand how you reach this plan.",
    Icon: MapPin,
  },
  arrival_origin_unresolved: {
    title: "Atlas can't match your origin yet",
    body: "There is an origin in this plan, but Atlas cannot match it to a known place. Enter the city or place again before route planning continues.",
    Icon: MapPin,
  },
  arrival_time_unknown: {
    title: "What time do you need to be there?",
    body: "Atlas knows where you are coming from and where you are going. Add the arrival deadline before it calculates transport.",
    Icon: CarFront,
  },
  transport_party_size_unknown: {
    title: "How many people are travelling?",
    body: "Atlas needs the party size before it can look for a transfer.",
    Icon: Users,
  },
  arrival_transport_unconfirmed: {
    title: "Transport is not confirmed",
    body: "Atlas knows your origin and destination, but has no confirmed transfer connecting them.",
    Icon: CarFront,
  },
};

const ROLES: { value: PlanPlaceRole; label: string }[] = [
  { value: "venue", label: "Event venue" },
  { value: "stay", label: "My stay" },
  { value: "both", label: "Both" },
];

function copyFor(need: PlanningNeed): Copy {
  return COPY[need.code] ?? {
    title: "More planning information is needed",
    body: "Atlas needs another confirmed fact before it can close this part of the plan.",
    Icon: CircleHelp,
  };
}

export function PlanningNeeds({
  needs,
}: {
  needs?: PlanningNeed[] | undefined;
}) {
  const qc = useQueryClient();

  const [busy, setBusy] = useState<string | null>(null);
  const [origin, setOrigin] = useState("");
  const [planDate, setPlanDate] = useState("");
  const [arrivalTime, setArrivalTime] = useState("");
  const [partySize, setPartySize] = useState("");
  const [error, setError] = useState<string | null>(null);

  if (!needs?.length) return null;

  async function run(key: string, fn: () => Promise<unknown>) {
    setBusy(key);
    setError(null);

    try {
      await fn();
    } catch {
      setError("Atlas couldn't save that answer. Try again.");
    } finally {
      setBusy(null);
    }
  }

  return (
    <section className="mx-5 mt-9 md:mx-10">
      <div className="mb-3 flex items-baseline justify-between">
        <h2 className="eyebrow">
          Needs planning · {needs.length}
        </h2>
      </div>

      <div className="surface divide-y divide-border">
        {needs.map((need) => {
          const { title, body, Icon } = copyFor(need);

          return (
            <div key={need.id} className="flex gap-3 p-4">
              <div className="mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-full bg-secondary">
                <Icon className="size-4 text-muted-foreground" />
              </div>

              <div className="min-w-0 flex-1">
                <p className="text-sm font-medium">{title}</p>

                <p className="mt-1 text-sm leading-relaxed text-muted-foreground">
                  {body}
                </p>

                {need.code === "date_unknown" &&
                  need.planKey && (
                    <div className="mt-3 space-y-2">
                      <div className="flex flex-wrap gap-2">
                        <button
                          type="button"
                          disabled={busy !== null}
                          onClick={() =>
                            void run("date-tomorrow", () =>
                              answerPlanDate(
                                qc,
                                need.planKey!,
                                "tomorrow",
                              ),
                            )
                          }
                          className="rounded-full border border-border px-3 py-1.5 text-xs font-medium"
                        >
                          Tomorrow
                        </button>

                        <button
                          type="button"
                          disabled={busy !== null}
                          onClick={() =>
                            void run("date-weekend", () =>
                              answerPlanDate(
                                qc,
                                need.planKey!,
                                "this_weekend",
                              ),
                            )
                          }
                          className="rounded-full border border-border px-3 py-1.5 text-xs font-medium"
                        >
                          This weekend
                        </button>
                      </div>

                      <div className="flex gap-2">
                        <input
                          value={planDate}
                          onChange={(e) =>
                            setPlanDate(e.target.value)
                          }
                          placeholder="30-31 October"
                          aria-label="Trip date"
                          className="min-w-0 flex-1 rounded-xl border border-border bg-background px-3 py-2 text-sm"
                        />

                        <button
                          type="button"
                          disabled={
                            !planDate.trim() ||
                            busy !== null
                          }
                          onClick={() =>
                            void run("date-custom", () =>
                              answerPlanDate(
                                qc,
                                need.planKey!,
                                planDate.trim(),
                              ),
                            )
                          }
                          className="rounded-xl bg-foreground px-4 py-2 text-xs font-medium text-background disabled:opacity-40"
                        >
                          Save
                        </button>
                      </div>
                    </div>
                  )}

                {need.code === "place_role_unknown" &&
                  need.planKey &&
                  canAnswerPlaceRole && (
                    <div className="mt-3 flex flex-wrap gap-2">
                      {ROLES.map((choice) => (
                        <button
                          key={choice.value}
                          disabled={busy !== null}
                          onClick={() =>
                            void run(choice.value, () =>
                              answerPlaceRole(
                                qc,
                                need.planKey!,
                                choice.value,
                              ),
                            )
                          }
                          className="rounded-full border border-border px-3 py-1.5 text-xs font-medium"
                        >
                          {choice.label}
                        </button>
                      ))}
                    </div>
                  )}

                {need.code === "overnight_stay_unknown" &&
                  need.planKey && (
                    <div className="mt-3 flex gap-2">
                      <button
                        disabled={busy !== null}
                        onClick={() =>
                          void run("overnight-yes", () =>
                            answerStayIntent(qc, need.planKey!, true),
                          )
                        }
                        className="rounded-full border border-border px-3 py-1.5 text-xs font-medium"
                      >
                        Yes, overnight
                      </button>

                      <button
                        disabled={busy !== null}
                        onClick={() =>
                          void run("overnight-no", () =>
                            answerStayIntent(qc, need.planKey!, false),
                          )
                        }
                        className="rounded-full border border-border px-3 py-1.5 text-xs font-medium"
                      >
                        No overnight
                      </button>
                    </div>
                  )}

                {(need.code === "arrival_origin_unknown" ||
                  need.code === "arrival_origin_unresolved") &&
                  need.planKey && (
                    <div className="mt-3 flex gap-2">
                      <input
                        value={origin}
                        onChange={(e) => setOrigin(e.target.value)}
                        placeholder="e.g. Podgorica"
                        className="min-w-0 flex-1 rounded-xl border border-border bg-background px-3 py-2 text-sm"
                      />

                      <button
                        disabled={!origin.trim() || busy !== null}
                        onClick={() =>
                          void run("origin", () =>
                            answerOrigin(
                              qc,
                              need.planKey!,
                              origin.trim(),
                            ),
                          )
                        }
                        className="rounded-xl bg-foreground px-4 py-2 text-xs font-medium text-background disabled:opacity-40"
                      >
                        {need.code === "arrival_origin_unresolved"
                          ? "Update"
                          : "Save"}
                      </button>
                    </div>
                  )}

                {need.code === "transport_party_size_unknown" &&
                  need.planKey && (
                    <div className="mt-3 flex gap-2">
                      <input
                        type="text"
                        inputMode="numeric"
                        enterKeyHint="done"
                        placeholder="1"
                        maxLength={2}
                        value={partySize}
                        onChange={(e) =>
                          setPartySize(
                            e.target.value.replace(/\D/g, "").slice(0, 2),
                          )
                        }
                        aria-label="Number of travellers"
                        className="w-20 rounded-xl border border-border bg-background px-3 py-2 text-sm"
                      />

                      <button
                        type="button"
                        disabled={
                          !partySize ||
                          Number(partySize) < 1 ||
                          Number(partySize) > 20 ||
                          busy !== null
                        }
                        onClick={() =>
                          void run("party-size", () =>
                            answerPartySize(
                              qc,
                              need.planKey!,
                              Number(partySize),
                            ),
                          )
                        }
                        className="rounded-xl bg-foreground px-4 py-2 text-xs font-medium text-background disabled:opacity-40"
                      >
                        Save
                      </button>
                    </div>
                  )}

                {need.code === "arrival_time_unknown" &&
                  need.planKey && (
                    <div className="mt-3 flex gap-2">
                      <input
                        type="text"
                        inputMode="numeric"
                        enterKeyHint="done"
                        autoComplete="off"
                        placeholder="09:00"
                        maxLength={5}
                        value={arrivalTime}
                        onChange={(e) => {
                          const digits = e.target.value.replace(/\D/g, "").slice(0, 4);
                          const formatted =
                            digits.length > 2
                              ? `${digits.slice(0, 2)}:${digits.slice(2)}`
                              : digits;
                          setArrivalTime(formatted);
                        }}
                        aria-label="Arrival time"
                        className="w-24 rounded-xl border border-border bg-background px-3 py-2 text-sm"
                      />

                      <button
                        type="button"
                        disabled={!/^(?:[01]\d|2[0-3]):[0-5]\d$/.test(arrivalTime) || busy !== null}
                        onClick={() =>
                          void run("arrival-time", () =>
                            answerArrivalTime(
                              qc,
                              need.planKey!,
                              arrivalTime,
                            ),
                          )
                        }
                        className="rounded-xl bg-foreground px-4 py-2 text-xs font-medium text-background disabled:opacity-40"
                      >
                        Save
                      </button>
                    </div>
                  )}

              </div>
            </div>
          );
        })}
      </div>

      {error && (
        <p className="mt-2 text-xs text-attention">
          {error}
        </p>
      )}

      <p className="mt-2 text-xs text-muted-foreground">
        Atlas asks before turning assumptions into planning requirements.
      </p>
    </section>
  );
}
