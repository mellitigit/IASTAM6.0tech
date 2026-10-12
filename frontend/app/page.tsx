"use client";

import Image from "next/image";
import Link from "next/link";
import { AnimatePresence, animate, motion, useMotionValue, useReducedMotion, useSpring, useTransform, type Variants } from "framer-motion";
import { useCallback, useEffect, useRef, useState } from "react";

type Dir = 1 | -1;

const steps = [
  { number: "01", title: "Sense", copy: "Camera and sensor readings meet weather, ground-station and communications context." },
  { number: "02", title: "Score", copy: "Small AI models evaluate science value, link quality, compression, resources and anomalies." },
  { number: "03", title: "Decide", copy: "Safety rules protect the spacecraft while the engine selects the best action for every data item." },
  { number: "04", title: "Deliver", copy: "The queue prioritizes urgent alerts, high-value data and the next reliable path to Earth." },
];

const actions = ["Process", "Store", "Compress", "Send", "Defer"];

const screens = [
  { id: "hero", hash: "", label: "Astra" },
  { id: "solution", hash: "solution", label: "The solution" },
  { id: "how", hash: "how-it-works", label: "How it works" },
  { id: "join", hash: "join", label: "Join" },
] as const;

type ScreenId = (typeof screens)[number]["id"];

const ease = [0.22, 1, 0.36, 1] as const;
const enter = { duration: 0.9, ease };
const leave = { duration: 0.75, ease };
const LOCK_MS = 950;

// One transition per screen, chosen for the move it makes:
// hero <-> solution zooms through the spacecraft, solution <-> how slides sideways,
// how <-> join wipes up like a curtain.
const screenVariants: Record<ScreenId, Variants> = {
  // The hero's own zoom is driven by scroll (see heroProgress); the screen itself only fades.
  hero: {
    initial: { opacity: 0 },
    animate: { opacity: 1, transition: { duration: 0.6, ease } },
    exit: { opacity: 0, transition: { duration: 0.6, ease } },
  },
  solution: {
    initial: (d: Dir) => (d > 0 ? { opacity: 0, scale: 1.35, filter: "blur(8px)" } : { opacity: 0, x: "-12vw" }),
    animate: { opacity: 1, x: 0, scale: 1, filter: "blur(0px)", transition: enter },
    exit: (d: Dir) => (d > 0 ? { opacity: 0, x: "-12vw", transition: leave } : { opacity: 0, scale: 1.3, filter: "blur(8px)", transition: leave }),
  },
  how: {
    initial: (d: Dir) => (d > 0 ? { opacity: 0, x: "12vw" } : { opacity: 0, y: "-8vh" }),
    animate: { opacity: 1, x: 0, y: 0, transition: enter },
    exit: (d: Dir) => (d > 0 ? { opacity: 0, y: "-8vh", transition: leave } : { opacity: 0, x: "12vw", transition: leave }),
  },
  join: {
    initial: { clipPath: "inset(100% 0% 0% 0%)" },
    animate: { clipPath: "inset(0% 0% 0% 0%)", transition: { duration: 1, ease } },
    exit: { clipPath: "inset(100% 0% 0% 0%)", transition: { duration: 0.85, ease } },
  },
};

const fadeVariants: Variants = {
  initial: { opacity: 0 },
  animate: { opacity: 1, transition: { duration: 0.3 } },
  exit: { opacity: 0, transition: { duration: 0.2 } },
};

// Children inherit the screen's variant labels, so each one reveals shortly after its screen arrives.
const rise = (i = 0): Variants => ({
  initial: { opacity: 0, y: 26 },
  animate: { opacity: 1, y: 0, transition: { delay: 0.12 + i * 0.09, duration: 0.7, ease } },
});

