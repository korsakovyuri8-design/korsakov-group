// DEMO FIXTURE — frozen copy of the deterministic Atlas backend demo world.
// Booking facts (DM1432, HK20931, R4417, TR551) and discovery entity names match the backend fixtures.
// Values the backend didn't supply are left absent, never made more realistic.
// Read only through DemoAtlasProvider (./providers/demo) — never from components.
import type { AtlasState, DiscoverySuggestion, FreeWindow, ImportedBooking, MapPlace, Trip, TripContext, TripIssue, TripItem, TripStateKind } from "./domain";

const DAY = "Sun 18 Oct";
const CHECKOUT_DAY = "Wed 21 Oct";

// ---- Bookings --------------------------------------------------------------
const flight: TripItem = {
  id: "flt-dm1432",
  kind: "flight",
  status: "confirmed",
  isBooking: true,
  day: DAY,
  city: "London",
  start: "09:15",
  end: "12:55",
  title: "DM1432",
  subtitle: "London Gatwick (LGW) → Podgorica (TGD)",
  provider: "Demo Air",
  reference: "DMX7Q2",
  facts: [
    { label: "Flight", value: "DM1432" },
    { label: "From", value: "London Gatwick (LGW)" },
    { label: "To", value: "Podgorica (TGD)" },
    { label: "Date", value: "18 Oct 2026" },
    { label: "Cabin", value: "Economy" },
    { label: "Passengers", value: "Ana Example, Marko Example" },
  ],
  source: { label: "Demo Air booking confirmation" },
};

const flightChanged: TripItem = {
  ...flight,
  status: "changed",
  start: "11:15",
  end: "14:55",
  previousStart: "09:15",
  previousEnd: "12:55",
  changes: [
    { field: "Departure", from: "09:15", to: "11:15" },
    { field: "Arrival", from: "12:55", to: "14:55" },
  ],
};

const transfer: TripItem = {
  id: "trf-tr551",
  kind: "transfer",
  status: "confirmed",
  isBooking: true,
  day: DAY,
  city: "Podgorica",
  start: "15:00",
  end: "16:00",
  title: "Demo Transfers",
  subtitle: "Podgorica Airport (TGD) → Stari grad 1, Kotor",
  provider: "Demo Transfers",
  reference: "TR551",
  facts: [
    { label: "Pickup", value: "Podgorica Airport (TGD)" },
    { label: "Pickup time", value: "15:00" },
    { label: "Drop-off", value: "Stari grad 1, Kotor, Montenegro" },
    { label: "Drop-off time", value: "16:00" },
    { label: "Passengers", value: "2" },
  ],
  source: { label: "Demo Transfers booking confirmation" },
};

const hotelFacts = [
  { label: "Address", value: "Stari grad 1, Kotor, Montenegro" },
  { label: "Check-in", value: "18 Oct 2026, from 14:00" },
  { label: "Check-out", value: "21 Oct 2026, until 11:00" },
  { label: "Room", value: "Double room, sea view" },
  { label: "Guests", value: "2" },
];

const hotelArrival: TripItem = {
  id: "htl-hk20931",
  kind: "hotel",
  status: "confirmed",
  isBooking: true,
  day: DAY,
  city: "Kotor",
  start: "16:00",
  title: "Hotel Demo Kotor",
  subtitle: "Arrival · check-in from 14:00",
  reference: "HK20931",
  facts: hotelFacts,
  source: { label: "Hotel Demo Kotor confirmation" },
};

// Hotel booking is still confirmed; only the arrival time is no longer proven.
const hotelArrivalUnproven: TripItem = { ...hotelArrival, start: undefined, subtitle: "Booking confirmed · arrival not proven" };

const hotelCheckout: TripItem = {
  ...hotelArrival,
  id: "htl-hk20931-out",
  day: CHECKOUT_DAY,
  start: "11:00",
  subtitle: "Check-out until 11:00",
};

const dinner: TripItem = {
  id: "rst-r4417",
  kind: "restaurant",
  status: "confirmed",
  isBooking: true,
  day: DAY,
  city: "Kotor",
  start: "20:00",
  title: "Konoba Demo",
  subtitle: "Dinner",
  provider: "DemoTable",
  reference: "R4417",
  facts: [
    { label: "Address", value: "Stari grad 210, Kotor, Montenegro" },
    { label: "Date", value: "18 Oct 2026" },
    { label: "Time", value: "20:00" },
    { label: "Party", value: "2" },
  ],
  source: { label: "DemoTable reservation" },
};

