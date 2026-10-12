"use client";

import { useEffect, useState } from "react";
import { motion, useMotionValue, useReducedMotion, useSpring } from "framer-motion";

export default function CursorHalo() {
  const reduced = useReducedMotion();
  const rawX = useMotionValue(-100);
  const rawY = useMotionValue(-100);
  const x = useSpring(rawX, { stiffness: 520, damping: 42, mass: 0.22 });
  const y = useSpring(rawY, { stiffness: 520, damping: 42, mass: 0.22 });
  const [visible, setVisible] = useState(false);
  const [hovering, setHovering] = useState(false);

  useEffect(() => {
    const onMove = (event: PointerEvent) => {
      if (event.pointerType !== "mouse") return;
      rawX.set(event.clientX);
      rawY.set(event.clientY);
      setVisible(true);
      setHovering(Boolean((event.target as HTMLElement | null)?.closest("a, button, [data-cursor='interactive']")));
    };
    const onExit = () => setVisible(false);
    window.addEventListener("pointermove", onMove);
    document.documentElement.addEventListener("pointerleave", onExit);
    return () => {
      window.removeEventListener("pointermove", onMove);
      document.documentElement.removeEventListener("pointerleave", onExit);
    };
  }, [rawX, rawY]);

  return <motion.div aria-hidden="true" className="cursor-halo" style={{ x, y }} animate={{ opacity: visible && !reduced ? 1 : 0, scale: hovering ? 1.5 : 1 }} transition={{ opacity: { duration: 0.18 }, scale: { type: "spring", stiffness: 420, damping: 28 } }}><span /></motion.div>;
}
