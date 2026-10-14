"use client";

import { useEffect, useRef, useState } from "react";
import { motion } from "framer-motion";
import { demoPatches, encodeNpy, errorMessage, makeDemoPatch, type DemoKind, type DiagnosticsResponse } from "./diagnostics";
import { DATA_CLASSES, toAssessment, type DataClass, type ItemAssessment, type QueuePlan, type QueueSlot } from "./queue";

type Product = { assessment: ItemAssessment; source: string };
type PlanState = { status: "idle" } | { status: "loading" } | { status: "error"; message: string } | { status: "ready"; data: QueuePlan };

const STORAGE_CAPACITY_MB = 8000;
const SAT_ID = "ORBITAL-01";
const SAMPLE_SET: { kind: DemoKind; dataClass: DataClass; size: number }[] = [
  { kind: "nominal", dataClass: "science_bulk", size: 900 },
  { kind: "nominal", dataClass: "image_batch", size: 240 },
  { kind: "novelty", dataClass: "image_batch", size: 120 },
  { kind: "dead-band", dataClass: "image_batch", size: 180 },
  { kind: "nominal", dataClass: "telemetry_log", size: 35 },
  { kind: "saturated", dataClass: "science_bulk", size: 400 },
  { kind: "bit-flip", dataClass: "image_batch", size: 150 },
];

const label = (value: string) => value.replace(/_/g, " ").toLowerCase().replace(/^\w/, (c) => c.toUpperCase());
const mb = (value: number) => `${value.toLocaleString("en-US", { maximumFractionDigits: value < 10 ? 1 : 0 })} MB`;

async function assess(id: string, kind: DemoKind, dataClass: DataClass, size: number, isProtected: boolean, battery: number): Promise<Product> {
  const patch = makeDemoPatch(kind);
  const form = new FormData();
  form.append("patch", encodeNpy(patch), patch.name);
  form.append("battery_pct", String(battery));
  const res = await fetch("/api/diagnostics/health", { method: "POST", body: form });
  const body: unknown = await res.json().catch(() => null);
  if (!res.ok) throw new Error(errorMessage(res.status, body));
  const source = demoPatches.find((d) => d.kind === kind)?.label ?? kind;
  return { assessment: toAssessment(id, dataClass, size, isProtected, body as DiagnosticsResponse), source };
}

