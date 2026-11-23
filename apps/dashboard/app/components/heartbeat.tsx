import { BEATS, type Beat, type Marker } from "../lib/incident-state";

const WIDTH = 20;
const MIDDLE = 40;
const TOTAL = BEATS * WIDTH;

function shape(ok: boolean, x: number): string {
  return ok
    ? `M${x} ${MIDDLE}h6l2 4l3 -30l3 34l2 -8h4`
    : `M${x} ${MIDDLE}h3l2 14l3 -32l2 40l3 -34l2 20l2 -8h3`;
}

export function Heartbeat({ beats, markers }: { beats: Beat[]; markers: Marker[] }) {
  const offset = BEATS - beats.length;
  const latest = beats.at(-1);
  const summary = latest ? `Latest checkout ${latest.ok ? "passed" : "failed"}` : "Waiting for the first checkout";
  return (
    <section aria-label="Checkout heartbeat" className="relative h-20 shrink-0 border-b border-line">
      <svg role="img" aria-label={summary} viewBox={`0 0 ${TOTAL} 80`} preserveAspectRatio="none" className="h-full w-full">
        <path d={`M0 ${MIDDLE}H${offset * WIDTH}`} fill="none" stroke="var(--color-line)" strokeWidth="2" vectorEffect="non-scaling-stroke" />
        {beats.map((beat, index) => (
          <path
            key={beat.id}
            d={shape(beat.ok, (offset + index) * WIDTH)}
            fill="none"
            stroke={beat.ok ? "var(--color-good)" : "var(--color-bad)"}
            strokeWidth={beat.ok ? 2 : 2.5}
            strokeLinejoin="round"
            vectorEffect="non-scaling-stroke"
          />
        ))}
        {beats.map((beat, index) =>
          beat.outage ? (
            <path
              key={beat.id}
              d={`M${(offset + index) * WIDTH} ${MIDDLE}h${WIDTH}`}
              stroke="var(--color-bad)"
              strokeWidth="2.5"
              vectorEffect="non-scaling-stroke"
            />
          ) : null,
        )}
      </svg>
      {markers.map((marker, index) => {
        const position = beats.findIndex((beat) => beat.id > marker.id);
        const column = offset + (position === -1 ? beats.length : position);
        const left = `${(column / BEATS) * 100}%`;
        // Opened and restored are often one beat apart, so labels take turns between two rows.
        const row = index % 2 === 0 ? "top-1" : "bottom-1";
        return (
          <div key={marker.id} className="pointer-events-none absolute inset-y-0 border-l border-dashed border-dim" style={{ left }}>
            <span className={`absolute ${row} rounded bg-ground px-1 text-sm whitespace-nowrap ${column > BEATS - 5 ? "right-1" : "left-1"}`}>
              {marker.label}
            </span>
          </div>
        );
      })}
      <p className="absolute bottom-1 left-5 rounded bg-ground px-1 text-sm text-dim">
        {latest ? `${latest.ok ? "✓ passed" : "✗ failed"} · ${latest.discount ? "SAVE10 cart" : "plain cart"}` : "Waiting for a checkout"}
      </p>
    </section>
  );
}
