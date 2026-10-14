// Types for the priority downlink queue. Mirror backend/app/schemas/queue.py and mission.py.
import type { DiagnosticsResponse, Severity } from "./diagnostics";

export type DataClass = "image_batch" | "science_bulk" | "telemetry_log";
export type Action = "PROCESS_NOW" | "STORE" | "TRANSMIT_RAW" | "TRANSMIT_COMPRESSED" | "DROP";

export const DATA_CLASSES: { value: DataClass; label: string }[] = [
  { value: "image_batch", label: "Image batch" },
  { value: "science_bulk", label: "Science bulk" },
  { value: "telemetry_log", label: "Telemetry log" },
];

export type ItemAssessment = {
  id: string;
  data_class: DataClass;
  size_MB: number;
  usability: number | null;
  urgency_probs: number[] | null;
  severity: Severity | null;
  health_status: string | null;
  ai_trusted: boolean;
  protected: boolean;
};

export type SpacecraftState = {
  battery_pct: number;
  battery_capacity_Wh: number;
  in_eclipse: boolean;
  storage_used_MB: number;
  storage_capacity_MB: number;
};

export type NextPass = { starts_in_s: number; duration_s: number; capacity_MB: number };

export type QueueSlot = {
  rank: number;
  item_id: string;
  data_class: DataClass;
  size_MB: number;
  action: Action;
  rule: string;
  reason: string;
  value: number;
  priority: number;
  send_MB: number | null;
  codec: string | null;
  pass_start_MB: number | null;
};

export type QueuePlan = {
  sat_id: string;
  created_at: string;
  capacity_MB: number | null;
  booked_MB: number;
  this_pass: QueueSlot[];
  waiting: QueueSlot[];
  dropped: QueueSlot[];
};

// A diagnosed product becomes the assessment the onboard policy ranks.
export function toAssessment(id: string, dataClass: DataClass, sizeMB: number, isProtected: boolean, d: DiagnosticsResponse): ItemAssessment {
  const probs = d.ai?.urgency_probs;
  return {
    id,
    data_class: dataClass,
    size_MB: sizeMB,
    usability: d.ai?.usability_pred ?? null,
    urgency_probs: probs && probs.every((p): p is number => p !== null) ? probs : null,
    severity: d.health.severity,
    health_status: d.health.health_status,
    ai_trusted: d.health.ai_prediction_trusted,
    protected: isProtected,
  };
}
