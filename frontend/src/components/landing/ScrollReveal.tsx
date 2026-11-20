"use client";

import { useEffect } from "react";

// Progressive enhancement for the landing page. Server-rendered HTML is fully visible. After
// hydration, only elements that are still BELOW the viewport are marked hidden, then revealed once
// as they scroll in. Nothing is hidden for reduced-motion users or browsers without
// IntersectionObserver, so content is never lost.
export default function ScrollReveal() {
  useEffect(() => {
    if (!("IntersectionObserver" in window)) return;
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;

    const targets = Array.from(document.querySelectorAll<HTMLElement>("[data-reveal]"));
    const io = new IntersectionObserver(
      (entries) => {
        for (const e of entries) {
          if (!e.isIntersecting) continue;
          (e.target as HTMLElement).dataset.reveal = "shown";
          io.unobserve(e.target);
        }
      },
      { rootMargin: "0px 0px -8% 0px", threshold: 0.08 },
    );
    for (const el of targets) {
      if (el.getBoundingClientRect().top >= window.innerHeight) {
        el.dataset.reveal = "hidden";
        io.observe(el);
      }
    }
    return () => {
      io.disconnect();
      for (const el of targets) if (el.dataset.reveal === "hidden") el.dataset.reveal = "shown";
    };
  }, []);

  return null;
}
