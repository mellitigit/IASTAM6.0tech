"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { motion } from "framer-motion";
import { errorMessage } from "./diagnostics";
import {
  OPERATOR_COMMANDS,
  SAT_ID,
  STALE_MS,
  applyDownlinkEvent,
  duration,
  label,
  mb,
  type Command,
  type DownlinkAttempt,
  type MissionEvent,
  type MissionState,
  type QueueItem,
  type Telemetry,
} from "./mission";

type Load = { status: "loading" } | { status: "error"; message: string } | { status: "ready" };
type Point = { sim_time: string; battery_pct: number; in_eclipse: boolean };
const FEED_KINDS = new Set(["diagnostic", "decision", "command", "alert", "log", "downlink"]);

// Live mission state: a snapshot, then the backend's SSE stream (telemetry, verdicts, decisions, commands, downlinks).
function useMission() {
  const [load, setLoad] = useState<Load>({ status: "loading" });
  const [reserve, setReserve] = useState(30);
  const [telemetry, setTelemetry] = useState<Telemetry | null>(null);
  const [history, setHistory] = useState<Point[]>([]);
  const [events, setEvents] = useState<MissionEvent[]>([]);
  const [commands, setCommands] = useState<Command[]>([]);
  const [downlinks, setDownlinks] = useState<DownlinkAttempt[]>([]);
  const [lastTelemetryAt, setLastTelemetryAt] = useState<number | null>(null);
  const [streaming, setStreaming] = useState(false);

  useEffect(() => {
    let source: EventSource | null = null;
    let cancelled = false;

    const onEvent = (raw: MessageEvent) => {
      const event: MissionEvent = JSON.parse(raw.data);
      const p = event.payload;
      if (event.kind === "telemetry") {
        const t = p as unknown as Telemetry;
        setTelemetry(t);
        setLastTelemetryAt(Date.now());
        setHistory((h) => [...h, { sim_time: t.sim_time, battery_pct: t.battery_pct, in_eclipse: t.in_eclipse }].slice(-240));
        return;
      }
      if (FEED_KINDS.has(event.kind)) setEvents((list) => [...list, event].slice(-80));
      if (event.kind === "command") {
        const c = p.command as Command;
        setCommands((list) => [c, ...list.filter((x) => x.id !== c.id)].slice(0, 30));
      }
      if (event.kind === "downlink") setDownlinks((list) => applyDownlinkEvent(list, p));
    };

    fetch(`/api/mission/state?sat_id=${SAT_ID}`, { cache: "no-store" })
      .then(async (res) => {
        const body: unknown = await res.json().catch(() => null);
        if (!res.ok) throw new Error(errorMessage(res.status, body));
        return body as MissionState;
      })
      .then((s) => {
        if (cancelled) return;
        setReserve(s.battery_reserve_pct);
        setTelemetry(s.telemetry);
        setHistory(s.battery_history);
        setEvents(s.events);
        setCommands(s.commands);
        setDownlinks(s.downlinks);
        setLastTelemetryAt(s.telemetry_age_s === null ? null : Date.now() - s.telemetry_age_s * 1000);
        setLoad({ status: "ready" });
        source = new EventSource(`/api/mission/stream?sat_id=${SAT_ID}&after=${s.last_event_id}`);
        source.onopen = () => setStreaming(true);
        source.onerror = () => setStreaming(false);
        for (const kind of ["telemetry", ...FEED_KINDS]) source.addEventListener(kind, onEvent as EventListener);
      })
      .catch((err: Error) => !cancelled && setLoad({ status: "error", message: err.message || "Could not reach the mission service." }));

    return () => {
      cancelled = true;
      source?.close();
    };
  }, []);

  return { load, reserve, telemetry, history, events, commands, downlinks, lastTelemetryAt, streaming };
}

function useNow(intervalMs = 1000) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), intervalMs);
    return () => window.clearInterval(timer);
  }, [intervalMs]);
  return now;
}

