"use client";

import { useEffect, useRef, useState } from "react";
import { motion } from "framer-motion";
import {
  BANDS,
  ENTROPY_LIMIT,
  NOVELTY_LIMIT,
  demoPatches,
  encodeNpy,
  errorMessage,
  makeDemoPatch,
  type AiOutputs,
  type DemoKind,
  type DiagnosticsResponse,
  type HealthReport,
} from "./diagnostics";

type Input = { blob: Blob; name: string; source: string };
type RunState =
  | { status: "idle" }
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "success"; data: DiagnosticsResponse };

const BATTERY_FLOOR = 30;

const ACRONYMS = new Set(["AI", "ADC", "SRAM", "VNIR", "FDIR"]);
const humanize = (value: string) =>
  value.split("_").map((word, i) => (ACRONYMS.has(word) ? word : i === 0 ? word[0] + word.slice(1).toLowerCase() : word.toLowerCase())).join(" ");
const fmt = (value: number | null, digits = 3) => (value === null ? "—" : value.toFixed(digits));
const formatBytes = (n: number) => (n < 1024 * 1024 ? `${(n / 1024).toFixed(1)} KB` : `${(n / 1024 / 1024).toFixed(1)} MB`);

export default function HealthView() {
  const [input, setInput] = useState<Input | null>(null);
  const [demo, setDemo] = useState<DemoKind | null>(null);
  const [battery, setBattery] = useState(85);
  const [run, setRun] = useState<RunState>({ status: "idle" });
  const request = useRef<AbortController | null>(null);

  useEffect(() => () => request.current?.abort(), []);

  const chooseDemo = (kind: DemoKind) => {
    const patch = makeDemoPatch(kind);
    setDemo(kind);
    setInput({ blob: encodeNpy(patch), name: patch.name, source: `Demo · ${patch.shape.join(" × ")}` });
  };

  const chooseFile = (file: File | undefined) => {
    if (!file) return;
    setDemo(null);
    setInput({ blob: file, name: file.name, source: `Upload · ${formatBytes(file.size)}` });
  };

  const diagnose = async () => {
    if (!input) return;
    request.current?.abort();
    const controller = new AbortController();
    request.current = controller;
    setRun({ status: "loading" });

    const form = new FormData();
    form.append("patch", input.blob, input.name);
    form.append("battery_pct", String(battery));
    try {
      const res = await fetch("/api/diagnostics/health", { method: "POST", body: form, signal: controller.signal });
      const body: unknown = await res.json().catch(() => null);
      if (!res.ok) setRun({ status: "error", message: errorMessage(res.status, body) });
      else setRun({ status: "success", data: body as DiagnosticsResponse });
    } catch (err) {
      if ((err as Error).name !== "AbortError") setRun({ status: "error", message: "Could not reach the diagnostics service." });
    }
  };

  return <motion.div key="Satellite health" className="view-content detail-view" initial={{ opacity: 0, y: 14 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -10 }} transition={{ duration: 0.25 }}>
    <div className="detail-heading"><p className="eyebrow">MISSION CONTROL <span>/</span> FAULT DETECTION</p><h1>Camera, AI<br /><em>or Earth?</em></h1><p>Run an image patch through the onboard model and the FDIR watchdog to isolate what is wrong, and what to do next.</p></div>

    <div className="hv-grid">
      <section className="panel hv-input">
        <div className="panel-head"><div><span className="section-index">INPUT</span><h2>Image patch</h2></div><span className="link-status">6 BANDS · [0, 1]</span></div>

        <p className="hv-label">Generate a demo patch</p>
        <div className="hv-demos">{demoPatches.map((d) => <button type="button" key={d.kind} className={demo === d.kind ? "selected" : ""} aria-pressed={demo === d.kind} onClick={() => chooseDemo(d.kind)}><b>{d.label}</b><small>{d.description}</small></button>)}</div>

        <p className="hv-label">or upload a .npy file</p>
        <label className="hv-upload"><input type="file" accept=".npy" onChange={(e) => { chooseFile(e.target.files?.[0]); e.target.value = ""; }} /><span>Choose file</span><small>float32 array (6, H, W) · {BANDS.join(", ")}</small></label>

        <div className="hv-selected" aria-live="polite">{input ? <><b>{input.name}</b><small>{input.source}</small></> : <small>No patch selected yet</small>}</div>

        <div className="hv-battery">
          <div><label htmlFor="hv-battery">Battery</label><b className={battery < BATTERY_FLOOR ? "low" : ""}>{battery}%</b></div>
          <input id="hv-battery" type="range" min={0} max={100} step={1} value={battery} onChange={(e) => setBattery(Number(e.target.value))} />
          <small>Below {BATTERY_FLOOR}% the watchdog marks the spacecraft as not battery-safe.</small>
        </div>

        <button type="button" className="primary-button" disabled={!input || run.status === "loading"} onClick={diagnose}>{run.status === "loading" ? "Running diagnostics…" : "Run diagnostics"} <span>↗</span></button>
      </section>

      <section className="panel hv-result" aria-live="polite" aria-busy={run.status === "loading"}>
        <div className="panel-head"><div><span className="section-index">WATCHDOG</span><h2>Health report</h2></div></div>
        {run.status === "idle" && <div className="hv-empty"><span>✳</span><p>Choose a demo patch or upload a .npy file, then run diagnostics.</p></div>}
        {run.status === "loading" && <div className="hv-empty hv-loading"><span>◌</span><p>Running sensor, network-integrity and novelty checks…</p></div>}
        {run.status === "error" && <div className="hv-error" role="alert"><b>Diagnostics failed</b><p>{run.message}</p><button type="button" className="text-action" onClick={diagnose} disabled={!input}>Try again <span>↻</span></button></div>}
        {run.status === "success" && <StatusCard health={run.data.health} />}
      </section>
    </div>

    {run.status === "success" && <ModelOutputs ai={run.data.ai} />}
  </motion.div>;
}

