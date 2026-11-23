"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { Decision } from "./components/decision";
import { Empty } from "./components/empty";
import { Feed } from "./components/feed";
import { Findings } from "./components/findings";
import { Heartbeat } from "./components/heartbeat";
import { ConfirmBreak, Help } from "./components/help";
import { Warn } from "./components/icons";
import { Rail } from "./components/rail";
import { RecoveryBanner } from "./components/recovery-banner";
import { Drawer } from "./components/report-drawer";
import { StatusStrip } from "./components/status-strip";
import { Thread } from "./components/thread";
import { Verification } from "./components/verification";
import type { PhoenixEvent } from "./lib/events";
import type { Phase } from "./lib/incident-state";
import { useControls } from "./lib/use-controls";
import { useIncident } from "./lib/use-incident";

const SAID: Partial<Record<Phase, string>> = {
  down: "Checkout down",
  fixing: "Service restored",
  verified: "Fix verified",
  releasing: "Releasing",
  recovered: "Recovered",
  escalated: "Escalated to a human",
};

type Spot = { panel: string; actor: string } | null;

/** The story panel that the newest event belongs to. */
function spotlight(feed: PhoenixEvent[]): Spot {
  for (const { type, actor } of feed) {
    if (actor === "code_investigator") return { panel: "code", actor };
    if (actor === "runtime_investigator") return { panel: "runtime", actor };
    if (type === "decision.made" || type === "incident.escalated") return { panel: "decision", actor };
    if (actor === "coordinator" && type.startsWith("agent.")) return { panel: "decision", actor };
    if (["repair", "verifier", "chaos", "release"].includes(actor) && type !== "chaos.break") return { panel: "verification", actor };
    if (type === "incident.opened") return null;
  }
  return null;
}

type Open = "report" | "pack" | "help" | "break" | null;

export function WarRoom() {
  const { state, connection, incidentOpen, banner, recorded } = useIncident();
  const controls = useControls(state, incidentOpen, recorded);
  const [director, setDirector] = useState(true);
  const [open, setOpen] = useState<Open>(null);
  const close = useCallback(() => setOpen(null), []);
  const showReport = useCallback(() => setOpen("report"), []);
  const showPack = useCallback(() => setOpen("pack"), []);
  const showHelp = useCallback(() => setOpen("help"), []);

  const { phase } = state;
  const working = phase === "fixing";
  const staged = director && !state.closed && ["fixing", "verified", "releasing"].includes(phase);
  const spot = useMemo(() => (staged ? spotlight(state.feed) : null), [staged, state.feed]);
  const focus = spot?.panel ?? null;

  // The listener is attached once and always sees the newest state.
  const keys = useRef<(key: string) => void>(() => undefined);
  useEffect(() => {
    keys.current = (key) => {
      if (key === "b" && controls.breakIt.enabled) setOpen("break");
      if (key === "a" && controls.approve.enabled) controls.approve.run();
      if (key === "d") setDirector((before) => !before);
      if (key === "r" && state.report) setOpen("report");
      if (key === "?") setOpen("help");
    };
  });
  useEffect(() => {
    const listen = (event: KeyboardEvent) => {
      const typing = event.target instanceof HTMLElement && event.target.closest("input, textarea, [contenteditable]");
      if (event.repeat || event.ctrlKey || event.metaKey || event.altKey || typing || document.querySelector("dialog[open]")) return;
      keys.current(event.key.toLowerCase());
    };
    window.addEventListener("keydown", listen);
    return () => window.removeEventListener("keydown", listen);
  }, []);

  const closed = state.feed.find((event) => event.type === "incident.closed");
  const idle = phase === "healthy" && state.incidentId === null;
  return (
    <div className="relative flex h-dvh min-h-[700px] min-w-[1180px] flex-col">
      {banner ? (
        <p className="shrink-0 bg-text py-1 text-center text-title leading-snug font-semibold text-bg">
          <Warn /> {banner}
        </p>
      ) : null}
      <p aria-live="polite" aria-atomic="true" className="sr-only">{SAID[phase] ?? ""}</p>
      <div className="relative shrink-0">
        <StatusStrip
          phase={phase}
          closed={state.closed}
          openedAt={state.openedAt}
          restoredAt={state.restoredAt}
          verifiedAt={state.verifiedAt}
          failed={state.failedCheckouts}
          connection={connection}
          canReport={state.report !== null}
          onReport={showReport}
          onHelp={showHelp}
        />
        <RecoveryBanner recovered={state.recovered} slot={state.slot} revision={state.revision} />
      </div>
      <Heartbeat
        beats={state.beats}
        markers={state.markers}
        probedAt={state.probedAt}
        slot={state.slot}
        revision={state.revision}
        recorded={recorded}
      />
      <main className="grid min-h-0 flex-1 grid-cols-[13rem_minmax(0,1fr)] grid-rows-[minmax(0,1fr)_2.75rem] gap-3 p-3 2xl:grid-cols-[13rem_minmax(0,1fr)_17rem]">
        <div className="col-start-1 row-span-2 row-start-1 grid min-h-0">
          <Rail controls={controls} agents={state.agents} />
        </div>
        <div className="col-start-2 row-start-1 min-h-0 min-w-0 2xl:row-span-2">
          {idle ? (
            <Empty waiting={state.beats.length === 0} last={closed?.summary ?? null} />
          ) : (
            <div className="grid h-full min-h-0 grid-rows-[minmax(0,1fr)_1.25rem_5.75rem_0.75rem_10rem] 2xl:grid-rows-[minmax(0,1fr)_1.25rem_7rem_0.75rem_12.5rem]">
              <Findings {...state.findings} active={working} over={state.decision !== null || state.escalation !== null} focus={focus} />
              <Thread joined={state.findings.code !== null && state.findings.runtime !== null} />
              <Decision
                decision={state.decision}
                escalation={state.escalation}
                active={working}
                lit={focus === "decision"}
                dim={focus !== null && focus !== "decision"}
                onPack={showPack}
              />
              <span />
              <Verification
                spot={focus === "verification" ? (spot?.actor ?? "verifier") : "verifier"}
                candidates={state.candidates}
                active={working}
                lit={focus === "verification"}
                dim={focus !== null && focus !== "verification"}
                controls={controls}
              />
            </div>
          )}
        </div>
        <Feed events={state.feed} />
      </main>
      {open === "report" && state.report ? (
        <Drawer title="Incident report" markdown={state.report.markdown} prUrl={state.report.prUrl} onClose={close} />
      ) : null}
      {open === "pack" && state.escalation ? <Drawer title="Evidence pack" markdown={state.escalation.pack} onClose={close} /> : null}
      {open === "help" ? <Help director={director} onClose={close} /> : null}
      {open === "break" ? <ConfirmBreak onConfirm={controls.breakIt.run} onClose={close} /> : null}
    </div>
  );
}
