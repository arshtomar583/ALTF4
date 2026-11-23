"use client";

import { memo, useEffect, useState } from "react";

import { BEATS, type Beat, type Marker } from "../lib/incident-state";
import { Check, Cross, Pause, Play, Warn } from "./icons";

const WIDTH = 20;
const MIDDLE = 36;
const TOTAL = BEATS * WIDTH;
const STALE_AFTER = 6;

type Point = [number, number];

/** One beat, as offsets from its left edge and the resting line. */
const PASS: Point[] = [[0, 0], [7, 0], [8.5, 4], [10.5, -27], [13, 13], [14.5, 0], [20, 0]];
const FAIL: Point[] = [[0, 0], [4, 0], [7, -15], [10.5, 23], [14, -11], [16.5, 0], [20, 0]];

function points(beat: Beat, x: number): string {
  return (beat.ok ? PASS : FAIL).map(([dx, dy]) => `${x + dx},${MIDDLE + dy}`).join(" ");
}

function Freshness({ probedAt, slot, revision, recorded }: {
  probedAt: number | null;
  slot: string | null;
  revision: string | null;
  recorded: boolean;
}) {
  const [now, setNow] = useState<number | null>(null);
  useEffect(() => {
    const tick = () => setNow(Date.now());
    tick();
    const timer = window.setInterval(tick, 1000);
    return () => window.clearInterval(timer);
  }, []);
  const age = probedAt === null || now === null ? null : Math.max(0, Math.round((now - probedAt) / 1000));
  const stale = !recorded && age !== null && age > STALE_AFTER;
  return (
    <p className="tnum text-meta whitespace-nowrap text-text-2">
      {recorded ? (
        <span className="font-semibold">NOT LIVE</span>
      ) : stale ? (
        <span className="font-semibold text-fixing"><Warn /> STALE</span>
      ) : (
        <span className="font-semibold text-text">{age === null ? "WAITING" : "LIVE"}</span>
      )}
      {!recorded && age !== null ? ` · updated ${age < 100 ? `${age}s` : `${Math.floor(age / 60)}m`} ago` : ""}
      {` · slot ${slot?.toUpperCase() ?? "—"} @ ${revision?.slice(0, 7) ?? "—"}`}
    </p>
  );
}

export const Heartbeat = memo(function Heartbeat({ beats: current, markers: currentMarkers, probedAt, slot, revision, recorded }: {
  beats: Beat[];
  markers: Marker[];
  probedAt: number | null;
  slot: string | null;
  revision: string | null;
  recorded: boolean;
}) {
  const [held, setHeld] = useState<{ beats: Beat[]; markers: Marker[] } | null>(null);
  const beats = held?.beats ?? current;
  const markers = held?.markers ?? currentMarkers;
  const offset = BEATS - beats.length;
  const latest = beats.at(-1);
  return (
    <section aria-label="Checkout heartbeat" className="relative grid h-16 shrink-0 grid-cols-[minmax(0,1fr)_auto] border-b border-line">
      <div className="@container relative min-w-0 overflow-hidden">
        <svg
          role="img"
          aria-label={latest ? `Latest checkout ${latest.ok ? "passed" : "failed"}` : "Waiting for the first checkout"}
          viewBox={`0 0 ${TOTAL} 76`}
          preserveAspectRatio="none"
          className="absolute inset-0 h-full w-full"
        >
          <g key={held ? "held" : latest?.id} className={held ? "" : "ecg-step"}>
            <polyline points={`-${WIDTH},${MIDDLE} ${offset * WIDTH},${MIDDLE}`} fill="none" stroke="var(--line)" strokeWidth="2" vectorEffect="non-scaling-stroke" />
            {beats.map((beat, index) => {
              const x = (offset + index) * WIDTH;
              return (
                <g key={beat.id} fill="none" strokeLinejoin="round" strokeWidth={beat.ok ? 1.75 : 2.25}>
                  <polyline points={points(beat, x)} stroke={beat.ok ? "var(--text-2)" : "var(--down)"} vectorEffect="non-scaling-stroke" />
                  {beat.outage ? (
                    <polyline points={`${x},${MIDDLE} ${x + WIDTH},${MIDDLE}`} stroke="var(--down)" strokeWidth="2.25" vectorEffect="non-scaling-stroke" />
                  ) : null}
                </g>
              );
            })}
          </g>
        </svg>
        {markers.map((marker, index) => {
          const position = beats.findIndex((beat) => beat.id > marker.id);
          const column = offset + (position === -1 ? beats.length : position);
          // OPENED and RESTORED are often one beat apart, so labels take turns between two rows.
          const row = index % 2 === 0 ? "top-0.5" : "bottom-0.5";
          return (
            <div key={marker.id} className="pointer-events-none absolute inset-y-0 left-0 border-l border-text-3" style={{ transform: `translateX(${(column / BEATS) * 100}cqw)` }}>
              <span className={`label absolute ${row} bg-bg px-1 text-text-2 ${column > BEATS - 6 ? "right-1" : "left-1"}`}>{marker.label}</span>
            </div>
          );
        })}
      </div>
      <div className="flex items-center gap-3 border-l border-line pr-3 pl-4">
        <div>
          <Freshness probedAt={probedAt} slot={slot} revision={revision} recorded={recorded} />
          <p className="text-meta whitespace-nowrap text-text-2">
            {latest ? (
              <>
                {latest.ok ? <Check /> : <Cross className="h-[1em] w-[1em] text-down" />} {latest.ok ? "Passed" : "Failed"} · {latest.discount ? "SAVE10 cart" : "plain cart"}
                {held ? " · paused" : ""}
              </>
            ) : (
              "Waiting for the first checkout"
            )}
          </p>
        </div>
        <button
          type="button"
          aria-pressed={held !== null}
          aria-label={held ? "Resume the heartbeat" : "Pause the heartbeat"}
          onClick={() => setHeld(held ? null : { beats: current, markers: currentMarkers })}
          className="control grid h-11 w-11 place-items-center rounded-control border border-line text-text"
        >
          {held ? <Play /> : <Pause />}
        </button>
      </div>
    </section>
  );
});
