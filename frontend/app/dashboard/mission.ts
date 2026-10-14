// Types for the live mission view. Mirror backend/app/schemas/mission.py.

export const SAT_ID = "ORBITAL-01";
// Telemetry older than this (wall-clock) marks the spacecraft as out of contact.
export const STALE_MS = 15_000;

export type PassInfo = { station_id: string; station_name: string; aos: string; los: string; max_elevation_deg: number; duration_s: number };

export type QueueItem = {
  id: string;
  data_class: "image_batch" | "science_bulk" | "telemetry_log";
  size_MB: number;
  state: string;
  created_at: string;
  value: number | null;
  action: string | null;
  reason: string | null;
  protected: boolean;
  compression_ratio: number | null;
};

export type Telemetry = {
  sat_id: string;
  sim_time: string;
  speed: number;
  scenario: string;
  mode: "NOMINAL" | "SAFE" | "VNIR_4BAND" | "AI_RESTART";
  position: { lat_deg: number; lon_deg: number; alt_km: number };
  in_eclipse: boolean;
  eclipse_change_in_s: number | null;
  battery_pct: number;
  battery_capacity_Wh: number;
  power_in_W: number;
  power_out_W: number;
  temperature_C: number;
  storage_used_MB: number;
  storage_capacity_MB: number;
  bands_active: number;
  ai_available: boolean;
  in_contact: string | null;
  next_passes: PassInfo[];
  queue: QueueItem[];
  counters: Record<string, number>;
};

export type MissionEvent = { id: number; sat_id: string; kind: string; ts: string; payload: Record<string, unknown> };

export type Command = {
  id: string;
  sat_id: string;
  type: string;
  params: Record<string, unknown>;
  source: "operator" | "fdir" | "policy";
  reason: string;
  status: "PENDING" | "SENT" | "EXECUTED" | "REJECTED";
  created_at: string;
  acked_at: string | null;
  result: string | null;
};

export type DownlinkAttempt = {
  id: string;
  item_id: string;
  created_at: string;
  plan: { probability: number; threshold: number; decision: "ATTEMPT" | "DEFER"; station_id: string; size_MB: number; inputs: Record<string, unknown> };
  outcome: { attempted: boolean; delivered: boolean; delivered_MB: number; duration_s: number; failure_reason: string } | null;
};

export type MissionState = {
  sat_id: string;
  online: boolean;
  telemetry_age_s: number | null;
  battery_reserve_pct: number;
  telemetry: Telemetry | null;
  battery_history: { sim_time: string; battery_pct: number; in_eclipse: boolean }[];
  events: MissionEvent[];
  commands: Command[];
  downlinks: DownlinkAttempt[];
  last_event_id: number;
};

export const OPERATOR_COMMANDS: { type: string; label: string; hint: string }[] = [
  { type: "CAPTURE_NOW", label: "Capture now", hint: "Image on the next tick (needs sunlight)" },
  { type: "ENTER_SAFE_MODE", label: "Enter safe mode", hint: "Payload off, no discretionary work" },
  { type: "EXIT_SAFE_MODE", label: "Exit safe mode", hint: "Rejected if the battery is too low" },
  { type: "RESTORE_6BAND", label: "Restore 6-band", hint: "Power-cycle the SWIR detectors" },
];

// Merge a stream event into a downlink attempt list (newest first).
export function applyDownlinkEvent(list: DownlinkAttempt[], p: Record<string, unknown>): DownlinkAttempt[] {
  const id = p.attempt_id as string;
  if (p.phase === "planned") {
    const attempt: DownlinkAttempt = {
      id,
      item_id: p.item_id as string,
      created_at: new Date().toISOString(),
      plan: { probability: p.probability as number, threshold: p.threshold as number, decision: p.decision as "ATTEMPT" | "DEFER", station_id: p.station_id as string, size_MB: p.size_MB as number, inputs: (p.inputs ?? {}) as Record<string, unknown> },
      outcome: null,
    };
    return [attempt, ...list.filter((a) => a.id !== id)].slice(0, 30);
  }
  const outcome = { attempted: p.attempted as boolean, delivered: p.delivered as boolean, delivered_MB: p.delivered_MB as number, duration_s: p.duration_s as number, failure_reason: p.failure_reason as string };
  return list.map((a) => (a.id === id ? { ...a, outcome } : a));
}

const KEEP = new Set(["AI", "FDIR", "VNIR", "SWIR", "DVM", "LOS", "AOS"]);
const WORDS: Record<string, string> = { "4BAND": "4-band", "6BAND": "6-band", SAFE: "safe" };

export function label(value: string): string {
  const text = value.split("_").map((w) => (KEEP.has(w) ? w : WORDS[w] ?? w.toLowerCase())).join(" ");
  return text.charAt(0).toUpperCase() + text.slice(1);
}
export const mb = (value: number) => (value >= 1000 ? `${(value / 1000).toFixed(1)} GB` : `${value.toFixed(value < 10 ? 1 : 0)} MB`);

export function duration(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  return h ? `${h} h ${String(m).padStart(2, "0")} min` : m ? `${m} min ${String(s % 60).padStart(2, "0")} s` : `${s} s`;
}