export default function MissionView() {
  const m = useMission();
  const now = useNow();
  const online = m.lastTelemetryAt !== null && now - m.lastTelemetryAt < STALE_MS;
  const t = m.telemetry;

  return <motion.div key="Mission ops" className="view-content detail-view" initial={{ opacity: 0, y: 14 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -10 }} transition={{ duration: 0.25 }}>
    <div className="detail-heading"><p className="eyebrow">MISSION CONTROL <span>/</span> LIVE OPERATIONS</p><h1>Every orbit,<br /><em>every decision.</em></h1><p>Live telemetry from the simulated spacecraft. Each capture runs through the DVM and the FDIR watchdog, then the decision policy and the link model.</p></div>

    {m.load.status === "loading" && <div className="panel mo-empty"><span>◌</span><p>Connecting to mission control…</p></div>}
    {m.load.status === "error" && <div className="hv-error" role="alert"><b>Mission service unavailable</b><p>{m.load.message}</p></div>}
    {m.load.status === "ready" && !t && <Offline />}
    {m.load.status === "ready" && t && <>
      <StatusStrip t={t} online={online} streaming={m.streaming} ageS={m.lastTelemetryAt ? (now - m.lastTelemetryAt) / 1000 : null} />
      <div className="mo-grid mo-three">
        <PowerPanel t={t} history={m.history} reserve={m.reserve} />
        <SpacecraftPanel t={t} />
        <ContactPanel t={t} />
      </div>
      <div className="mo-grid mo-two">
        <ProductsPanel t={t} />
        <CommandsPanel commands={m.commands} disabled={!online} />
      </div>
      <div className="mo-grid mo-two">
        <DownlinkPanel downlinks={m.downlinks} />
        <FeedPanel events={m.events} />
      </div>
    </>}
  </motion.div>;
}

function Offline() {
  return <div className="panel mo-empty"><span>◉</span><p><b>No telemetry yet.</b> Start the simulator to bring the spacecraft online:</p><code>cd simulator &amp;&amp; python -m satsim --scenario nominal_day --speed 60</code></div>;
}

function StatusStrip({ t, online, streaming, ageS }: { t: Telemetry; online: boolean; streaming: boolean; ageS: number | null }) {
  const sim = new Date(t.sim_time);
  return <section className="panel mo-strip">
    <div><span>SPACECRAFT</span><strong>{t.sat_id}</strong></div>
    <div><span>LINK</span><strong className={online ? "ok" : "bad"}><i className={`mo-dot ${online ? "ok" : "bad"}`} />{online ? "LIVE" : `SILENT ${ageS ? Math.round(ageS) + " s" : ""}`}{!streaming && online ? " · reconnecting" : ""}</strong></div>
    <div><span>MODE</span><strong className={`mo-mode ${t.mode.toLowerCase()}`}>{label(t.mode)}</strong></div>
    <div><span>SIM TIME (UTC)</span><strong>{sim.toISOString().slice(0, 19).replace("T", " ")}</strong></div>
    <div><span>SCENARIO</span><strong>{label(t.scenario)}</strong></div>
    <div><span>SPEED</span><strong>{t.speed >= 1e5 ? "max" : `×${t.speed}`}</strong></div>
  </section>;
}

