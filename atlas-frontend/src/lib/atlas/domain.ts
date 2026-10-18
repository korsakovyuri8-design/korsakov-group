// Atlas frontend view models — what the UI needs, not a mirror of the backend schema.
// Providers (demo fixture or real API) map into these shapes. Optional = genuinely optional:
// when the source doesn't supply a field, it stays absent and the UI hides it or says so.

/** Explicit trip state. Decided by the provider (backend), never computed in the UI. */
export type TripStateKind = "ON_PLAN" | "FLIGHT_CHANGED" | "MISSING_TRANSPORT" | "NO_PLAN";

export type TripState =
  | { kind: "ON_PLAN" }
  | { kind: "FLIGHT_CHANGED"; attentionItemId?: string | undefined }
  | { kind: "MISSING_TRANSPORT" }
  | { kind: "NO_PLAN" };

export type ItemStatus =
  | "confirmed"
  | "planned"
  | "tentative"
  | "changed"
  | "cancelled"
  | "missing"
  | "attention"
  | "paused" // downstream of an unresolved movement — no longer proven
  | "listed"; // on the itinerary; the source gave no status for this line

export type ItemKind = "flight" | "transfer" | "hotel" | "free" | "restaurant" | "activity";

export interface Fact {
  label: string;
  value: string;
}

/** One field that changed on a booking: Original → Current. */
export interface BookingChange {
  field: string;
  from: string;
  to: string;
  at?: string | undefined;
}

/** Where Atlas learned about a booking. Shown quietly, never as debug data. */
export interface BookingSource {
  label: string;
  receivedAt?: string | undefined;
}

export interface TripItem {
  id: string;
  kind: ItemKind;
  status: ItemStatus;
  /** True for real bookings (open a detail page); false for gaps and free time. */
  isBooking: boolean;
  day: string;
  city: string;
  title: string;
  subtitle?: string | undefined;
  /** "HH:MM". Absent = unknown. */
  start?: string | undefined;
  end?: string | undefined;
  previousStart?: string | undefined;
  previousEnd?: string | undefined;
  provider?: string | undefined;
  reference?: string | undefined;
  facts?: Fact[] | undefined;
  changes?: BookingChange[] | undefined;
  source?: BookingSource | undefined;
}

export type PlanPlaceRole = "venue" | "stay" | "both";

export interface PlanningNeed {
  id: string;
  code: string;
  category: string;
  state: string;
  basis?: string | undefined;
  planKey?: string | undefined;
}

export interface TripIssue {
  id: string;
  kind: "missing_transport" | "may_not_fit" | "flight_changed" | "overlap" | "unknown_status" | "conflict";
  title: string;
  what: string;
  /** Backend sentences, shown verbatim in order. */
  lines?: string[] | undefined;
  why?: string | undefined;
  action?: { label: string | undefined; itemId: string };
  relatedIds: string[];
}

/** Position on the illustrative placeholder map. Real maps use `coords`. */
export interface SketchPoint { x: number; y: number }
export interface Coords { lat: number; lng: number }

export interface DiscoverySuggestion {
  id: string;
  name: string;
  category?: string | undefined;
  /** Minutes. null = Atlas doesn't know. */
  travel: { fromMinutes: number | null; toMinutes: number | null; mode?: "walk" };
  visit: { start: string; end: string };
  /** Only when the source exposes opening-hours evidence. Absent = no footer. */
  hours?: { label?: string; confidence: "verified" | "unverified" } | undefined;
  /** Leave the start place / arrive at the next place — supplied by Atlas. */
  leaveAt?: string | undefined;
  arriveNextAt?: string | undefined;
  visitMinutes?: number | undefined;
  why?: string | undefined;
  sketch?: SketchPoint | undefined;
  coords?: Coords | undefined;
}

export interface WindowAnchor {
  place: string;
  /** Absent when the arrival/departure there is not proven. */
  time?: string | undefined;
  /** Short noun for sentences: "hotel", "dinner". */
  noun: string;
}

export interface FreeWindow {
  id: string;
  status: "valid" | "on_hold";
  holdReason?: string | undefined;
  start: string;
  end: string;
  durationMinutes: number;
  from: WindowAnchor;
  to: WindowAnchor;
  suggestions: DiscoverySuggestion[];
  /** Backend sentence, rendered as-is when present. */
  headline?: string | undefined;
  day?: string | undefined;
  /** Policy buffer before the next booking, when the source states one. */
  endBufferMinutes?: number | undefined;
  considered?: number | undefined;
  leftOut?: number | undefined;
}