export default function QueueView() {
  const [products, setProducts] = useState<Product[]>([]);
  const [nextId, setNextId] = useState(1);
  const [kind, setKind] = useState<DemoKind>("nominal");
  const [dataClass, setDataClass] = useState<DataClass>("image_batch");
  const [size, setSize] = useState(200);
  const [isProtected, setIsProtected] = useState(false);
  const [adding, setAdding] = useState(false);
  const [addError, setAddError] = useState("");

  const [battery, setBattery] = useState(80);
  const [otherStorage, setOtherStorage] = useState(1500);
  const [passEnabled, setPassEnabled] = useState(true);
  const [capacity, setCapacity] = useState(800);
  const [plan, setPlan] = useState<PlanState>({ status: "idle" });
  const request = useRef<AbortController | null>(null);

  const addProducts = async (specs: { kind: DemoKind; dataClass: DataClass; size: number; protected?: boolean }[]) => {
    setAdding(true);
    setAddError("");
    let id = nextId;
    try {
      const made: Product[] = [];
      for (const spec of specs) {
        made.push(await assess(`P-${String(id).padStart(3, "0")}`, spec.kind, spec.dataClass, spec.size, spec.protected ?? false, battery));
        id += 1;
      }
      setProducts((current) => [...current, ...made]);
      setNextId(id);
    } catch (err) {
      setAddError((err as Error).message);
    } finally {
      setAdding(false);
    }
  };

  const productMB = products.reduce((sum, p) => sum + p.assessment.size_MB, 0);
  const storageUsed = Math.min(STORAGE_CAPACITY_MB, otherStorage + productMB);

  useEffect(() => {
    if (products.length === 0) return;
    const timer = window.setTimeout(async () => {
      request.current?.abort();
      const controller = new AbortController();
      request.current = controller;
      setPlan({ status: "loading" });
      try {
        const res = await fetch("/api/queue/plan", {
          method: "POST",
          headers: { "content-type": "application/json" },
          signal: controller.signal,
          body: JSON.stringify({
            sat_id: SAT_ID,
            items: products.map((p) => p.assessment),
            state: { battery_pct: battery, battery_capacity_Wh: 40, in_eclipse: false, storage_used_MB: storageUsed, storage_capacity_MB: STORAGE_CAPACITY_MB },
            next_pass: passEnabled ? { starts_in_s: 600, duration_s: 480, capacity_MB: capacity } : null,
          }),
        });
        const body: unknown = await res.json().catch(() => null);
        if (!res.ok) setPlan({ status: "error", message: errorMessage(res.status, body) });
        else setPlan({ status: "ready", data: body as QueuePlan });
      } catch (err) {
        if ((err as Error).name !== "AbortError") setPlan({ status: "error", message: "Could not reach the planning service." });
      }
    }, 250);
    return () => window.clearTimeout(timer);
  }, [products, battery, storageUsed, passEnabled, capacity]);

  useEffect(() => () => request.current?.abort(), []);

  const sourceOf = (id: string) => products.find((p) => p.assessment.id === id);

  return <motion.div key="Downlink queue" className="view-content detail-view" initial={{ opacity: 0, y: 14 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -10 }} transition={{ duration: 0.25 }}>
    <div className="detail-heading"><p className="eyebrow">MISSION CONTROL <span>/</span> DOWNLINK QUEUE</p><h1>Most important<br /><em>goes down first.</em></h1><p>Every product is scored by the onboard model and watchdog, ranked by the decision policy, and packed into the next pass in that order. The rest wait onboard, in line.</p></div>

    <div className="q-grid">
      <div className="q-left">
        <section className="panel hv-input">
          <div className="panel-head"><div><span className="section-index">ONBOARD</span><h2>Products</h2></div><span className="link-status">{products.length} · {mb(productMB)}</span></div>
          <div className="q-form">
            <label>Patch<select value={kind} onChange={(e) => setKind(e.target.value as DemoKind)}>{demoPatches.map((d) => <option key={d.kind} value={d.kind}>{d.label}</option>)}</select></label>
            <label>Type<select value={dataClass} onChange={(e) => setDataClass(e.target.value as DataClass)}>{DATA_CLASSES.map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}</select></label>
            <label>Size (MB)<input type="number" min={1} max={5000} value={size} onChange={(e) => setSize(Math.max(1, Number(e.target.value) || 1))} /></label>
            <label className="q-check"><input type="checkbox" checked={isProtected} onChange={(e) => setIsProtected(e.target.checked)} />Protected</label>
          </div>
          <div className="q-actions">
            <button type="button" className="primary-button" disabled={adding} onClick={() => addProducts([{ kind, dataClass, size, protected: isProtected }])}>{adding ? "Assessing…" : "Capture & assess"} <span>＋</span></button>
            <button type="button" className="text-action" disabled={adding} onClick={() => addProducts(SAMPLE_SET)}>Load sample set</button>
            {products.length > 0 && <button type="button" className="text-action" disabled={adding} onClick={() => { setProducts([]); setPlan({ status: "idle" }); }}>Clear</button>}
          </div>
          {addError && <div className="hv-error" role="alert"><b>Could not assess the product</b><p>{addError}</p></div>}
          {products.length > 0 && <ul className="q-products">{products.map((p) => <li key={p.assessment.id}>
            <span className={`hv-severity ${(p.assessment.severity ?? "nominal").toLowerCase()}`}><i /> {p.assessment.severity ?? "—"}</span>
            <span className="q-pmain"><b>{p.assessment.id} · {label(p.assessment.data_class)} · {mb(p.assessment.size_MB)}</b><small>{p.source} patch · usability {p.assessment.usability === null ? "—" : p.assessment.usability.toFixed(2)}{p.assessment.protected ? " · protected" : ""}{p.assessment.ai_trusted ? "" : " · AI untrusted"}</small></span>
            <button type="button" aria-label={`Remove ${p.assessment.id}`} onClick={() => setProducts((current) => current.filter((x) => x.assessment.id !== p.assessment.id))}>×</button>
          </li>)}</ul>}
        </section>

        <section className="panel hv-input">
          <div className="panel-head"><div><span className="section-index">STATE</span><h2>Spacecraft &amp; next pass</h2></div></div>
          <Slider id="q-battery" label="Battery" value={battery} min={0} max={100} unit="%" onChange={setBattery} warn={battery < 30} />
          <Slider id="q-storage" label="Other data onboard" value={otherStorage} min={0} max={6000} step={50} unit=" MB" onChange={setOtherStorage} />
          <p className="q-hint">Storage {mb(storageUsed)} of {mb(STORAGE_CAPACITY_MB)} ({Math.round((100 * storageUsed) / STORAGE_CAPACITY_MB)}%)</p>
          <label className="q-check q-pass-toggle"><input type="checkbox" checked={passEnabled} onChange={(e) => setPassEnabled(e.target.checked)} />Ground pass predicted</label>
          {passEnabled && <Slider id="q-capacity" label="Pass capacity" value={capacity} min={50} max={3000} step={10} unit=" MB" onChange={setCapacity} />}
        </section>
      </div>

      <section className="panel hv-result q-plan" aria-live="polite" aria-busy={plan.status === "loading"}>
        <div className="panel-head"><div><span className="section-index">PLAN</span><h2>Downlink order</h2></div>{plan.status === "loading" && <span className="link-status">UPDATING…</span>}</div>
        {products.length === 0 && <div className="hv-empty"><span>▤</span><p>Capture a few products, or load the sample set, to see them ranked.</p></div>}
        {products.length > 0 && plan.status === "error" && <div className="hv-error" role="alert"><b>Planning failed</b><p>{plan.message}</p></div>}
        {products.length > 0 && plan.status === "ready" && <PlanView plan={plan.data} sourceOf={sourceOf} />}
        {products.length > 0 && (plan.status === "idle" || (plan.status === "loading")) && <div className="hv-empty hv-loading"><span>◌</span><p>Ranking products…</p></div>}
      </section>
    </div>
  </motion.div>;
}