function StatusCard({ health }: { health: HealthReport }) {
  const severity = health.severity.toLowerCase();
  return <div className={`hv-status ${severity}`}>
    <span className={`hv-severity ${severity}`}><i /> {health.severity}</span>
    <h3>{humanize(health.health_status)}</h3>
    <p className="hv-message">{health.diagnostic_message}</p>
    <div className="hv-facts">
      <div><span>RECOMMENDED ACTION</span><strong>{humanize(health.recommended_action)}</strong></div>
      <div><span>ROOT CAUSE</span><strong>{humanize(health.root_cause)}</strong></div>
      <div><span>AI PREDICTION</span><strong className={health.ai_prediction_trusted ? "ok" : "bad"}>{health.ai_prediction_trusted ? "Trusted" : "Not trusted"}</strong></div>
      <div><span>BATTERY</span><strong className={health.battery_safe ? "ok" : "bad"}>{health.battery_safe ? "Safe" : "Below reserve"}</strong></div>
    </div>
    <small className="hv-time">{new Date(health.timestamp).toUTCString()}</small>
  </div>;
}

function ModelOutputs({ ai }: { ai: AiOutputs | null }) {
  return <section className="panel hv-outputs">
    <div className="panel-head"><div><span className="section-index">EDGE MODEL</span><h2>Model outputs</h2></div><span className="link-status">DVM · TORCHSCRIPT</span></div>
    {ai === null ? <div className="hv-empty"><span>⌀</span><p>Inference skipped: the patch contained NaN or Inf pixels, so it never reached the model.</p></div> : <div className="hv-outputs-grid">
      <div className="hv-bars" role="img" aria-label={`Urgency probabilities: ${ai.urgency_probs.map((p, i) => `class ${i + 1} ${fmt(p, 2)}`).join(", ")}`}>
        <p className="hv-label">Urgency probabilities</p>
        {ai.urgency_probs.map((p, i) => <div className="hv-bar" key={i}><span>Class {i + 1}</span><div><motion.i initial={{ width: 0 }} animate={{ width: `${Math.max(0, Math.min(1, p ?? 0)) * 100}%` }} transition={{ duration: 0.5, delay: i * 0.05 }} /></div><b>{fmt(p, 2)}</b></div>)}
      </div>
      <div className="link-stats hv-stats">
        <div><span>USABILITY</span><strong>{fmt(ai.usability_pred, 2)}</strong></div>
        <div><span>ENTROPY</span><strong className={ai.entropy !== null && ai.entropy > ENTROPY_LIMIT ? "bad" : ""}>{fmt(ai.entropy)}</strong><small>limit {ENTROPY_LIMIT}</small></div>
        <div><span>RECON ERROR</span><strong className={ai.recon_error !== null && ai.recon_error > NOVELTY_LIMIT ? "bad" : ""}>{fmt(ai.recon_error)}</strong><small>novelty &gt; {NOVELTY_LIMIT}</small></div>
      </div>
    </div>}
  </section>;
}