// ---- Derived items (backend-owned) -----------------------------------------
const free: TripItem = {
  id: "free-pre-dinner",
  kind: "free",
  status: "planned",
  isBooking: false,
  day: DAY,
  city: "Kotor",
  start: "16:30",
  end: "19:30",
  title: "Free time",
  subtitle: "Before dinner at Konoba Demo",
};

const freeOnHold: TripItem = { ...free, status: "paused", subtitle: "On hold — hotel arrival is no longer proven" };

const noTransport: TripItem = {
  id: "gap-tgd-kotor",
  kind: "transfer",
  status: "missing",
  isBooking: false,
  day: DAY,
  city: "Podgorica",
  title: "No transport booked",
  subtitle: "Podgorica Airport (TGD) → Kotor",
};

// ---- Discovery (synthetic demo world) ----------------------------------------
// Copied verbatim from the backend's trip.activities after the sample transfer
// (6 considered, 3 left out). Walking times and visit intervals are computed by Atlas.
const sg = (name: string, category: string, there: number, on: number, leave: string, from: string, to: string, arrive: string, mins: number): DiscoverySuggestion => ({
  id: `d-${name.toLowerCase().replace(/\s+/g, "-")}`, name, category,
  travel: { fromMinutes: there, toMinutes: on, mode: "walk" },
  visit: { start: from, end: to }, leaveAt: leave, arriveNextAt: arrive, visitMinutes: mins,
});

const windowValid: FreeWindow = {
  id: "fw-pre-dinner",
  status: "valid",
  start: "16:30",
  end: "19:30",
  durationMinutes: 180,
  from: { place: "Stari grad 1, Kotor, Montenegro", noun: "start" },
  to: { place: "Stari grad 210, Kotor, Montenegro", noun: "reservation" },
  headline: "You have 3 hours before dinner. Here are 3 things that actually fit.",
  day: "18 Oct",
  endBufferMinutes: 30,
  considered: 6,
  leftOut: 3,
  suggestions: [
    sg("City Walls Lookout", "viewpoint", 4, 7, "16:30", "16:34", "18:04", "18:11", 90),
    sg("Maritime Museum", "museum", 1, 2, "16:30", "16:31", "17:31", "17:33", 60),
    sg("Old Town Cathedral", "religious site", 2, 2, "16:30", "16:32", "17:02", "17:04", 30),
  ],
};

const windowOnHold: FreeWindow = {
  ...windowValid,
  status: "on_hold",
  headline: undefined,
  holdReason: "DM1432 now lands at 14:55, so Atlas can no longer prove when you reach Hotel Demo Kotor.",
  suggestions: [],
};

// ---- Issues ------------------------------------------------------------------
const transferMayNotFit: TripIssue = {
  id: "iss-transfer-fit",
  kind: "may_not_fit",
  title: "Transfer may no longer fit",
  what: "Demo Air moved DM1432 from 09:15 to 11:15. You now land at 14:55.",
  why: "Your Demo Transfers pickup is at 15:00 — five minutes after landing. Until that’s sorted, Atlas can’t prove when you reach Hotel Demo Kotor, so your free time before dinner is on hold.",
  action: { label: "Review transfer", itemId: "trf-tr551" },
  relatedIds: ["flt-dm1432", "trf-tr551", "free-pre-dinner"],
};

const missingTransport: TripIssue = {
  id: "iss-missing-transport",
  kind: "missing_transport",
  title: "Missing transport",
  // Verbatim backend lines. Hotel stays confirmed; arrival there is not asserted.
  lines: [
    "You land in Podgorica at 12:55.",
    "Your next confirmed place is dinner at Konoba Demo in Kotor at 20:00.",
    "There is no transport between them.",
  ],
  what: "You land in Podgorica at 12:55. Your next confirmed place is dinner at Konoba Demo in Kotor at 20:00. There is no transport between them.",
  relatedIds: ["flt-dm1432", "gap-tgd-kotor", "rst-r4417"],
};

