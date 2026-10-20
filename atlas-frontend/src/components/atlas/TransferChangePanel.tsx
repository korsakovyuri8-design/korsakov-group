import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  answerPartySize,
  canConfirmTransfer,
  canGetActiveTransferQuote,
  canGetTransferQuote,
  confirmTransfer,
  getActiveTransferQuote,
  getTransferQuote,
} from "@/lib/atlas/api";
import type {
  ConfirmedTransport,
  MobilityQuote,
  TransportChange,
} from "@/lib/atlas/domain";

interface Props {
  planKey?: string | undefined;
  confirmed: ConfirmedTransport;
  needsGap: boolean;
  change?: TransportChange | undefined;
}

export function TransferChangePanel({
  planKey,
  confirmed,
  needsGap,
  change,
}: Props) {
  const qc = useQueryClient();
  const inFlight = useRef(false);

  const [expanded, setExpanded] = useState(false);
  const [partySize, setPartySize] = useState("");
  const [quote, setQuote] = useState<MobilityQuote | null>(null);
  const [consent, setConsent] = useState(false);
  const [busy, setBusy] = useState<"quote" | "confirm" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [needsVerification, setNeedsVerification] = useState(false);
  const [expectedReference, setExpectedReference] = useState<string | null>(null);

  // Restore an offered replacement after refresh.
  // Reading it never confirms or cancels a booking.
  useEffect(() => {
    let active = true;

    if (
      !planKey ||
      !needsGap ||
      !confirmed.sandbox ||
      !canGetActiveTransferQuote ||
      change?.status === "needs_attention"
    ) {
      return () => { active = false; };
    }

    void getActiveTransferQuote(planKey)
      .then((restored) => {
        if (
          !active || !restored || !restored.sandbox ||
          restored.bookingCreated || restored.transactionCreated
        ) return;

        setPartySize(String(restored.request["party_size"] ?? ""));
        setQuote(restored);
        setConsent(false);
        setExpanded(true);
      })
      .catch(() => {
        if (active) {
          setError(
            "Atlas could not restore the replacement offer. " +
            "Your existing booking has not been changed by this request.",
          );
        }
      });

    return () => { active = false; };
  }, [planKey, needsGap, confirmed.sandbox, change?.status]);

  const verifiedReplacement =
    needsVerification &&
    change?.status === "completed" &&
    !!expectedReference &&
    confirmed.providerReference === expectedReference;

  const unresolvedOutcome = needsVerification && !verifiedReplacement;

  // Change is currently enabled for sandbox transactions only.
  if (
    !planKey ||
    !confirmed.sandbox ||
    confirmed.status !== "accepted" ||
    !canGetTransferQuote ||
    !canConfirmTransfer ||
    change?.status === "needs_attention"
  ) return null;

  const enteredParty = Number(partySize);
  const validParty =
    /^\d{1,2}$/.test(partySize) &&
    Number.isInteger(enteredParty) &&
    enteredParty >= 1 &&
    enteredParty <= 20 &&
    (
      needsGap ||
      confirmed.partySize === undefined ||
      enteredParty !== confirmed.partySize
    );

  function openPanel() {
    setExpanded(true);
    setPartySize(String(confirmed.partySize ?? ""));
    setQuote(null);
    setConsent(false);
    setError(null);
  }

  function closePanel() {
    setExpanded(false);
    setQuote(null);
    setConsent(false);
    setError(null);
  }

  async function requestReplacementQuote() {
    if (!planKey || !validParty || busy || inFlight.current || unresolvedOutcome) return;

    inFlight.current = true;
    setBusy("quote");
    setError(null);
    setConsent(false);

    try {
      const updated = await answerPartySize(qc, planKey, enteredParty);
      if (!updated) throw new Error("party_size_not_saved");

      const offered = await getTransferQuote(planKey);

      if (
        !offered || !offered.sandbox ||
        offered.bookingCreated || offered.transactionCreated ||
        offered.request["party_size"] !== enteredParty
      ) throw new Error("replacement_quote_not_verified");

      setExpectedReference(null);
      setNeedsVerification(false);
      setQuote(offered);
    } catch {
      setQuote(null);
      setError(
        "Atlas could not prepare a replacement offer. " +
        "The existing transfer has not been cancelled.",
      );
    } finally {
      inFlight.current = false;
      setBusy(null);
    }
  }

  async function confirmReplacement() {
    if (
      !planKey || !quote || !consent || busy ||
      inFlight.current || unresolvedOutcome
    ) return;

    inFlight.current = true;
    setBusy("confirm");
    setError(null);

    try {
      const result = await confirmTransfer(qc, planKey, quote.id);
      setNeedsVerification(true);

      if (!result || result.status !== "accepted") {
        setExpectedReference(null);
        setError(
          "The new transfer has not been confirmed. " +
          "Check both booking statuses before trying again.",
        );
        return;
      }

      setExpectedReference(result.providerReference ?? null);
      setQuote(null);
      setConsent(false);
      setExpanded(false);

      // The backend transport_change projection must prove
      // that the old booking was actually cancelled.
    } catch {
      setExpectedReference(null);
      setNeedsVerification(true);
      setError(
        "Replacement outcome could not be verified. " +
        "The original booking may still be active. " +
        "Refresh the trip before taking another action.",
      );
    } finally {
      inFlight.current = false;
      setBusy(null);
    }
  }

  return (
    <div className="mt-5 border-t border-border pt-4">
      {needsGap && (
        <p role="status" className="mb-4 text-sm text-muted-foreground">
          Your updated trip is not covered by the existing transfer.
          The previous booking remains active until a replacement is accepted.
        </p>
      )}

      {unresolvedOutcome ? (
        <div role="alert" className="rounded-xl border border-attention/40 p-4 text-sm">
          {error ??
            "The replacement was submitted, but the previous cancellation " +
            "has not yet been verified. Check your trip before changing it again."}
        </div>
      ) : !expanded ? (
        <button
          type="button"
          onClick={openPanel}
          className="rounded-xl border border-border px-4 py-2 text-sm font-medium"
        >
          Change transfer
        </button>
      ) : (
        <div className="space-y-4">
          <div>
            <p className="font-medium">Change transfer</p>
            <p className="mt-1 text-sm text-muted-foreground">
              A new quote does not cancel your current booking. Atlas will
              request cancellation only after the replacement is accepted.
              If cancellation fails, both bookings may remain active.
            </p>
          </div>

          {!quote ? (
            <div className="space-y-2">
              <label htmlFor="atlas-replacement-party" className="block text-sm font-medium">
                Travellers for replacement
              </label>
              <input
                id="atlas-replacement-party"
                type="text"
                inputMode="numeric"
                maxLength={2}
                value={partySize}
                disabled={busy !== null}
                onChange={(event) => {
                  setPartySize(event.target.value.replace(/\D/g, "").slice(0, 2));
                  setError(null);
                }}
                className="w-24 rounded-xl border border-border bg-background px-3 py-2 text-sm"
              />
              <p className="text-xs text-muted-foreground">
                Current booking: {confirmed.partySize ?? "unknown"} traveller(s)
              </p>
              <button
                type="button"
                disabled={!validParty || busy !== null}
                onClick={() => void requestReplacementQuote()}
                className="block rounded-xl bg-foreground px-4 py-2 text-sm font-medium text-background disabled:opacity-40"
              >
                {busy === "quote" ? "Getting replacement quote…" : "Get replacement quote"}
              </button>
            </div>
          ) : (
            <div className="space-y-3">
              <div className="rounded-xl border border-border p-4">
                <p className="eyebrow">New quote · not booked</p>
                <p className="mt-1 font-display text-2xl">
                  {quote.amount} {quote.currency}
                </p>
                <p className="text-sm text-muted-foreground">{quote.provider}</p>
                <p className="mt-2 text-sm">
                  {String(quote.request["party_size"] ?? "—")} travellers
                  {quote.sandbox ? " · Sandbox only" : ""}
                </p>
                {quote.conditions && (
                  <p className="mt-2 text-xs text-muted-foreground">
                    {quote.conditions}
                  </p>
                )}
              </div>

              <label className="flex items-start gap-3 text-sm">
                <input
                  type="checkbox"
                  aria-label="I consent to replacing this transfer"
                  checked={consent}
                  disabled={busy !== null}
                  onChange={(event) => setConsent(event.target.checked)}
                  className="mt-1"
                />
                <span>
                  I authorize this new booking and understand that cancellation
                  of the previous booking may fail.
                </span>
              </label>

              <button
                type="button"
                disabled={!consent || busy !== null}
                onClick={() => void confirmReplacement()}
                className="rounded-xl bg-foreground px-4 py-2 text-sm font-medium text-background disabled:opacity-40"
              >
                {busy === "confirm" ? "Confirming replacement…" : "Confirm replacement"}
              </button>

              <button
                type="button"
                disabled={busy !== null}
                onClick={() => { setQuote(null); setConsent(false); }}
                className="block text-sm underline underline-offset-4 disabled:opacity-40"
              >
                Edit travellers
              </button>
            </div>
          )}

          {error && <p role="alert" className="text-sm text-attention">{error}</p>}

          <button
            type="button"
            disabled={busy !== null}
            onClick={closePanel}
            className="text-sm text-muted-foreground underline underline-offset-4 disabled:opacity-40"
          >
            Close change
          </button>
        </div>
      )}
    </div>
  );
}
