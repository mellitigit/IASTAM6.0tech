"use client";

import { useEffect, useState } from "react";
import { AnimatePresence, MotionConfig } from "framer-motion";
import DownlinkView from "./downlink-view";
import HealthView from "./health-view";
import MissionView from "./mission-view";
import QueueView from "./queue-view";

// Only views backed by real backend models are shown.
type View = "Mission ops" | "Satellite health" | "Downlink queue" | "Downlink prediction";

const navigation: { label: View; icon: string; model: string }[] = [
  { label: "Mission ops", icon: "◉", model: "Live simulated spacecraft: DVM, watchdog, decision policy and link model in the loop" },
  { label: "Satellite health", icon: "✚", model: "DVM CNN + FDIR watchdog" },
  { label: "Downlink queue", icon: "▤", model: "Decision policy over DVM + watchdog outputs" },
  { label: "Downlink prediction", icon: "⌁", model: "Link delivery model" },
];

type BackendStatus = "checking" | "online" | "offline";

function useBackendStatus(): BackendStatus {
  const [status, setStatus] = useState<BackendStatus>("checking");
  useEffect(() => {
    let cancelled = false;
    const check = () =>
      fetch("/api/backend-status", { cache: "no-store" })
        .then((res) => !cancelled && setStatus(res.ok ? "online" : "offline"))
        .catch(() => !cancelled && setStatus("offline"));
    check();
    const timer = window.setInterval(check, 30_000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, []);
  return status;
}

export default function Dashboard() {
  const [activeView, setActiveView] = useState<View>("Satellite health");
  const backend = useBackendStatus();

  return <main className="app-shell"><MotionConfig reducedMotion="user">
    <aside className="sidebar">
      <a className="product-brand" href="#" onClick={(event) => { event.preventDefault(); setActiveView("Satellite health"); }}><span className="brand-mark"><i /><i /><i /></span><span>ASTRA<small>MISSION INTELLIGENCE</small></span></a>
      <span className="nav-label dash-nav-label">MODELS</span>
      <nav className="side-nav" aria-label="Mission control views">{navigation.map((item) => <button key={item.label} onClick={() => setActiveView(item.label)} className={activeView === item.label ? "active" : ""} aria-current={activeView === item.label ? "page" : undefined} title={item.model}><span className="nav-icon">{item.icon}</span>{item.label}</button>)}</nav>
      <div className="sidebar-bottom">
        <div className="space-status"><span className="status-orbit">◉</span><span><small>BACKEND API</small><strong className={`backend-${backend}`}><i /> {backend === "checking" ? "CHECKING" : backend === "online" ? "ONLINE" : "OFFLINE"}</strong></span></div>
      </div>
    </aside>
    <section className="workspace">
      <header className="topbar"><div className="breadcrumb"><span>MISSION CONTROL</span><i>/</i><b>{activeView.toUpperCase()}</b></div></header>
      <div className="workspace-scroll"><AnimatePresence mode="wait" initial={false}>{activeView === "Mission ops" ? <MissionView key="Mission ops" /> : activeView === "Satellite health" ? <HealthView key="Satellite health" /> : activeView === "Downlink queue" ? <QueueView key="Downlink queue" /> : <DownlinkView key="Downlink prediction" />}</AnimatePresence></div>
    </section>
  </MotionConfig></main>;
}
