"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { motion } from "framer-motion";
import { errorMessage } from "./diagnostics";
import { KEY_INPUTS, formatValue, type LinkModelInfo, type LinkSample, type PredictResponse } from "./downlink";

type Load<T> = { status: "loading" } | { status: "error"; message: string } | { status: "ready"; data: T };

async function getJson<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init);
  const body: unknown = await res.json().catch(() => null);
  if (!res.ok) throw new Error(errorMessage(res.status, body));
  return body as T;
}

const pct = (p: number | null) => (p === null ? "—" : `${(p * 100).toFixed(1)}%`);

export default function DownlinkView() {
  const [catalog, setCatalog] = useState<Load<{ info: LinkModelInfo; samples: LinkSample[] }>>({ status: "loading" });
  const [selected, setSelected] = useState<LinkSample | null>(null);
  const [prediction, setPrediction] = useState<Load<PredictResponse> | null>(null);
  const request = useRef<AbortController | null>(null);

  const fetchCatalog = useCallback(() => {
    Promise.all([getJson<LinkModelInfo>("/api/downlink/model"), getJson<LinkSample[]>("/api/downlink/samples")])
      .then(([info, samples]) => setCatalog({ status: "ready", data: { info, samples } }))
      .catch((err: Error) => setCatalog({ status: "error", message: err.message }));
  }, []);

  useEffect(() => {
    fetchCatalog();
    return () => request.current?.abort();
  }, [fetchCatalog]);

  const retryCatalog = () => {
    setCatalog({ status: "loading" });
    fetchCatalog();
  };

  const predict = async (sample: LinkSample) => {
    setSelected(sample);
    request.current?.abort();
    const controller = new AbortController();
    request.current = controller;
    setPrediction({ status: "loading" });
    try {
      const data = await getJson<PredictResponse>("/api/downlink/predict", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ features: sample.features }),
        signal: controller.signal,
      });
      setPrediction({ status: "ready", data });
    } catch (err) {
      if ((err as Error).name !== "AbortError") setPrediction({ status: "error", message: (err as Error).message });
    }
  };

  return <motion.div key="Downlink prediction" className="view-content detail-view" initial={{ opacity: 0, y: 14 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -10 }} transition={{ duration: 0.25 }}>
    <div className="detail-heading"><p className="eyebrow">MISSION CONTROL <span>/</span> COMMUNICATIONS</p><h1>Will this file<br /><em>make it down?</em></h1><p>Calibrated probability that a file is fully delivered and acknowledged before the contact window closes.</p></div>

    {catalog.status === "loading" && <div className="panel dl-panel"><div className="hv-empty hv-loading"><span>◌</span><p>Loading the link model and recorded attempts…</p></div></div>}
    {catalog.status === "error" && <div className="panel dl-panel"><div className="hv-error" role="alert"><b>Downlink model unavailable</b><p>{catalog.message}</p><button type="button" className="text-action" onClick={retryCatalog}>Try again <span>↻</span></button></div></div>}

    {catalog.status === "ready" && <>
      <div className="dl-grid">
        <section className="panel dl-panel">
          <div className="panel-head"><div><span className="section-index">DATASET</span><h2>Recorded attempts</h2></div><span className="link-status">HELD-OUT TEST SPLIT</span></div>
          {catalog.data.samples.length === 0 ? <div className="hv-empty"><span>⌀</span><p>No recorded attempts available. Generate them with backend/scripts/make_link_samples.py.</p></div> :
            <div className="dl-list">{catalog.data.samples.map((s) => <button type="button" key={s.id} className={selected?.id === s.id ? "selected" : ""} aria-pressed={selected?.id === s.id} onClick={() => predict(s)}>
              <span className={`dl-dot ${s.recorded_success ? "ok" : "bad"}`} aria-hidden="true" />
              <span className="dl-main"><b>{formatValue(s.features.mission_data_class)} · {formatValue(s.features.file_size_MB, 0)} MB</b><small>{s.id} · {s.station_id} · {formatValue(s.features.weather_state)} · {formatValue(s.features.remaining_window_s, 0)} s left</small></span>
              <span className="dl-sim"><b>{pct(s.simulated_success_probability)}</b><small>SIMULATOR</small></span>
            </button>)}</div>}
        </section>

        <section className="panel dl-panel" aria-live="polite" aria-busy={prediction?.status === "loading"}>
          <div className="panel-head"><div><span className="section-index">MODEL</span><h2>Delivery forecast</h2></div></div>
          {!selected && <div className="hv-empty"><span>⌁</span><p>Pick a recorded attempt to forecast whether its file gets through.</p></div>}
          {selected && prediction?.status === "loading" && <div className="hv-empty hv-loading"><span>◌</span><p>Scoring attempt {selected.id}…</p></div>}
          {selected && prediction?.status === "error" && <div className="hv-error" role="alert"><b>Prediction failed</b><p>{prediction.message}</p><button type="button" className="text-action" onClick={() => predict(selected)}>Try again <span>↻</span></button></div>}
          {selected && prediction?.status === "ready" && <Forecast sample={selected} result={prediction.data} />}
        </section>
      </div>
      <p className="dl-note">{catalog.data.info.warning} Model: {catalog.data.info.model} · threshold {catalog.data.info.threshold.toFixed(3)} ({catalog.data.info.threshold_policy}).</p>
    </>}
  </motion.div>;
}

function Forecast({ sample, result }: { sample: LinkSample; result: PredictResponse }) {
  const attempt = result.decision === "ATTEMPT";
  const agrees = attempt === Boolean(sample.recorded_success);
  return <div className="dl-forecast">
    <div className="dl-headline">
      <div><span className="dl-prob">{pct(result.probability)}</span><small>P(DELIVERED BEFORE LOS)</small></div>
      <span className={`dl-decision ${attempt ? "attempt" : "defer"}`}>{attempt ? "ATTEMPT TRANSMISSION" : "DEFER"}</span>
    </div>
    <div className="dl-meter" role="img" aria-label={`Probability ${pct(result.probability)}, threshold ${pct(result.threshold)}`}>
      <motion.i initial={{ width: 0 }} animate={{ width: `${result.probability * 100}%` }} transition={{ duration: 0.5 }} className={attempt ? "attempt" : "defer"} />
      <span style={{ left: `${result.threshold * 100}%` }}><em>threshold {result.threshold.toFixed(2)}</em></span>
    </div>
    <div className="hv-facts">
      <div><span>SIMULATOR (MONTE CARLO)</span><strong>{pct(sample.simulated_success_probability)}</strong></div>
      <div><span>RECORDED OUTCOME</span><strong className={sample.recorded_success ? "ok" : "bad"}>{sample.recorded_success ? "Delivered" : formatValue(sample.failure_reason)}</strong></div>
    </div>
    <p className={`dl-verdict ${agrees ? "ok" : "bad"}`}>{agrees ? "The decision matches what happened in this attempt." : "The decision differs from what happened in this attempt."}</p>
    <div className="dl-inputs">{KEY_INPUTS.map((k) => <div key={k.key}><span>{k.label}</span><b>{formatValue(sample.features[k.key] ?? null, k.digits)}{k.unit && sample.features[k.key] !== null ? <small> {k.unit}</small> : null}</b></div>)}</div>
  </div>;
}