export interface MapPlace {
  id: string;
  label: string;
  role: "stay" | "next" | "booking" | "suggestion";
  itemId?: string | undefined;
  sketch?: SketchPoint | undefined;
  coords?: Coords | undefined;
}

export interface PlanningNeed {
  id: string;
  code: string;
  category: string;
  state: string;
  basis?: string | undefined;
  planKey?: string | undefined;
}

export interface RoutePlanPoint {
  name: string;
  locality?: string | undefined;
  coords?: Coords | undefined;
}

export interface RoutePlan {
  status: "estimated";
  mode: "driving";
  origin: RoutePlanPoint;
  destination: RoutePlanPoint;
  distanceKm: number;
  routeDurationMinutes: number;
  planningBufferMinutes: number;
  arriveByLocal: string;
  leaveByLocal: string;
  timezone: string;
  trafficLive: boolean;
  provider?: string | undefined;
  providerGuarantee: boolean;
  policyVersion?: string | undefined;
  basis?: string | undefined;
  generatedAt?: string | undefined;
}

export type MobilityMode =
  | "private_transfer"
  | "scheduled_bus"
  | "rental_car"
  | "rail";

export type MobilityModeStatus =
  | "available"
  | "needs_details"
  | "unavailable";

export interface MobilityProviderSummary {
  id: string;
  slug: string;
  name: string;
  sandbox: boolean;
}

export interface MobilityModeResult {
  mode: MobilityMode;
  serviceType: string;
  status: MobilityModeStatus;
  reason: string;
  providers: MobilityProviderSummary[];
  quoteCreated: boolean;
  bookingCreated: boolean;
}

export interface MobilityComparison {
  request: {
    origin: string;
    destination: string;
    partySize: number;
    arriveByLocal: string;
    leaveByLocal?: string | undefined;
    timezone: string;
  };
  modes: MobilityModeResult[];
}

export interface MobilityQuote {
  id: string;
  code: string;
  status: string;
  provider: string;
  sandbox: boolean;
  serviceType: string;
  amount: string;
  currency: string;
  description?: string | undefined;
  conditions?: string | undefined;
  validUntil: string;
  request: Record<string, unknown>;
  bookingCreated: boolean;
  transactionCreated: boolean;
}

export interface ConfirmedTransport {
  status: string;
  provider: string;
  providerReference?: string | undefined;
  pickup?: string | undefined;
  destination?: string | undefined;
  pickupTime?: string | undefined;
  partySize?: number | undefined;
  amount?: string | undefined;
  currency?: string | undefined;
  sandbox: boolean;
}

export interface MobilityTransaction {
  id: string;
  actionId: string;
  quoteId: string;
  status:
    | "proposed"
    | "submitted"
    | "accepted"
    | "in_progress"
    | "completed"
    | "rejected"
    | "failed"
    | "cancelled"
    | string;
  provider: string;
  providerReference?: string | undefined;
  serviceType: string;
  amount: string;
  currency: string;
  sandbox: boolean;
  transactionCreated: boolean;
  providerAccepted: boolean;
  realWorld: boolean;
}

export interface MobilityConfirmResult {
  transaction: MobilityTransaction;
  state: AtlasState;
}

export interface Trip {
  id: string;
  title: string;
  dates: string;
  route: string[];
  items: TripItem[];
  issues: TripIssue[];
  /** Unconfirmed planning gaps. Separate from broken/at-risk bookings. */
  planningNeeds?: PlanningNeed[] | undefined;
  /** Derived road estimate. Never means transport is booked. */
  routePlan?: RoutePlan | undefined;
  /** Provider-confirmed transport, safe public summary only. */
  confirmedTransport?: ConfirmedTransport | undefined;
  freeWindow?: FreeWindow | undefined;
  places: MapPlace[];
  /** Confirmed bookings as the source lists them (may differ from itinerary lines). */
  bookings?: TripItem[] | undefined;
  /** Windows a disruption put on hold (backend: windows_no_longer_provable). */
  heldWindows?: FreeWindow[] | undefined;
  /** Ordered place ids to connect on the map. */
  mapPath?: string[] | undefined;
}

/** Where the plan places you, exactly as the backend supplies it. Never physical location. */
export type PlannedLocation =
  | { status: "arrived"; place?: string | undefined; locality?: string | undefined; since?: string | undefined }
  | { status: "at"; place?: string | undefined; locality?: string | undefined }
  | { status: "in_transit"; from?: string | undefined; to?: string | undefined; until?: string | undefined }
  | { status: "unknown"; reason?: string | undefined };

