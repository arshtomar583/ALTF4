"use client";

import { memo, useEffect, useRef, useState } from "react";

/** The line of reasoning from both findings into the decision. */
export const Thread = memo(function Thread({ joined }: { joined: boolean }) {
  const box = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(0);
  useEffect(() => {
    if (!box.current) return;
    const observer = new ResizeObserver(([entry]) => setWidth(Math.round(entry.contentRect.width)));
    observer.observe(box.current);
    return () => observer.disconnect();
  }, []);
  const middle = width / 2;
  return (
    <div ref={box} aria-hidden className="hidden h-5 xl:block">
      {joined && width > 0 ? (
        <svg viewBox={`0 0 ${width} 20`} className="h-full w-full overflow-visible">
          {[width / 4, (width * 3) / 4].map((x) => (
            <path
              key={x}
              className="thread"
              d={`M${x} 0V5Q${x} 10 ${x + (x < middle ? 6 : -6)} 10H${middle + (x < middle ? -6 : 6)}Q${middle} 10 ${middle} 15V20`}
              fill="none"
              stroke="var(--agent-coordinator)"
              strokeWidth="1.5"
              pathLength={1}
              strokeDasharray={1}
            />
          ))}
        </svg>
      ) : null}
    </div>
  );
});