// ---- Map (placeholder sketch positions) ---------------------------------------
const places: MapPlace[] = [
  { id: "p-hotel", label: "Hotel Demo Kotor", role: "stay", itemId: "htl-hk20931", sketch: { x: 36, y: 60 } },
  { id: "p-dinner", label: "Konoba Demo", role: "next", itemId: "rst-r4417", sketch: { x: 52, y: 66 } },
];

const trip = (items: TripItem[], issues: TripIssue[], freeWindow?: FreeWindow): Trip => ({
  id: "mne-oct-2026",
  title: "Montenegro",
  dates: "18–21 October 2026",
  route: ["London", "Podgorica", "Kotor"],
  items,
  issues,
  freeWindow,
  places,
  mapPath: ["p-hotel", "p-dinner"],
});

// Frozen demo clock — explicitly demo, never product truth.
const clock = (time: string, plannedPlace?: string): TripContext => ({ day: DAY, time, plannedPlace, clock: "frozen_demo" });

export const demoStates: Record<TripStateKind, AtlasState> = {
  ON_PLAN: {
    state: { kind: "ON_PLAN" },
    context: clock("16:42", "Hotel Demo Kotor"),
    trip: trip([flight, transfer, hotelArrival, free, dinner, hotelCheckout], [], windowValid),
  },
  FLIGHT_CHANGED: {
    state: { kind: "FLIGHT_CHANGED", attentionItemId: "trf-tr551" },
    context: clock("08:00"),
    trip: trip([flightChanged, { ...transfer, status: "attention" }, hotelArrivalUnproven, freeOnHold, dinner, hotelCheckout], [transferMayNotFit], windowOnHold),
  },
  MISSING_TRANSPORT: {
    state: { kind: "MISSING_TRANSPORT" },
    context: clock("08:00"),
    trip: trip([flight, noTransport, hotelArrivalUnproven, dinner, hotelCheckout], [missingTransport]),
  },
  NO_PLAN: {
    state: { kind: "NO_PLAN" },
    context: clock("13:00"),
    trip: { ...trip([], []), mapPath: [] , places: [] },
  },
};

// ---- Import fixture -----------------------------------------------------------
export const sampleConfirmation = `Demo Air — Booking confirmed
Booking reference: DMX7Q2
Flight DM1432
London Gatwick (LGW) → Podgorica (TGD)
18 Oct 2026 · Departs 09:15 · Arrives 12:55
Cabin: Economy
Passengers: Ana Example, Marko Example`;

export const sampleImport: ImportedBooking[] = [
  {
    kind: "flight",
    title: "DM1432",
    facts: [
      { label: "Provider", value: "Demo Air" },
      { label: "Booking reference", value: "DMX7Q2" },
      { label: "From", value: "London Gatwick (LGW)" },
      { label: "To", value: "Podgorica (TGD)" },
      { label: "Date", value: "18 Oct 2026" },
      { label: "Departure", value: "09:15" },
      { label: "Arrival", value: "12:55" },
      { label: "Cabin", value: "Economy" },
      { label: "Passengers", value: "Ana Example, Marko Example" },
    ],
  },
];

// ---- 90-second story ----------------------------------------------------------
export const demoStory: { title: string; body: string; cta?: string; items: TripItem[] }[] = [
  { title: "Three confirmations imported.", body: "London → Podgorica → Kotor · 18–21 October 2026", items: [flight, hotelArrivalUnproven, dinner] },
  {
    title: "There is no transport after you land.",
    body: "You land in Podgorica at 12:55. Your next confirmed place is dinner at Konoba Demo in Kotor at 20:00. There is no transport between them.",
    cta: "Add the transfer",
    items: [flight, noTransport, hotelArrivalUnproven, dinner],
  },
  { title: "Transfer confirmed.", body: "Demo Transfers TR551 · 15:00 → 16:00. Atlas rebuilt the day around it — and found real free time.", items: [flight, transfer, hotelArrival, free, dinner] },
  { title: "You have 3 hours before dinner.", body: "Here are 3 things that actually fit: City Walls Lookout, Maritime Museum, Old Town Cathedral.", items: [flight, transfer, hotelArrival, free, dinner] },
  {
    title: "Your transfer needs attention.",
    body: "Departure 09:15 → 11:15. Arrival 12:55 → 14:55. Transfer still at 15:00. Your free time before dinner is on hold.",
    items: [flightChanged, { ...transfer, status: "attention" }, hotelArrivalUnproven, freeOnHold, dinner],
  },
];
