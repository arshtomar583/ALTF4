"use client";

import { useState } from "react";

import { Decision } from "./components/decision";
import { Feed } from "./components/feed";
import { Findings } from "./components/findings";
import { Heartbeat } from "./components/heartbeat";
import { Rail } from "./components/rail";
import { RecoveryBanner } from "./components/recovery-banner";
import { ReportDrawer } from "./components/report-drawer";
import { StatusStrip } from "./components/status-strip";
import { Verification } from "./components/verification";
import { useIncident } from "./lib/use-incident";

export function WarRoom() {
  const { state, connection, incidentOpen, recorded } = useIncident();
  const [reading, setReading] = useState(false);
  const working = state.phase === "fixing";
  return (
    <div className="relative flex h-dvh min-h-[700px] min-w-[1180px] flex-col">
      <StatusStrip state={state} connection={connection} />
      <RecoveryBanner recovered={state.recovered} />
      <Heartbeat beats={state.beats} markers={state.markers} />
      <main className="grid min-h-0 flex-1 grid-cols-[10rem_minmax(0,1fr)_15.5rem] gap-3 p-3">
        <Rail state={state} incidentOpen={incidentOpen} recorded={recorded} onReport={() => setReading(true)} />
        <div className="grid min-h-0 grid-rows-[minmax(0,1fr)_6.5rem_minmax(11rem,0.75fr)] gap-3">
          <Findings {...state.findings} active={working} over={state.decision !== null || state.escalation !== null} />
          <Decision decision={state.decision} escalation={state.escalation} active={working} />
          <Verification state={state} active={working} recorded={recorded} />
        </div>
        <Feed events={state.feed} />
      </main>
      {reading && state.report ? <ReportDrawer report={state.report} onClose={() => setReading(false)} /> : null}
    </div>
  );
}