function PowerPanel({ t, history, reserve }: { t: Telemetry; history: Point[]; reserve: number }) {
  const net = t.power_in_W - t.power_out_W;
  return <section className="panel mo-panel">
    <div className="panel-head"><div><span className="section-index">POWER</span><h2>Battery</h2></div><span className={`link-status ${t.in_eclipse ? "mo-eclipse" : ""}`}>{t.in_eclipse ? "☾ ECLIPSE" : "☀ SUNLIT"}</span></div>
    <div className="mo-big"><strong className={t.battery_pct < reserve ? "bad" : ""}>{t.battery_pct.toFixed(1)}<small>%</small></strong><span>{(t.battery_pct / 100 * t.battery_capacity_Wh).toFixed(1)} / {t.battery_capacity_Wh} Wh</span></div>
    <Meter value={t.battery_pct} max={100} mark={reserve} markLabel={`${reserve}% reserve`} tone={t.battery_pct < reserve ? "bad" : "ok"} />
    <Sparkline points={history} reserve={reserve} />
    <div className="link-stats"><div><span>SOLAR IN</span><strong>{t.power_in_W.toFixed(1)} <small>W</small></strong></div><div><span>LOAD</span><strong>{t.power_out_W.toFixed(1)} <small>W</small></strong></div><div><span>NET</span><strong className={net < 0 ? "bad" : "ok"}>{net > 0 ? "+" : ""}{net.toFixed(1)} <small>W</small></strong></div></div>
    {t.eclipse_change_in_s !== null && <p className="mo-note">{t.in_eclipse ? "Sunrise" : "Eclipse"} in {duration(t.eclipse_change_in_s)} (sim)</p>}
  </section>;
}

function SpacecraftPanel({ t }: { t: Telemetry }) {
  const used = t.storage_used_MB / t.storage_capacity_MB;
  const c = t.counters;
  return <section className="panel mo-panel">
    <div className="panel-head"><div><span className="section-index">BUS &amp; PAYLOAD</span><h2>Spacecraft</h2></div><span className="link-status">{t.position.alt_km} KM SSO</span></div>
    <div className="mo-facts">
      <div><span>POSITION</span><strong>{Math.abs(t.position.lat_deg).toFixed(1)}°{t.position.lat_deg >= 0 ? "N" : "S"} {Math.abs(t.position.lon_deg).toFixed(1)}°{t.position.lon_deg >= 0 ? "E" : "W"}</strong></div>
      <div><span>TEMPERATURE</span><strong>{t.temperature_C.toFixed(1)} °C</strong></div>
      <div><span>IMAGER</span><strong className={t.bands_active < 6 ? "warn" : ""}>{t.bands_active === 6 ? "6 bands" : "4-band VNIR"}</strong></div>
      <div><span>EDGE AI</span><strong className={t.ai_available ? "ok" : "warn"}>{t.ai_available ? "Online" : "Paused"}</strong></div>
    </div>
    <p className="mo-label">Storage {mb(t.storage_used_MB)} of {mb(t.storage_capacity_MB)}</p>
    <Meter value={t.storage_used_MB} max={t.storage_capacity_MB} mark={t.storage_capacity_MB * 0.9} markLabel="90% pressure" tone={used > 0.9 ? "bad" : used > 0.7 ? "warn" : "ok"} />
    <div className="mo-counters">
      <div><b>{c.captured ?? 0}</b><span>captured</span></div>
      <div><b>{c.downlinked ?? 0}</b><span>downlinked</span></div>
      <div><b>{c.dropped ?? 0}</b><span>dropped</span></div>
      <div><b>{c.fdir_events ?? 0}</b><span>FDIR events</span></div>
      <div><b>{c.alerts ?? 0}</b><span>alerts</span></div>
      <div><b>{c.commands_executed ?? 0}</b><span>commands</span></div>
    </div>
  </section>;
}