/** "Now" as supplied by the provider. Components never invent the current time. */
export interface TripContext {
  day: string;
  /** "HH:MM". Absent = the source supplies no clock. */
  time?: string | undefined;
  timezone?: string | undefined;
  /** Where the plan says you are. Atlas never tracks physical location. */
  plannedPlace?: string | undefined;
  plannedLocation?: PlannedLocation | undefined;
  clock: "frozen_demo" | "live";
}

/** A next step the backend offers (e.g. add the sample transfer). */
export interface NextStep { id: string; label: string; hint?: string | undefined }

/** The backend's next item. The UI renders it; it never derives it. */
export interface NextItem {
  item: TripItem;
  minutesFromNow?: number | undefined;
  /** Non-empty = at risk. The booking itself is kept. */
  issues: string[];
}

/** Everything a screen needs for one coherent moment of the trip. */
export interface AtlasState {
  state: TripState;
  context: TripContext;
  trip: Trip;
  next?: NextStep[] | undefined;
  nextItem?: NextItem | undefined;
  /** Live providers only: "none" before a trip exists, "ended" when the session expired. */
  session?: "active" | "none" | "ended" | undefined;
}

export interface ImportedBooking {
  kind?: ItemKind | undefined;
  title?: string | undefined;
  outcome?: "new" | "updated" | undefined;
  /** False for a stated plan: useful context, not a provider-confirmed booking. */
  isBooking?: boolean | undefined;
  /** Only fields present in the response. */
  facts: Fact[];
  changes?: string[] | undefined;
}

export type ImportInput = { text: string } | { sample: string };

export type ImportResult =
  | { ok: true; bookings: ImportedBooking[]; state?: AtlasState | undefined }
  | { ok: false; reason: "unsupported" | "unreachable" | "ended"; message: string; detail?: string | undefined };

export interface ScenarioOption { id: TripStateKind; label: string }

/** Both providers implement exactly this. No component knows which one is active. */
export interface AtlasProvider {
  /** Selectable design/QA moments. Empty for a live provider. */
  scenarios: ScenarioOption[];
  /** True when state lives on a server session (browser-only). */
  live: boolean;
  getState(scenario?: TripStateKind): Promise<AtlasState>;
  importBooking(input: ImportInput): Promise<ImportResult>;
  /** Start from the backend's sample trip. */
  startSample?: (() => Promise<ImportResult>) | undefined;
  /** Forget the current session. */
  reset?: (() => Promise<void>) | undefined;
  /** Compare mobility modes. This creates no Quote and no booking. */
  compareMobility?: ((planKey: string) => Promise<MobilityComparison>) | undefined;
  /** Read the still-valid transfer Quote for the current plan. Creates nothing. */
  getActiveTransferQuote?: ((planKey: string) => Promise<MobilityQuote | undefined>) | undefined;
  /** Request a priced private-transfer offer. This creates a Quote, not a booking. */
  getTransferQuote?: ((planKey: string) => Promise<MobilityQuote>) | undefined;
  /** Explicitly accept one quoted transfer. */
  confirmTransfer?: ((
    planKey: string,
    quoteId: string,
  ) => Promise<MobilityConfirmResult>) | undefined;
  /** Ask Python Atlas to calculate and store a route estimate. */
  answerPartySize?: ((planKey: string, value: number) => Promise<AtlasState>) | undefined;
  planRoute?: ((planKey: string) => Promise<AtlasState>) | undefined;
  /** Save an explicit answer to the place-role clarification. */
  answerPlaceRole?: ((planKey: string, role: PlanPlaceRole) => Promise<AtlasState>) | undefined;
  /** Save an explicit date clarification for this stated plan. */
  answerPlanDate?: ((planKey: string, value: string) => Promise<AtlasState>) | undefined;
  /** Save whether this stated plan needs an overnight stay. */
  answerStayIntent?: ((planKey: string, value: boolean) => Promise<AtlasState>) | undefined;
  /** Save the traveller-stated origin for this plan. */
  answerOrigin?: ((planKey: string, origin: string) => Promise<AtlasState>) | undefined;
  answerArrivalTime?: ((planKey: string, value: string) => Promise<AtlasState>) | undefined;
  /** Optional sample text a provider can offer for import. */
  sampleImportText?: string | undefined;
}