function FloatingSpacecraft() {
  const reduceMotion = useReducedMotion();
  const targetX = useMotionValue(0);
  const targetY = useMotionValue(0);
  const x = useSpring(targetX, { stiffness: 72, damping: 21, mass: 0.55 });
  const y = useSpring(targetY, { stiffness: 72, damping: 21, mass: 0.55 });
  const rotateY = useTransform(x, [-16, 16], [-2.2, 2.2]);
  const rotateX = useTransform(y, [-12, 12], [1.8, -1.8]);

  const followPointer = useCallback((event: React.PointerEvent<HTMLDivElement>) => {
    if (event.pointerType !== "mouse" || reduceMotion) return;
    const bounds = event.currentTarget.getBoundingClientRect();
    targetX.set(((event.clientX - bounds.left - bounds.width / 2) / bounds.width) * 32);
    targetY.set(((event.clientY - bounds.top - bounds.height / 2) / bounds.height) * 24);
  }, [reduceMotion, targetX, targetY]);

  const resetPosition = useCallback(() => {
    targetX.set(0);
    targetY.set(0);
  }, [targetX, targetY]);

  return <motion.div className="sc-hero-pointer-space" data-cursor="interactive" onPointerMove={followPointer} onPointerLeave={resetPosition} style={{ x, y, rotateX, rotateY, transformPerspective: 900 }}>
    <div className="sc-hero-float"><Image src="/images/astra-spacecraft.png" alt="Astronaut seated on the Astra satellite" fill priority sizes="(max-width: 900px) 94vw, 900px" /></div>
  </motion.div>;
}