function ContactPanel({ t }: { t: Telemetry }) {
  const sim = new Date(t.sim_time).getTime();
  const current = t.next_passes.find((p) => new Date(p.aos).getTime() <= sim && sim <= new Date(p.los).getTime());
  const upcoming = t.next_passes.filter((p) => new Date(p.aos).getTime() > sim);
  const next = upcoming[0];
  const transmitting = t.queue.find((q) => q.state === "transmitting");
  return <section className="panel mo-panel">
    <div className="panel-head"><div><span className="section-index">GROUND CONTACT</span><h2>{current ? current.station_name : "Next pass"}</h2></div><span className={`link-status ${current ? "mo-live" : ""}`}>{current ? "● IN CONTACT" : "PREDICTED"}</span></div>
    {current ? <>
      <div className="mo-big"><strong>{duration((new Date(current.los).getTime() - sim) / 1000)}</strong><span>until LOS · max elev. {current.max_elevation_deg}°</span></div>
      <p className="mo-note">{transmitting ? <>Transmitting <b>{transmitting.id}</b> ({mb(transmitting.size_MB)}{transmitting.compression_ratio ? `, ${transmitting.compression_ratio}× compressed` : ""})</> : "Link idle: nothing queued fits this pass."}</p>
    </> : next ? <>
      <div className="mo-big"><strong>{duration((new Date(next.aos).getTime() - sim) / 1000)}</strong><span>until AOS (sim){t.speed < 1e5 ? ` · ≈ ${duration((new Date(next.aos).getTime() - sim) / 1000 / t.speed)} real time` : ""}</span></div>
      <p className="mo-note">{next.station_name} ({next.station_id}) · {Math.round(next.duration_s)} s · max elevation {next.max_elevation_deg}°</p>
    </> : <p className="mo-note">No pass predicted in the planning horizon.</p>}
    <ul className="mo-passes">{upcoming.slice(current ? 0 : 1, 4).map((p) => <li key={p.station_id + p.aos}><span>{new Date(p.aos).toISOString().slice(11, 16)} UTC</span><b>{p.station_name}</b><small>{Math.round(p.duration_s)} s · {p.max_elevation_deg}°</small></li>)}</ul>
  </section>;
}

const STATE_ORDER: Record<string, number> = { transmitting: 0, queued: 1, processing: 2, captured: 3, stored: 4, downlinked: 5, dropped: 6 };

function ProductsPanel({ t }: { t: Telemetry }) {
  const [showDone, setShowDone] = useState(false);
  const items = useMemo(() => t.queue.filter((q) => showDone || !["downlinked", "dropped"].includes(q.state)).sort((a, b) => (STATE_ORDER[a.state] ?? 9) - (STATE_ORDER[b.state] ?? 9)), [t.queue, showDone]);
  return <section className="panel mo-panel">
    <div className="panel-head"><div><span className="section-index">ONBOARD DATA</span><h2>Products &amp; decisions</h2></div><button type="button" className="text-action" onClick={() => setShowDone((v) => !v)}>{showDone ? "Hide finished" : "Show finished"}</button></div>
    {items.length === 0 ? <p className="mo-note">Nothing on board.</p> : <ul className="mo-products">{items.map((q) => <Product key={q.id} q={q} />)}</ul>}
  </section>;
}

function Product({ q }: { q: QueueItem }) {
  const [busy, setBusy] = useState(false);
  const live = !["downlinked", "dropped", "transmitting"].includes(q.state);
  const send = async (type: string) => {
    setBusy(true);
    await sendCommand(type, { item_id: q.id }, `Operator: ${label(type)} ${q.id}`).catch(() => undefined);
    setBusy(false);
  };
  return <li className={q.protected ? "protected" : ""}>
    <div className="mo-pmain">
      <div className="mo-pline"><b>{q.id}</b><span className={`mo-state ${q.state}`}>{q.state}</span>{q.action && <span className={`q-action ${q.action.toLowerCase()}`}>{label(q.action)}</span>}{q.protected && <span className="mo-protected">PROTECTED</span>}</div>
      <small>{label(q.data_class)} · {mb(q.size_MB)}{q.compression_ratio ? ` (${q.compression_ratio}× compressed)` : ""}{q.value === null ? "" : q.action && q.reason?.includes("AI unavailable") ? " · unassessed" : ` · value ${Math.round(q.value)}`}</small>
      {q.reason && <small className="mo-reason">{q.reason}</small>}
    </div>
    {live && <div className="mo-pactions"><button type="button" disabled={busy || q.protected} onClick={() => send("PRIORITIZE_ITEM")} title="Protect and send first">↑</button><button type="button" disabled={busy} onClick={() => send("DELETE_ITEM")} title="Delete onboard">✕</button></div>}
  </li>;
}

