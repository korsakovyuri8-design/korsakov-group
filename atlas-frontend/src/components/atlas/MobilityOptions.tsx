import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { TransferChangePanel } from "./TransferChangePanel";
import { RevenuePanel } from "./RevenuePanel";

import {
  answerPartySize,
  canCompareMobility,
  canConfirmTransfer,
  canGetActiveTransferQuote,
  canGetTransferQuote,
  compareMobility,
  canReviewTransferCancellation,
  getTransferCancellation,
  canCancelTransfer,
  cancelTransfer,
  getActiveTransferQuote,
  confirmTransfer,
  getTransferQuote,
} from "@/lib/atlas/api";

import type {
  ConfirmedTransport,
  MobilityCancellation,
  MobilityComparison,
  MobilityMode,
  MobilityQuote,
  PlanningNeed,
  RoutePlan,
  TransportChange,
} from "@/lib/atlas/domain";

interface Props {
  planKey?: string | undefined;
  route?: RoutePlan | undefined;
  needs?: PlanningNeed[] | undefined;
  confirmedTransport?: ConfirmedTransport | undefined;
  transportChange?: TransportChange | undefined;
}

const LABELS: Record<MobilityMode, string> = {
  private_transfer: "Private transfer",
  scheduled_bus: "Bus",
  rental_car: "Rental car",
  rail: "Rail",
};

function hm(value: unknown) {
  if (typeof value !== "string") return "—";
  return value.length >= 16 ? value.slice(11, 16) : value;
}

function statusText(
  status: string,
  reason: string,
) {
  if (status === "available") {
    return "Available";
  }

  if (status === "needs_details") {
    return "More details required";
  }

  if (reason === "not_offered") {
    return "No connected provider yet";
  }

  return "Not available through Atlas yet";
}

