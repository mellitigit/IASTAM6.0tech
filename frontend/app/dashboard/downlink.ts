// Types for the downlink delivery prediction view. Mirror backend/app/schemas/downlink.py.

export type FeatureValue = number | string | null;

export type LinkModelInfo = {
  model: string;
  target: string;
  features: string[];
  categorical_features: string[];
  threshold: number;
  threshold_policy: string;
  calibration: string;
  sklearn_version: string;
  dataset: string;
  warning: string;
};

export type LinkSample = {
  id: string;
  station_id: string;
  pass_id: string;
  recorded_success: 0 | 1;
  simulated_success_probability: number | null;
  failure_reason: string;
  features: Record<string, FeatureValue>;
};

export type PredictResponse = {
  probability: number;
  threshold: number;
  decision: "ATTEMPT" | "DEFER";
  model: string;
  warning: string;
};

// The inputs the notebook highlights when explaining a prediction, with display units.
export const KEY_INPUTS: { key: string; label: string; unit?: string; digits?: number }[] = [
  { key: "remaining_window_s", label: "Remaining window", unit: "s", digits: 0 },
  { key: "elevation_angle_deg", label: "Elevation", unit: "°", digits: 1 },
  { key: "sinr_dB", label: "SINR", unit: "dB", digits: 1 },
  { key: "link_margin_dB", label: "Link margin", unit: "dB", digits: 1 },
  { key: "modulation_coding", label: "MODCOD" },
  { key: "estimated_data_rate_Mbps", label: "Data rate", unit: "Mbit/s", digits: 1 },
  { key: "file_size_MB", label: "File size", unit: "MB", digits: 1 },
  { key: "capacity_ratio", label: "Capacity ratio", digits: 2 },
  { key: "rain_rate_mm_h", label: "Rain rate", unit: "mm/h", digits: 1 },
  { key: "weather_state", label: "Weather" },
];

export function formatValue(value: FeatureValue, digits = 2): string {
  if (value === null) return "—";
  if (typeof value === "number") return value.toLocaleString("en-US", { maximumFractionDigits: digits, minimumFractionDigits: 0 });
  return value.replace(/_/g, " ");
}
