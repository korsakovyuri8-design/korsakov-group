import policy from "./attributionPolicy.json";

export const UTM_KEYS = [
  "utm_source",
  "utm_medium",
  "utm_campaign",
  "utm_content",
  "utm_term",
] as const;
type UtmKey = (typeof UTM_KEYS)[number];
export type Attribution = Partial<Record<UtmKey, string>>;
const STORAGE_KEY = "atlas.revenue.attribution.v1";
const contentPattern = new RegExp(`^${policy.utm_content_pattern}$`);

export function sanitizeAttribution(input: unknown): Attribution {
  if (!input || typeof input !== "object" || Array.isArray(input)) return {};
  const result: Attribution = {};
  for (const key of UTM_KEYS) {
    const value = (input as Record<string, unknown>)[key];
    if (typeof value !== "string" || value.length > 48) continue;
    const normalized = value.trim().toLowerCase();
    const allowed =
      key === "utm_content" ? contentPattern.test(normalized) : policy[key].includes(normalized);
    if (allowed) result[key] = normalized;
  }
  return result;
}

/** Last tagged landing in this browser tab; only approved campaign labels persist.
 * No URL, identity, cookie, private session token or cross-tab visitor identifier.
 */
export function captureRevenueAttribution(): Attribution {
  if (typeof window === "undefined") return {};
  const query = new URLSearchParams(window.location.search);
  if (UTM_KEYS.some((key) => query.has(key))) {
    const labels = sanitizeAttribution(
      Object.fromEntries(UTM_KEYS.map((key) => [key, query.get(key)])),
    );
    try {
      window.sessionStorage.setItem(STORAGE_KEY, JSON.stringify(labels));
    } catch {
      /* optional storage */
    }
    return labels;
  }
  try {
    return sanitizeAttribution(JSON.parse(window.sessionStorage.getItem(STORAGE_KEY) ?? "{}"));
  } catch {
    return {};
  }
}