export default function Home() {
  const reduceMotion = useReducedMotion();
  const [index, setIndex] = useState(0);
  const [dir, setDir] = useState<Dir>(1);
  const [boot, setBoot] = useState(0);
  const indexRef = useRef(0);
  const locked = useRef(false);
  const reduceRef = useRef(false);
  useEffect(() => { reduceRef.current = Boolean(reduceMotion); }, [reduceMotion]);

  // Scroll-driven zoom on the hero: 0 = resting, 1 = fully zoomed in, which hands over to the solution screen.
  const heroRaw = useMotionValue(0);
  const heroProgress = useSpring(heroRaw, { stiffness: 130, damping: 26, mass: 0.5 });
  const artScale = useTransform(heroProgress, [0, 1], [1, 4.2]);
  const artOpacity = useTransform(heroProgress, [0, 0.62, 0.97], [1, 1, 0]);
  const titleScale = useTransform(heroProgress, [0, 0.6], [1, 1.7]);
  const titleOpacity = useTransform(heroProgress, [0, 0.4], [1, 0]);

  const commit = useCallback((to: number) => {
    locked.current = true;
    setDir(to > indexRef.current ? 1 : -1);
    indexRef.current = to;
    setIndex(to);
    if (to === 0) heroRaw.set(0); // the zoom winds back out as the hero returns
    window.history.replaceState(null, "", screens[to].hash ? `#${screens[to].hash}` : window.location.pathname);
    window.setTimeout(() => { locked.current = false; }, LOCK_MS);
  }, [heroRaw]);

  // Leaving the hero always plays the zoom first (continuing from wherever scrolling left it).
  const go = useCallback((to: number) => {
    if (locked.current || to < 0 || to >= screens.length || to === indexRef.current) return;
    if (indexRef.current === 0 && !reduceRef.current) {
      locked.current = true;
      const remaining = 1 - heroRaw.get();
      animate(heroRaw, 1, { duration: 0.25 + remaining * 0.85, ease }).then(() => {
        locked.current = false;
        commit(to);
      });
      return;
    }
    commit(to);
  }, [commit, heroRaw]);

  // Open straight onto a screen when the URL carries its hash.
  useEffect(() => {
    const start = screens.findIndex((s) => s.hash && s.hash === window.location.hash.slice(1));
    if (start <= 0) return;
    const t = window.setTimeout(() => {
      indexRef.current = start;
      setIndex(start);
      setBoot(1); // remount the presence group so the deep-linked screen appears without animating from the hero
    }, 0);
    return () => window.clearTimeout(t);
  }, []);

  useEffect(() => {
    let lastWheel = 0;
    let lastTrigger = 0;
    let acc = 0;
    let consumed = false;
    let touchY: number | null = null;
    let settle: number | undefined;

    // On the hero, scrolling scrubs the zoom directly; once the scroll pauses it completes or eases back.
    const scrubHero = (delta: number) => {
      if (locked.current) return;
      const next = Math.min(1, Math.max(0, heroRaw.get() + delta / 600));
      heroRaw.set(next);
      window.clearTimeout(settle);
      if (next >= 1) { go(1); return; }
      settle = window.setTimeout(() => {
        if (indexRef.current !== 0 || locked.current) return;
        if (heroRaw.get() > 0.08) go(1);
        else animate(heroRaw, 0, { duration: 0.4, ease });
      }, 170);
    };

    // A scroll gesture is a burst of wheel events. Add them up, move one screen once the
    // total passes a threshold, then ignore the rest of that burst (trackpad inertia included).
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const now = performance.now();
      const gap = now - lastWheel;
      const delta = e.deltaMode === 1 ? e.deltaY * 33 : e.deltaY;

      if (indexRef.current === 0 && !reduceRef.current) {
        lastWheel = now;
        scrubHero(delta);
        return;
      }

      lastWheel = now;
      if (gap > 150) { acc = 0; consumed = false; }
      // Still scrolling steadily after the transition ended: treat a firm push as a new gesture.
      if (consumed && now - lastTrigger > 1400 && Math.abs(delta) >= 40) { acc = 0; consumed = false; }
      if (consumed) return;

      acc += delta;
      if (Math.abs(acc) < 30) return;
      consumed = true;
      lastTrigger = now;
      go(indexRef.current + (acc > 0 ? 1 : -1));
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.altKey || e.ctrlKey || e.metaKey) return;
      const next = ["ArrowDown", "PageDown", " "];
      const prev = ["ArrowUp", "PageUp"];
      if (next.includes(e.key)) { e.preventDefault(); go(indexRef.current + 1); }
      else if (prev.includes(e.key)) { e.preventDefault(); go(indexRef.current - 1); }
      else if (e.key === "Home") go(0);
      else if (e.key === "End") go(screens.length - 1);
    };
    const onTouchStart = (e: TouchEvent) => { touchY = e.touches[0].clientY; };
    const onTouchEnd = (e: TouchEvent) => {
      if (touchY === null) return;
      const delta = touchY - e.changedTouches[0].clientY;
      touchY = null;
      if (Math.abs(delta) > 50) go(indexRef.current + (delta > 0 ? 1 : -1));
    };

    window.addEventListener("wheel", onWheel, { passive: false });
    window.addEventListener("keydown", onKey);
    window.addEventListener("touchstart", onTouchStart, { passive: true });
    window.addEventListener("touchend", onTouchEnd, { passive: true });
    return () => {
      window.clearTimeout(settle);
      window.removeEventListener("wheel", onWheel);
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("touchstart", onTouchStart);
      window.removeEventListener("touchend", onTouchEnd);
    };
  }, [go, heroRaw]);

  const screen = screens[index];
  const variants = reduceMotion ? fadeVariants : screenVariants[screen.id];

  return <main className={`deck${index > 0 ? " not-hero" : ""}${screen.id === "join" ? " is-last" : ""}`}>
    <AnimatePresence key={boot} initial={false} custom={dir}>
      <motion.section
        key={screen.id}
        className={`sc sc-${screen.id}`}
        aria-label={screen.label}
        custom={dir}
        variants={variants}
        initial="initial"
        animate="animate"
        exit="exit"
        style={{ zIndex: index + 1 }}
      >
        {screen.id === "hero" && <>
          <motion.div className="sc-hero-title" style={{ opacity: titleOpacity, scale: titleScale }}><h1>ASTRA</h1></motion.div>
          <motion.div className="sc-hero-art" style={{ scale: artScale, opacity: artOpacity, transformOrigin: "50% 24%" }}><FloatingSpacecraft /></motion.div>
        </>}

        {screen.id === "solution" && <div className="sc-inner sc-solution-grid">
          <div className="sc-copy">
            <motion.p variants={rise(0)} className="sc-eyebrow"><i /> THE SOLUTION</motion.p>
            <motion.h2 variants={rise(1)}>The spacecraft<br /><em>that knows what matters.</em></motion.h2>
            <motion.p variants={rise(2)} className="sc-lede">Astra turns raw observations into safe, explainable decisions. It looks at every new image and sensor reading, balances science value against battery, storage, temperature and radio conditions, then chooses the best next action.</motion.p>
            <motion.ul variants={rise(3)} className="sc-chips" aria-label="Possible decisions">{actions.map((a) => <li key={a}>{a}</li>)}</motion.ul>
            <motion.div variants={rise(4)}><button type="button" className="sc-next" onClick={() => go(2)}>See how it works <span>↓</span></button></motion.div>
          </div>
          <motion.div variants={rise(1)} className="sc-solution-art"><Image src="/images/control-room.jpg" alt="An operator monitoring satellite imagery in a mission control room" fill priority sizes="(max-width: 900px) 90vw, 46vw" /></motion.div>
        </div>}

        {screen.id === "how" && <div className="sc-inner sc-how-col">
          <div className="sc-how-head">
            <motion.h2 variants={rise(1)}>One system.<br /><em>Clear choices.</em></motion.h2>
            <motion.p variants={rise(2)}>Several focused models. One safety-first decision engine. A continuous learning loop between orbit and Earth.</motion.p>
          </div>
          <div className="sc-steps">{steps.map((step, i) => <motion.article key={step.number} variants={rise(3 + i)}><span className="sc-step-no">{step.number}</span><i /><h3>{step.title}</h3><p>{step.copy}</p></motion.article>)}</div>
          <motion.div variants={rise(7)} className="sc-strip"><span>CAMERA + SENSORS</span><b>→</b><span>AI MODELS</span><b>→</b><span>DECISION ENGINE</span><b>→</b><span>GROUND DASHBOARD</span><b>→</b><span>LEARNING LOOP</span></motion.div>
        </div>}

        {screen.id === "join" && <div className="sc-inner sc-join-col">
          <div className="sc-join-body">
            <div>
              <motion.p variants={rise(1)} className="sc-eyebrow"><i /> JOIN ASTRA</motion.p>
              <motion.h2 variants={rise(2)}>See the whole<br /><em>picture.</em></motion.h2>
            </div>
            <div className="sc-join-copy">
              <motion.p variants={rise(3)}>Monitor spacecraft health, understand every data decision, plan the next downlink and keep your mission moving with confidence.</motion.p>
              <motion.div variants={rise(4)} className="sc-join-buttons"><Link className="sc-primary" href="/signup">Sign up <span>↗</span></Link><Link className="sc-secondary" href="/login">Log in to mission control</Link></motion.div>
            </div>
          </div>
          <motion.div variants={rise(5)} className="sc-join-foot"><span className="sc-foot-brand">◉ ASTRA<small>®</small></span><span>SAFETY-FIRST AUTONOMY · OPERATOR CONTROL · EARTH + ORBIT</span><span>© ASTRA MISSION SYSTEMS</span></motion.div>
        </div>}
      </motion.section>
    </AnimatePresence>

    <button type="button" className="deck-brand" onClick={() => go(0)} aria-label="Back to the start" tabIndex={index > 0 ? 0 : -1}><span>◉</span> ASTRA<small>®</small></button>
    <button type="button" className="deck-next" onClick={() => go(index < screens.length - 1 ? index + 1 : 0)} aria-label={index < screens.length - 1 ? `Next: ${screens[index + 1].label}` : "Back to the start"}>
      <em>{index < screens.length - 1 ? screens[index + 1].label : "Back to start"}</em>
      <span aria-hidden="true">{index < screens.length - 1 ? "↓" : "↑"}</span>
    </button>
    <nav className="deck-dots" aria-label="Page sections">
      {screens.map((s, i) => <button key={s.id} type="button" className={i === index ? "active" : ""} aria-label={s.label} aria-current={i === index ? "step" : undefined} onClick={() => go(i)}><em>{s.label}</em><i /></button>)}
    </nav>
  </main>;
}