export function MobilityOptions({
  route,
  needs,
  confirmedTransport,
  transportChange,
  planKey: fallbackPlanKey,
}: Props) {
  const qc = useQueryClient();
  const transportNeed = needs?.find(
    (n) => n.code === "arrival_transport_unconfirmed",
  );

  const planKey = transportNeed?.planKey ?? fallbackPlanKey;

  const [comparison, setComparison] =
    useState<MobilityComparison | null>(null);

  const [quote, setQuote] =
    useState<MobilityQuote | null>(null);

  const [partySize, setPartySize] =
    useState("");

  const [cancellation, setCancellation] =
    useState<MobilityCancellation | null>(null);

  const [cancelConsent, setCancelConsent] = useState(false);

  const [cancelNotice, setCancelNotice] =
    useState<"cancelled" | "pending" | "needs_attention" | null>(null);

  const cancelInFlight = useRef(false);

  const [busy, setBusy] =
    useState<"compare" | "quote" | "confirm" | "cancellation" | "cancel" | null>(null);

  const [error, setError] =
    useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    setComparison(null);
    setQuote(null);
    setCancellation(null);
    setError(null);

    if (
      !planKey ||
      !route ||
      confirmedTransport ||
      !canGetActiveTransferQuote
    ) {
      return () => {
        cancelled = true;
      };
    }

    void getActiveTransferQuote(planKey)
      .then((restored) => {
        if (!cancelled && restored) {
          setQuote(restored);
        }
      })
      .catch((e) => {
        if (!cancelled) {
          setError(
            e instanceof Error
              ? e.message
              : "Atlas couldn't restore the current transfer offer.",
          );
        }
      });

    return () => {
      cancelled = true;
    };
  }, [
    planKey,
    route?.generatedAt,
    confirmedTransport,
  ]);

  // The latest provider-backed trip state is authoritative.
  // Never retain an older confirmation after a replacement.
  const confirmed = cancelNotice === "cancelled"
    ? undefined
    : confirmedTransport;

  const previousConfirmedReference = useRef(
    confirmedTransport?.providerReference,
  );

  useEffect(() => {
    const reference = confirmedTransport?.providerReference;
    const newlyConfirmed =
      confirmedTransport?.status === "accepted" ||
      confirmedTransport?.status === "in_progress" ||
      confirmedTransport?.status === "completed";

    if (
      cancelNotice !== null &&
      newlyConfirmed &&
      reference &&
      reference !== previousConfirmedReference.current
    ) {
      // A later confirmed booking supersedes the local notice
      // for the previously cancelled transaction.
      setCancelNotice(null);
      setCancelConsent(false);
      setCancellation(null);
    }

    previousConfirmedReference.current = reference;
  }, [
    confirmedTransport?.providerReference,
    confirmedTransport?.status,
    cancelNotice,
  ]);

  if (
    !route ||
    (
      !confirmed &&
      (
        !planKey ||
        !canCompareMobility
      )
    )
  ) {
    return null;
  }

  async function compare() {
    if (!planKey) return;

    setBusy("compare");
    setError(null);

    try {
      const result = await compareMobility(planKey);

      if (result) {
        setComparison(result);
        setQuote(null);
      }
    } catch {
      setError(
        "Atlas couldn't compare transport options right now.",
      );
    } finally {
      setBusy(null);
    }
  }

  async function quoteTransfer() {
    if (
      !planKey ||
      !canGetTransferQuote
    ) {
      return;
    }

    const knownPartySize =
      comparison?.request.partySize;

    const enteredPartySize =
      Number(partySize);

    const effectivePartySize =
      knownPartySize ??
      (
        Number.isInteger(enteredPartySize)
          ? enteredPartySize
          : undefined
      );

    if (
      !effectivePartySize ||
      effectivePartySize < 1 ||
      effectivePartySize > 20
    ) {
      setError(
        "Choose between 1 and 20 travellers before getting a quote.",
      );
      return;
    }

    setBusy("quote");
    setError(null);

    try {
      if (knownPartySize === undefined) {
        await answerPartySize(
          qc,
          planKey,
          effectivePartySize,
        );

        setComparison((current) =>
          current
            ? {
                ...current,
                request: {
                  ...current.request,
                  partySize: effectivePartySize,
                },
              }
            : current,
        );
      }

      const result =
        await getTransferQuote(planKey);

      if (result) {
        setQuote(result);
      }
    } catch {
      setError(
        "Atlas couldn't get a transfer quote right now.",
      );
    } finally {
      setBusy(null);
    }
  }

  async function confirmQuote() {
    if (
      !planKey ||
      !quote ||
      !canConfirmTransfer
    ) {
      return;
    }

    setBusy("confirm");
    setError(null);

    try {
      const result = await confirmTransfer(
        qc,
        planKey,
        quote.id,
      );

      if (
        !result ||
        !["accepted", "in_progress", "completed"].includes(result.status)
      ) {
        setError(
          "Atlas has not verified confirmation of this transfer. " +
          "Check the current booking status before trying again.",
        );
      }
    } catch {
      setError(
        "Atlas couldn't confirm this transfer offer.",
      );
    } finally {
      setBusy(null);
    }
  }




  async function reviewCancellation() {
    if (!planKey || !canReviewTransferCancellation) return;

    setBusy("cancellation");
    setError(null);

    try {
      const preview = await getTransferCancellation(planKey);

      if (preview?.transactionId) {
        setCancelConsent(false);
        setCancellation(preview);
      } else {
        setError("No cancellation information is available.");
      }
    } catch {
      setError(
        "Atlas couldn't load the cancellation terms. No changes were made.",
      );
    } finally {
      setBusy(null);
    }
  }

  async function submitCancellation() {
    if (
      !planKey ||
      !cancellation ||
      !cancellation.cancellable ||
      !cancelConsent ||
      !canCancelTransfer ||
      busy !== null ||
      cancelInFlight.current ||
      cancelNotice !== null
    ) return;

    cancelInFlight.current = true;
    setBusy("cancel");
    setError(null);

    try {
      const result = await cancelTransfer(
        qc,
        planKey,
        cancellation.transactionId,
      );

      if (!result || result.pending ||
          result.transaction.status !== "cancelled") {
        setCancelNotice("pending");
      } else {
        // The backend confirmed cancellation.
        // Never retain a stale locally confirmed transaction.
        setQuote(null);
        setComparison(null);
        setCancellation(null);
        setCancelNotice("cancelled");
      }
    } catch {
      // Includes refusal, timeout and uncertain network outcomes.
      // Do not change the local transaction to CANCELLED.
      setCancelNotice("needs_attention");
      setError(
        "Cancellation was not confirmed. The transfer may still be active. " +
        "Check its current status before making another booking.",
      );
    } finally {
      cancelInFlight.current = false;
      setCancelConsent(false);
      setBusy(null);
    }
  }

  if (confirmed) {
    return (
      <section className="mx-5 mt-9 md:mx-10">
        <h2 className="eyebrow mb-3">
          Transport
        </h2>

        <div className="surface p-5">
          {transportChange?.status === "needs_attention" && (
            <div role="alert" className="mb-5 rounded-xl border border-attention/40 bg-attention-soft p-4">
              <p className="font-semibold">
                Booking change needs attention
              </p>
              <p className="mt-2 text-sm">
                Atlas cannot verify cancellation of your previous
                transfer. Both bookings may still be active.
                Do not assume the old booking was cancelled.
              </p>
              <p className="mt-2 text-xs">
                Previous: {transportChange.oldTransactionStatus ?? "unverified"}
                {" · "}
                Current: {transportChange.newTransactionStatus ?? "unverified"}
              </p>
            </div>
          )}

          {transportChange?.status === "completed" && (
            <p role="status" className="mb-5 rounded-xl bg-secondary p-3 text-sm">
              Replacement completed. The previous transfer was
              cancelled according to the provider records.
            </p>
          )}

          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <p className="font-display text-2xl">
                Transfer confirmed
              </p>

              <p className="mt-1 text-sm text-muted-foreground">
                {confirmed.provider}
              </p>
            </div>

            <span className="rounded-full bg-secondary px-3 py-1 text-xs">
              Accepted
              {confirmed.sandbox
                ? " · Sandbox"
                : ""}
            </span>
          </div>

          <div className="mt-5 grid grid-cols-2 gap-4 border-y border-border py-4">
            <div>
              <p className="eyebrow">
                Price
              </p>
              <p className="mt-1 text-lg">
                {confirmed.amount ?? "—"}{" "}
                {confirmed.currency ?? ""}
              </p>
            </div>

            <div>
              <p className="eyebrow">
                Reference
              </p>
              <p className="mt-1 break-all text-sm">
                {confirmed.providerReference ??
                  "—"}
              </p>
            </div>
          </div>

          {confirmed.sandbox && (
            <div className="mt-4 rounded-xl bg-secondary px-3 py-3 text-sm">
              <strong>
                Sandbox confirmation.
              </strong>{" "}
              The transaction pipeline completed,
              but no real vehicle was ordered.
            </div>
          )}

          <p className="mt-4 text-xs text-muted-foreground">
            Provider status:{" "}
            {confirmed.status}.
          </p>

          {!cancellation && cancelNotice === null && (
            <TransferChangePanel
              planKey={planKey}
              confirmed={confirmed}
              needsGap={Boolean(transportNeed)}
              change={transportChange}
            />
          )}

          {cancelNotice === "pending" && (
            <p role="status" className="mt-4 text-sm text-muted-foreground">
              Cancellation requested; awaiting provider confirmation.
              Your transfer is not yet confirmed as cancelled.
            </p>
          )}

          {canReviewTransferCancellation &&
           planKey &&
           cancelNotice === null && (
            <div className="mt-5 border-t border-border pt-4">
              {!cancellation ? (
                <button
                  type="button"
                  disabled={busy !== null}
                  onClick={() => void reviewCancellation()}
                  className="rounded-xl border border-border px-4 py-2 text-sm font-medium disabled:opacity-50"
                >
                  {busy === "cancellation"
                    ? "Loading terms…"
                    : "Review cancellation terms"}
                </button>
              ) : (
                <div className="space-y-3">
                  <p className="font-medium">
                    Cancellation terms
                  </p>

                  <p className="text-sm">
                    {cancellation.policy.status === "free"
                      ? "Provider policy: free cancellation."
                      : cancellation.policy.status === "fee"
                        ? "Provider policy: a cancellation fee may apply."
                        : "Cancellation cost is not confirmed."}
                  </p>

                  {cancellation.policy.deadline && (
                    <p className="text-xs text-muted-foreground">
                      Policy deadline: {cancellation.policy.deadline}
                    </p>
                  )}

                  {cancellation.policy.fee && (
                    <p className="text-xs text-muted-foreground">
                      Fee: {cancellation.policy.fee}
                    </p>
                  )}

                  <p className="text-xs text-muted-foreground">
                    This is a preview. Your transfer has not been cancelled.
                    {cancellation.sandbox
                      ? " This is a sandbox booking."
                      : ""}
                  </p>

                  {!cancellation.cancellable ? (
                    <p className="text-sm text-muted-foreground">
                      This transaction cannot be cancelled through Atlas
                      in its current status.
                    </p>
                  ) : canCancelTransfer ? (
                    <div className="space-y-3">
                      <label className="flex items-start gap-3 text-sm">
                        <input
                          type="checkbox"
                          aria-label="I consent to cancelling this transfer"
                          checked={cancelConsent}
                          disabled={busy !== null}
                          onChange={(e) => setCancelConsent(e.target.checked)}
                          className="mt-1"
                        />
                        <span>
                          I understand the displayed policy and explicitly
                          authorize cancellation of this transfer.
                        </span>
                      </label>

                      <button
                        type="button"
                        disabled={!cancelConsent || busy !== null}
                        onClick={() => void submitCancellation()}
                        className="rounded-xl border border-border px-4 py-2 text-sm font-medium disabled:opacity-40"
                      >
                        {busy === "cancel"
                          ? "Requesting cancellation…"
                          : "Confirm cancellation"}
                      </button>
                    </div>
                  ) : null}

                  <button
                    type="button"
                    disabled={busy !== null}
                    onClick={() => {
                      setCancellation(null);
                      setCancelConsent(false);
                    }}
                    className="text-sm font-medium underline underline-offset-4 disabled:opacity-40"
                  >
                    Close
                  </button>
                </div>
              )}
            </div>
          )}

          {error && (
            <p role="alert" className="mt-3 text-xs text-attention">
              {error}
            </p>
          )}
        </div>
      </section>
    );
  }


  return (
    <section className="mx-5 mt-9 md:mx-10">
      <h2 className="eyebrow mb-3">
        Transport options
      </h2>

      {cancelNotice === "cancelled" && (
        <p role="status" className="surface mb-4 p-4 text-sm">
          Transfer cancellation confirmed by the provider.
          No current transfer booking is being claimed.
        </p>
      )}

      {!comparison && !quote && (
        <div className="surface p-5">
          <p className="font-display text-2xl">
            Compare ways to get there.
          </p>

          <p className="mt-2 max-w-xl text-sm leading-relaxed text-muted-foreground">
            Atlas can compare connected transport providers
            against your route and arrival deadline.
            Comparing does not create a quote or a booking.
          </p>

          <button
            type="button"
            disabled={busy !== null}
            onClick={() => void compare()}
            className="mt-4 rounded-xl bg-foreground px-4 py-2 text-sm font-medium text-background disabled:opacity-50"
          >
            {busy === "compare"
              ? "Comparing…"
              : "Compare transport"}
          </button>
        </div>
      )}

      {quote && !comparison && (
        <div className="surface border border-border p-5">
          <p className="eyebrow">
            Private transfer offer
          </p>

          <div className="mt-2 flex flex-wrap items-baseline justify-between gap-3">
            <div>
              <p className="font-display text-3xl">
                {quote.amount} {quote.currency}
              </p>
              <p className="mt-1 text-sm text-muted-foreground">
                {quote.provider}
              </p>
            </div>

            <span className="rounded-full bg-secondary px-3 py-1 text-xs">
              Not booked
            </span>
          </div>

          <p className="mt-3 text-xs text-muted-foreground">
            This provider offer survived the page refresh.
            Confirming it requires an explicit action.
          </p>

          <button
            type="button"
            disabled={
              busy !== null ||
              !canConfirmTransfer
            }
            onClick={() => void confirmQuote()}
            className="mt-4 rounded-xl bg-foreground px-4 py-2 text-sm font-medium text-background disabled:opacity-50"
          >
            {busy === "confirm"
              ? "Confirming…"
              : `Confirm ${quote.amount} ${quote.currency}`}
          </button>
        </div>
      )}

      {comparison && (
        <div className="space-y-3">
          <div className="surface p-5">
            <div className="flex flex-wrap items-baseline justify-between gap-2">
              <div>
                <p className="text-sm text-muted-foreground">
                  {comparison.request.origin}
                  {" → "}
                  {comparison.request.destination}
                </p>

                <p className="mt-1 font-display text-2xl">
                  Arrive by{" "}
                  {hm(comparison.request.arriveByLocal)}
                </p>
              </div>

              <button
                type="button"
                disabled={busy !== null}
                onClick={() => void compare()}
                className="text-xs font-medium text-muted-foreground underline underline-offset-4"
              >
                Refresh
              </button>
            </div>
          </div>

          {comparison.modes.map((mode) => {
            const available =
              mode.status === "available";

            return (
              <div
                key={mode.mode}
                className="surface p-5"
              >
                <div className="flex items-start justify-between gap-4">
                  <div>
                    <p className="font-display text-xl">
                      {LABELS[mode.mode]}
                    </p>

                    <p className="mt-1 text-sm text-muted-foreground">
                      {statusText(
                        mode.status,
                        mode.reason,
                      )}
                    </p>
                  </div>

                  <span className="eyebrow">
                    {available
                      ? "Available"
                      : mode.status === "needs_details"
                        ? "Needs details"
                        : mode.reason === "not_offered"
                          ? "Not connected"
                          : "Unavailable"}
                  </span>
                </div>

                {mode.providers.map((provider) => (
                  <div
                    key={provider.id}
                    className="mt-4 rounded-xl bg-secondary px-3 py-3 text-sm"
                  >
                    <div className="font-medium">
                      {provider.name}
                    </div>

                    {provider.sandbox && (
                      <div className="mt-1 text-xs text-muted-foreground">
                        Sandbox provider · not a real transport company
                      </div>
                    )}
                  </div>
                ))}

                {mode.mode === "private_transfer" &&
                  available &&
                  !quote && (
                    comparison.request.partySize !== undefined
                      ? (
                        <button
                          type="button"
                          disabled={
                            busy !== null ||
                            !canGetTransferQuote
                          }
                          onClick={() =>
                            void quoteTransfer()
                          }
                          className="mt-4 rounded-xl bg-foreground px-4 py-2 text-sm font-medium text-background disabled:opacity-50"
                        >
                          {busy === "quote"
                            ? "Getting quote…"
                            : "Get quote"}
                        </button>
                      )
                      : (
                        <div className="mt-4">
                          <p className="text-sm font-medium">
                            How many travellers?
                          </p>

                          <p className="mt-1 text-xs text-muted-foreground">
                            Atlas only needs this now to price the transfer.
                          </p>

                          <div className="mt-3 flex gap-2">
                            <input
                              type="text"
                              inputMode="numeric"
                              enterKeyHint="done"
                              maxLength={2}
                              placeholder="1"
                              aria-label="Number of travellers for quote"
                              value={partySize}
                              onChange={(e) =>
                                setPartySize(
                                  e.target.value
                                    .replace(/\D/g, "")
                                    .slice(0, 2),
                                )
                              }
                              className="w-20 rounded-xl border border-border bg-background px-3 py-2 text-sm"
                            />

                            <button
                              type="button"
                              disabled={
                                !partySize ||
                                Number(partySize) < 1 ||
                                Number(partySize) > 20 ||
                                busy !== null ||
                                !canGetTransferQuote
                              }
                              onClick={() =>
                                void quoteTransfer()
                              }
                              className="rounded-xl bg-foreground px-4 py-2 text-sm font-medium text-background disabled:opacity-50"
                            >
                              {busy === "quote"
                                ? "Getting quote…"
                                : "Get quote"}
                            </button>
                          </div>
                        </div>
                      )
                  )}
              </div>
            );
          })}

          {quote && (
            <div className="surface border border-border p-5">
              <p className="eyebrow">
                Private transfer offer
              </p>

              <div className="mt-2 flex flex-wrap items-baseline justify-between gap-3">
                <div>
                  <p className="font-display text-3xl">
                    {quote.amount} {quote.currency}
                  </p>

                  <p className="mt-1 text-sm text-muted-foreground">
                    {quote.provider}
                  </p>
                </div>

                <span className="rounded-full bg-secondary px-3 py-1 text-xs">
                  Quote · not booked
                </span>
              </div>

              <div className="mt-5 grid grid-cols-2 gap-4 border-y border-border py-4 text-sm">
                <div>
                  <p className="eyebrow">
                    Pickup
                  </p>
                  <p className="mt-1">
                    {hm(
                      quote.request[
                        "pickup_time"
                      ],
                    )}
                  </p>
                </div>

                <div>
                  <p className="eyebrow">
                    Travellers
                  </p>
                  <p className="mt-1">
                    {String(
                      quote.request[
                        "party_size"
                      ] ?? "—",
                    )}
                  </p>
                </div>
              </div>

              {quote.conditions && (
                <p className="mt-4 text-xs leading-relaxed text-muted-foreground">
                  {quote.conditions}
                </p>
              )}

              {quote.sandbox && (
                <div className="mt-4 rounded-xl bg-secondary px-3 py-3 text-sm">
                  <strong>
                    Sandbox offer.
                  </strong>{" "}
                  No real vehicle will be booked.
                </div>
              )}

              <p className="mt-4 text-xs text-muted-foreground">
                Atlas has created a price quote only.
                No transaction or reservation exists yet.
              </p>

              <div className="mt-5 border-t border-border pt-5">
                <p className="text-xs leading-relaxed text-muted-foreground">
                  Confirming creates an Atlas transaction
                  and submits this exact quote to the
                  provider. This provider is currently
                  sandbox-only.
                </p>

                <button
                  type="button"
                  disabled={
                    busy !== null ||
                    !canConfirmTransfer
                  }
                  onClick={() =>
                    void confirmQuote()
                  }
                  className="mt-3 rounded-xl bg-foreground px-4 py-2 text-sm font-medium text-background disabled:opacity-50"
                >
                  {busy === "confirm"
                    ? "Confirming…"
                    : `Confirm ${quote.amount} ${quote.currency}`}
                </button>
              </div>
            </div>
          )}

        </div>
      )}

      <RevenuePanel />

      {error && (
        <p className="mt-3 text-xs text-attention">
          {error}
        </p>
      )}
    </section>
  );
}
