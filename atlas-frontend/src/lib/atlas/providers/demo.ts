// DemoAtlasProvider — frozen local design/QA data through the shared AtlasProvider interface.
import type { AtlasProvider, ImportResult } from "../domain";
import { demoStates, sampleConfirmation, sampleImport } from "../demo-data";

const UNSUPPORTED: ImportResult = { ok: false, reason: "unsupported", message: "Atlas can’t read this booking format yet.", detail: "No trip data was changed." };

export const DemoAtlasProvider: AtlasProvider = {
  scenarios: [
    { id: "ON_PLAN", label: "On plan" },
    { id: "FLIGHT_CHANGED", label: "Flight changed" },
    { id: "MISSING_TRANSPORT", label: "No transport" },
    { id: "NO_PLAN", label: "No plan" },
  ],
  live: false,
  async getState(scenario = "ON_PLAN") {
    return demoStates[scenario];
  },
  async importBooking(input) {
    await new Promise((r) => setTimeout(r, 600));
    // Fixture match only — the frontend never parses bookings. No state change offline.
    return "text" in input && input.text.trim() === sampleConfirmation.trim() ? { ok: true, bookings: sampleImport } : UNSUPPORTED;
  },
  sampleImportText: sampleConfirmation,
};