function Slider({ id, label: text, value, min, max, step = 1, unit, onChange, warn = false }: { id: string; label: string; value: number; min: number; max: number; step?: number; unit: string; onChange: (v: number) => void; warn?: boolean }) {
  return <div className="hv-battery q-slider">
    <div><label htmlFor={id}>{text}</label><b className={warn ? "low" : ""}>{value.toLocaleString("en-US")}{unit}</b></div>
    <input id={id} type="range" min={min} max={max} step={step} value={value} onChange={(e) => onChange(Number(e.target.value))} />
  </div>;
}

function PlanView({ plan, sourceOf }: { plan: QueuePlan; sourceOf: (id: string) => Product | undefined }) {
  const capacity = plan.capacity_MB;
  return <div>
    {capacity !== null && <div className="q-pass">
      <div className="q-pass-head"><span>NEXT PASS</span><b>{mb(plan.booked_MB)} of {mb(capacity)} booked</b></div>
      <div className="q-pass-bar" role="img" aria-label={`Next pass: ${plan.this_pass.map((s) => s.item_id).join(", ") || "nothing"} in order`}>
        {plan.this_pass.map((s) => <span key={s.item_id} className={s.priority >= 100 ? "alert" : ""} style={{ left: `${(100 * (s.pass_start_MB ?? 0)) / capacity}%`, width: `${(100 * (s.send_MB ?? 0)) / capacity}%` }} title={`#${s.rank} ${s.item_id}`}>{s.rank}</span>)}
      </div>
    </div>}
    <SlotList title={capacity === null ? "No pass predicted" : "Sent in the next pass, in order"} slots={plan.this_pass} sourceOf={sourceOf} empty={capacity === null ? "No ground contact is predicted, so nothing is sent yet." : "Nothing qualifies for this pass."} />
    <SlotList title="Waiting onboard, in order" slots={plan.waiting} sourceOf={sourceOf} empty="Nothing is waiting." />
    {plan.dropped.length > 0 && <SlotList title="Dropped" slots={plan.dropped} sourceOf={sourceOf} empty="" />}
  </div>;
}

function SlotList({ title, slots, sourceOf, empty }: { title: string; slots: QueueSlot[]; sourceOf: (id: string) => Product | undefined; empty: string }) {
  return <div className="q-section">
    <p className="hv-label">{title}</p>
    {slots.length === 0 ? <p className="q-none">{empty}</p> : <ol className="q-slots">{slots.map((s) => <li key={s.item_id}>
      <span className={`q-rank ${s.priority >= 100 ? "alert" : ""}`}>#{s.rank}</span>
      <span className="q-smain">
        <b>{s.item_id} · {label(s.data_class)} · {mb(s.size_MB)}{s.send_MB !== null && s.send_MB < s.size_MB ? ` → ${mb(s.send_MB)} ${s.codec}` : ""}</b>
        <small>{s.reason}</small>
        <small className="q-meta">{sourceOf(s.item_id)?.source ?? ""} patch · value {s.value.toFixed(0)} · priority {s.priority} · {s.rule}</small>
      </span>
      <span className={`q-action ${s.action.toLowerCase()}`}>{label(s.action)}</span>
    </li>)}</ol>}
  </div>;
}