async function sendCommand(type: string, params: Record<string, unknown> = {}, reason = "Operator request") {
  const res = await fetch("/api/mission/commands", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ sat_id: SAT_ID, type, params, reason }) });
  const body: unknown = await res.json().catch(() => null);
  if (!res.ok) throw new Error(errorMessage(res.status, body));
  return body as Command;
}

function CommandsPanel({ commands, disabled }: { commands: Command[]; disabled: boolean }) {
  const [error, setError] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const send = useCallback(async (type: string) => {
    setBusy(type);
    setError("");
    try {
      await sendCommand(type);
    } catch (err) {
      setError((err as Error).message);
    }
    setBusy(null);
  }, []);
  return <section className="panel mo-panel">
    <div className="panel-head"><div><span className="section-index">UPLINK</span><h2>Telecommands</h2></div><span className="link-status">{disabled ? "QUEUED UNTIL CONTACT" : "LIVE"}</span></div>
    <div className="mo-cmds">{OPERATOR_COMMANDS.map((c) => <button type="button" key={c.type} disabled={busy !== null} onClick={() => send(c.type)} title={c.hint}><b>{c.label}</b><small>{c.hint}</small></button>)}</div>
    {error && <p className="mo-error" role="alert">{error}</p>}
    <ul className="mo-history">{commands.slice(0, 12).map((c) => <li key={c.id}>
      <span className={`mo-cstatus ${c.status.toLowerCase()}`}>{c.status}</span>
      <div><b>{label(c.type)}</b><small>{c.source === "fdir" ? "FDIR · " : c.source === "operator" ? "Operator · " : ""}{c.result ?? c.reason}</small></div>
    </li>)}</ul>
  </section>;
}

function DownlinkPanel({ downlinks }: { downlinks: DownlinkAttempt[] }) {
  const scored = downlinks.filter((d) => d.outcome?.attempted);
  const correct = scored.filter((d) => (d.plan.decision === "ATTEMPT") === d.outcome!.delivered).length;
  const delivered = scored.filter((d) => d.outcome!.delivered).length;
  return <section className="panel mo-panel">
    <div className="panel-head"><div><span className="section-index">LINK MODEL</span><h2>Predicted vs actual</h2></div><span className="link-status">{scored.length ? `${correct}/${scored.length} CORRECT` : "NO ATTEMPTS YET"}</span></div>
    {scored.length > 0 && <div className="link-stats"><div><span>ATTEMPTS</span><strong>{scored.length}</strong></div><div><span>DELIVERED</span><strong>{delivered}</strong></div><div><span>DEFERRED</span><strong>{downlinks.filter((d) => d.plan.decision === "DEFER").length}</strong></div></div>}
    <ul className="mo-downlinks">{downlinks.slice(0, 12).map((d) => {
      const o = d.outcome;
      const verdict = !o ? "in flight" : !o.attempted ? "deferred" : o.delivered ? "delivered" : label(o.failure_reason);
      const right = o?.attempted ? (d.plan.decision === "ATTEMPT") === o.delivered : null;
      return <li key={d.id}>
        <div className="mo-prob" style={{ ["--p" as string]: d.plan.probability }}><span>{Math.round(d.plan.probability * 100)}%</span></div>
        <div><b>{d.item_id}</b><small>{d.plan.station_id} · {mb(d.plan.size_MB)} · model says {d.plan.decision}</small></div>
        <span className={`mo-verdict ${right === null ? "" : right ? "ok" : "bad"}`}>{right === null ? "" : right ? "✓ " : "✗ "}{verdict}</span>
      </li>;
    })}</ul>
  </section>;
}

