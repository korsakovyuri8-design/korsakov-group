// Atlas data boundary. The UI only talks to this module and never knows which provider is active.
// Selected by environment only. Production builds → ApiAtlasProvider (same-origin relative URLs,
// VITE_ATLAS_API_BASE to override). Dev server → DemoAtlasProvider (design preview).
// VITE_ATLAS_PROVIDER=api|demo overrides either default.
import { queryOptions, type QueryClient } from "@tanstack/react-query";
import type { AtlasProvider, AtlasState, ImportInput, ImportResult, TripStateKind } from "./domain";
import { DemoAtlasProvider } from "./providers/demo";
import { ApiAtlasProvider } from "./providers/api";

const choice = import.meta.env["VITE_ATLAS_PROVIDER"] ?? (import.meta.env.PROD ? "api" : "demo");
const provider: AtlasProvider = choice === "api" ? ApiAtlasProvider : DemoAtlasProvider;

export const scenarioOptions = provider.scenarios;
export const sampleImportText = provider.sampleImportText;
export const isLive = provider.live;
export const canStartSample = !!provider.startSample;

export const atlasQuery = (scenario?: TripStateKind) =>
  queryOptions({ queryKey: ["atlas", "state", scenario ?? "live"], queryFn: () => provider.getState(scenario) });

/** The returned trip replaces the UI state — nothing is mutated locally. */
const apply = (qc: QueryClient) => (r: ImportResult) => {
  if (r.ok && r.state) qc.setQueryData<AtlasState>(atlasQuery().queryKey, r.state);
  if (!r.ok && r.reason === "ended") void qc.invalidateQueries({ queryKey: atlasQuery().queryKey });
  return r;
};

export const importBooking = (qc: QueryClient, input: ImportInput) => provider.importBooking(input).then(apply(qc));
export const startSample = (qc: QueryClient) =>
  provider.startSample ? provider.startSample().then(apply(qc)) : Promise.resolve<ImportResult>({ ok: false, reason: "unsupported", message: "No sample available." });
export const startAgain = async (qc: QueryClient) => {
  await provider.reset?.();
  await qc.invalidateQueries({ queryKey: atlasQuery().queryKey });
};

export const answerPlanDate = async (
  qc: QueryClient,
  planKey: string,
  value: string,
) => {
  if (!provider.answerPlanDate) return undefined;

  const state = await provider.answerPlanDate(
    planKey,
    value,
  );

  qc.setQueryData<AtlasState>(
    atlasQuery().queryKey,
    state,
  );

  return state;
};


export const answerStayIntent = async (
  qc: QueryClient,
  planKey: string,
  value: boolean,
) => {
  if (!provider.answerStayIntent) return undefined;

  const state = await provider.answerStayIntent(planKey, value);
  qc.setQueryData<AtlasState>(atlasQuery().queryKey, state);
  return state;
};

export const answerOrigin = async (
  qc: QueryClient,
  planKey: string,
  origin: string,
) => {
  if (!provider.answerOrigin) return undefined;

  const state = await provider.answerOrigin(planKey, origin);
  qc.setQueryData<AtlasState>(atlasQuery().queryKey, state);
  return state;
};


export const canAnswerPlaceRole = !!provider.answerPlaceRole;


export const answerPlaceRole = async (
  qc: QueryClient,
  planKey: string,
  role: "venue" | "stay" | "both",
) => {
  if (!provider.answerPlaceRole) return undefined;

  const state = await provider.answerPlaceRole(planKey, role);
  qc.setQueryData<AtlasState>(atlasQuery().queryKey, state);
  return state;
};

export const canPlanRoute = !!provider.planRoute;

export const planRoute = async (
  qc: QueryClient,
  planKey: string,
) => {
  if (!provider.planRoute) return undefined;

  const state = await provider.planRoute(planKey);
  qc.setQueryData<AtlasState>(atlasQuery().queryKey, state);
  return state;
};


export const answerArrivalTime = async (
  qc: QueryClient,
  planKey: string,
  value: string,
) => {
  if (!provider.answerArrivalTime) return undefined;

  const state = await provider.answerArrivalTime(
    planKey,
    value,
  );

  qc.setQueryData<AtlasState>(
    atlasQuery().queryKey,
    state,
  );

  return state;
};


export const answerPartySize = async (
  qc: QueryClient,
  planKey: string,
  value: number,
) => {
  if (!provider.answerPartySize) return undefined;

  const state = await provider.answerPartySize(planKey, value);

  qc.setQueryData<AtlasState>(
    atlasQuery().queryKey,
    state,
  );

  return state;
};

export const canCompareMobility = !!provider.compareMobility;
export const canGetTransferQuote = !!provider.getTransferQuote;

export const compareMobility = async (
  planKey: string,
) => {
  if (!provider.compareMobility) return undefined;
  return provider.compareMobility(planKey);
};

export const canGetActiveTransferQuote =
  !!provider.getActiveTransferQuote;

export const getActiveTransferQuote = async (
  planKey: string,
) => {
  if (!provider.getActiveTransferQuote) return undefined;
  return provider.getActiveTransferQuote(planKey);
};


export const getTransferQuote = async (
  planKey: string,
) => {
  if (!provider.getTransferQuote) return undefined;
  return provider.getTransferQuote(planKey);
};

export const canReviewTransferCancellation =
  !!provider.getTransferCancellation;

export const getTransferCancellation = async (
  planKey: string,
) => provider.getTransferCancellation?.(planKey);

export const canCancelTransfer =
  !!provider.cancelTransfer;

export const cancelTransfer = async (
  qc: QueryClient,
  planKey: string,
  transactionId: string,
) => {
  if (!provider.cancelTransfer) return undefined;

  try {
    const result = await provider.cancelTransfer(
      planKey,
      transactionId,
    );

    qc.setQueryData<AtlasState>(
      atlasQuery().queryKey,
      result.state,
    );

    return result;
  } catch (error) {
    // Outcome may be uncertain. Refresh authoritative state.
    void qc.invalidateQueries({
      queryKey: atlasQuery().queryKey,
    });
    throw error;
  }
};

export const canConfirmTransfer =
  !!provider.confirmTransfer;

export const confirmTransfer = async (
  qc: QueryClient,
  planKey: string,
  quoteId: string,
) => {
  if (!provider.confirmTransfer) return undefined;

  try {
    const result = await provider.confirmTransfer(
      planKey,
      quoteId,
    );

    qc.setQueryData<AtlasState>(
      atlasQuery().queryKey,
      result.state,
    );

    return result.transaction;
  } catch (error) {
    // An interrupted request is not evidence that no booking occurred.
    void qc.invalidateQueries({
      queryKey: atlasQuery().queryKey,
    });
    throw error;
  }
};
