"use client";

import { useEffect, useRef, useState } from "react";

/** True when the visitor asked their OS to reduce motion. */
export function useReducedMotion() {
  const [reduce, setReduce] = useState(false);
  useEffect(() => {
    const mq = window.matchMedia("(prefers-reduced-motion: reduce)");
    const update = () => setReduce(mq.matches);
    const h = setTimeout(update, 0);
    mq.addEventListener("change", update);
    return () => {
      clearTimeout(h);
      mq.removeEventListener("change", update);
    };
  }, []);
  return reduce;
}

/** Becomes true once the element scrolls into view (and stays true). */
export function useInView<T extends Element>(threshold = 0.25) {
  const ref = useRef<T>(null);
  const [seen, setSeen] = useState(false);
  useEffect(() => {
    const el = ref.current;
    if (!el || seen) return;
    const io = new IntersectionObserver(
      ([e]) => {
        if (e.isIntersecting) {
          setSeen(true);
          io.disconnect();
        }
      },
      { threshold },
    );
    io.observe(el);
    return () => io.disconnect();
  }, [seen, threshold]);
  return [ref, seen] as const;
}

/** Fades and lifts its children in as they enter the viewport. */
export function Reveal({
  children,
  delay = 0,
  className = "",
}: {
  children: React.ReactNode;
  delay?: number;
  className?: string;
}) {
  const [ref, seen] = useInView<HTMLDivElement>(0.15);
  return (
    <div ref={ref} className={`reveal ${seen ? "in" : ""} ${className}`} style={{ transitionDelay: `${delay}ms` }}>
      {children}
    </div>
  );
}

/**
 * 0 → 1 as a (tall) section scrolls past: 0 when its top reaches the top of the viewport,
 * 1 when its bottom reaches the bottom. Drives sticky scroll stories.
 */
export function useScrollProgress<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  const [p, setP] = useState(0);
  useEffect(() => {
    let raf = 0;
    const measure = () => {
      raf = 0;
      const el = ref.current;
      if (!el) return;
      const r = el.getBoundingClientRect();
      const span = r.height - window.innerHeight;
      setP(span <= 0 ? 1 : Math.min(1, Math.max(0, -r.top / span)));
    };
    const onScroll = () => {
      if (!raf) raf = requestAnimationFrame(measure);
    };
    measure();
    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", onScroll);
    return () => {
      cancelAnimationFrame(raf);
      window.removeEventListener("scroll", onScroll);
      window.removeEventListener("resize", onScroll);
    };
  }, []);
  return [ref, p] as const;
}

/** Counts up to `to` once visible. */
export function CountUp({ to, duration = 1200, suffix = "" }: { to: number; duration?: number; suffix?: string }) {
  const [ref, seen] = useInView<HTMLSpanElement>(0.6);
  const reduce = useReducedMotion();
  const [v, setV] = useState(0);
  useEffect(() => {
    if (!seen) return;
    if (reduce) {
      const h = setTimeout(() => setV(to), 0);
      return () => clearTimeout(h);
    }
    let raf = 0;
    const t0 = performance.now();
    const tick = (t: number) => {
      const k = Math.min(1, (t - t0) / duration);
      setV(Math.round(to * (1 - Math.pow(1 - k, 3))));
      if (k < 1) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [seen, to, duration, reduce]);
  return (
    <span ref={ref} className="tabular">
      {v.toLocaleString()}
      {suffix}
    </span>
  );
}

/** Maps overall progress p onto a sub-range [a, b] → 0..1 (clamped). */
export const stage = (p: number, a: number, b: number) => Math.min(1, Math.max(0, (p - a) / (b - a)));