function describe(e: MissionEvent): { tone: string; title: string; text: string } {
  const p = e.payload as Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any
  switch (e.kind) {
    case "diagnostic": {
      const h = p.health;
      return { tone: h.severity.toLowerCase(), title: `${p.item_id} · ${label(h.health_status)}`, text: `${h.diagnostic_message} → ${label(h.recommended_action)}` };
    }
    case "decision":
      return { tone: "decision", title: `${p.item_id} · ${label(p.action)}`, text: `${p.rule}: ${p.reason}` };
    case "command":
      return { tone: p.command.status === "REJECTED" ? "warning" : "command", title: `${label(p.command.type)} · ${p.command.status.toLowerCase()}`, text: p.command.result ?? p.command.reason };
    case "downlink":
      return p.phase === "planned"
        ? { tone: "link", title: `${p.item_id} · link model ${p.decision}`, text: `P(delivery) ${(p.probability * 100).toFixed(0)}% at ${p.station_id}, threshold ${(p.threshold * 100).toFixed(0)}%` }
        : { tone: p.delivered ? "nominal" : "warning", title: `${p.item_id} · ${p.attempted ? (p.delivered ? "delivered" : label(p.failure_reason)) : "deferred"}`, text: p.attempted ? `${p.delivered_MB.toFixed(1)} MB in ${Math.round(p.duration_s)} s` : "Held for a later pass" };
    case "alert":
      return { tone: "alert", title: `${p.item_id} · anomaly alert`, text: p.message };
    default:
      return { tone: p.level === "warning" ? "warning" : "log", title: "Onboard log", text: p.message };
  }
}

function FeedPanel({ events }: { events: MissionEvent[] }) {
  const [filter, setFilter] = useState<"all" | "diagnostic" | "decision" | "command">("all");
  const shown = events.filter((e) => filter === "all" || e.kind === filter).slice(-40).reverse();
  return <section className="panel mo-panel">
    <div className="panel-head"><div><span className="section-index">EVENT FEED</span><h2>What just happened</h2></div>
      <div className="mo-filter">{(["all", "diagnostic", "decision", "command"] as const).map((f) => <button type="button" key={f} className={filter === f ? "selected" : ""} onClick={() => setFilter(f)}>{f === "all" ? "All" : label(f) + "s"}</button>)}</div>
    </div>
    <ul className="mo-feed">{shown.map((e) => {
      const d = describe(e);
      return <li key={e.id} className={d.tone}><span className="mo-ftime">{e.ts.slice(11, 19)}</span><div><b>{d.title}</b><small>{d.text}</small></div></li>;
    })}</ul>
  </section>;
}

function Meter({ value, max, mark, markLabel, tone }: { value: number; max: number; mark?: number; markLabel?: string; tone: "ok" | "warn" | "bad" }) {
  const pct = Math.max(0, Math.min(100, (value / max) * 100));
  return <div className="mo-meter" role="meter" aria-valuenow={Math.round(value)} aria-valuemin={0} aria-valuemax={Math.round(max)}>
    <i className={tone} style={{ width: `${pct}%` }} />
    {mark !== undefined && <span style={{ left: `${(mark / max) * 100}%` }} title={markLabel} />}
  </div>;
}

function Sparkline({ points, reserve }: { points: Point[]; reserve: number }) {
  if (points.length < 2) return <div className="mo-spark mo-spark-empty">Battery history appears as telemetry arrives.</div>;
  const w = 300, h = 64;
  const x = (i: number) => (i / (points.length - 1)) * w;
  const y = (v: number) => h - (v / 100) * h;
  const line = points.map((p, i) => `${x(i).toFixed(1)},${y(p.battery_pct).toFixed(1)}`).join(" ");
  const span = (new Date(points[points.length - 1].sim_time).getTime() - new Date(points[0].sim_time).getTime()) / 1000;
  return <figure className="mo-spark">
    <svg viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" aria-label={`Battery over the last ${duration(span)} of simulated time`}>
      {points.map((p, i) => p.in_eclipse && i > 0 ? <rect key={i} x={x(i - 1)} y={0} width={x(i) - x(i - 1) + 0.5} height={h} className="mo-spark-eclipse" /> : null)}
      <line x1={0} x2={w} y1={y(reserve)} y2={y(reserve)} className="mo-spark-reserve" />
      <polyline points={line} className="mo-spark-line" />
    </svg>
    <figcaption>Last {duration(span)} (sim) · shaded = eclipse · dashed = reserve</figcaption>
  </figure>;
}
