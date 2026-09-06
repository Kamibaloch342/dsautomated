import { useEffect, useRef } from "react";

// Scroll-reveal wrapper: fades/slides children in the first time they
// enter the viewport. Respects prefers-reduced-motion via CSS.
//   <Reveal as="section" className="hsec" delay={1}>…</Reveal>
export default function Reveal({ as: Tag = "div", className = "", delay = 0, children, ...rest }) {
  const ref = useRef(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    if (typeof IntersectionObserver === "undefined") {
      el.classList.add("is-visible");
      return;
    }
    const io = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) {
            entry.target.classList.add("is-visible");
            io.unobserve(entry.target);
          }
        });
      },
      { rootMargin: "0px 0px -8% 0px", threshold: 0.05 }
    );
    io.observe(el);
    return () => io.disconnect();
  }, []);

  const delayCls = delay ? ` reveal-d${Math.min(delay, 3)}` : "";
  return (
    <Tag ref={ref} className={`reveal${delayCls} ${className}`.trim()} {...rest}>
      {children}
    </Tag>
  );
}
