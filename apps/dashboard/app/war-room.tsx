"use client";

import { useCallback, useEffect, useRef, useState } from "react";

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
import { ReleaseStage, RestoreStage, stageOf, Stepper } from "./components/stages";
import { Thread } from "./components/thread";
import { Verification } from "./components/verification";
import { download } from "./lib/download";
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

const SIGNAL_MS = 6000;

type Open = "report" | "pack" | "help" | "break" | null;

/** Whether what the screen shows is fresh: connected, with a probe in the last six seconds. */
function useSignal(probedAt: number | null, connection: string, recorded: boolean): boolean {
  const [fresh, setFresh] = useState(false);
  useEffect(() => {
    const check = () => setFresh(probedAt !== null && Date.now() - probedAt <= SIGNAL_MS);
    check();
    const timer = window.setInterval(check, 500);
    return () => window.clearInterval(timer);
  }, [probedAt]);
  return recorded ? probedAt !== null : connection === "Live" && fresh;
}

export function WarRoom() {
  const { state, connection, incidentOpen, banner, recorded } = useIncident();
  const controls = useControls(state, incidentOpen, recorded, connection === "Live");
  const [following, setFollowing] = useState(true);
  const [picked, setPicked] = useState<number | null>(null);
  const [open, setOpen] = useState<Open>(null);
  const close = useCallback(() => setOpen(null), []);
  const showReport = useCallback(() => setOpen("report"), []);
  const showPack = useCallback(() => setOpen("pack"), []);
  const showHelp = useCallback(() => setOpen("help"), []);

  const { phase } = state;
  const signal = useSignal(state.probedAt, connection, recorded);
  const lastOk = state.beats.at(-1)?.ok ?? false;
  const working = phase === "fixing";
  const reached = stageOf(state);
  // A new stage takes the screen back, unless the viewer asked to stay.
  useEffect(() => {
    if (following) setPicked(null);
  }, [reached, following]);
  const shown = Math.min(picked ?? reached, reached);
  const report = state.report;
  const saveReport = useCallback(() => {
    if (report) download("phoenix-incident-report.md", report.markdown);
  }, [report]);

  // The listener is attached once and always sees the newest state.
  const keys = useRef<(key: string) => void>(() => undefined);
  useEffect(() => {
    keys.current = (key) => {
      if (key === "b" && controls.breakIt.enabled) setOpen("break");
      if (key === "a" && controls.approve.enabled) controls.approve.run();
      if (key === "d") {
        setFollowing(!following);
        setPicked(following ? shown : null);
      }
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
      <p aria-live="polite" aria-atomic="true" className="sr-only">{signal ? (SAID[phase] ?? "") : "No signal"}</p>
      <div className="relative shrink-0">
        <StatusStrip
          phase={phase}
          closed={state.closed}
          openedAt={state.openedAt}
          restoredAt={state.restoredAt}
          verifiedAt={state.verifiedAt}
          failed={state.failedCheckouts}
          connection={connection}
          signal={signal}
          lastOk={lastOk}
          canReport={state.report !== null}
          onReport={showReport}
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
      <main className="grid min-h-0 flex-1 grid-cols-[14rem_minmax(0,1fr)] grid-rows-[minmax(0,1fr)_2.75rem] gap-4 p-4 2xl:grid-cols-[14rem_minmax(0,1fr)_18rem]">
        <div className="col-start-1 row-span-2 row-start-1 grid min-h-0">
          <Rail controls={controls} agents={state.agents} onHelp={showHelp} />
        </div>
        <div className="col-start-2 row-start-1 min-h-0 min-w-0 2xl:row-span-2">
          {idle ? (
            <Empty beats={state.beats} slot={state.slot} revision={state.revision} last={closed?.summary ?? null} healthy={signal && lastOk} />
          ) : (
            <div className="grid h-full min-h-0 grid-rows-[auto_minmax(0,1fr)] gap-3">
              <Stepper reached={reached} shown={shown} done={phase === "recovered"} onPick={setPicked} />
              {shown === 0 ? <RestoreStage state={state} /> : null}
              {shown === 1 ? (
                <div className="grid min-h-0 grid-rows-[minmax(0,1fr)_1.25rem_7.5rem]">
                  <Findings {...state.findings} active={working} over={state.decision !== null || state.escalation !== null} />
                  <Thread joined={state.findings.code !== null && state.findings.runtime !== null} />
                  <Decision decision={state.decision} escalation={state.escalation} active={working} onPack={showPack} />
                </div>
              ) : null}
              {shown === 2 ? <Verification candidates={state.candidates} active={working} controls={controls} /> : null}
              {shown === 3 ? <ReleaseStage state={state} onReport={showReport} onDownload={saveReport} /> : null}
            </div>
          )}
        </div>
        <Feed events={state.feed} />
      </main>
      {open === "report" && state.report ? (
        <Drawer title="Incident report" file="phoenix-incident-report.md" markdown={state.report.markdown} prUrl={state.report.prUrl} onClose={close} />
      ) : null}
      {open === "pack" && state.escalation ? <Drawer title="Evidence pack" file="phoenix-evidence-pack.md" markdown={state.escalation.pack} onClose={close} /> : null}
      {open === "help" ? <Help following={following} onClose={close} /> : null}
      {open === "break" ? <ConfirmBreak onConfirm={controls.breakIt.run} onClose={close} /> : null}
    </div>
  );
}
