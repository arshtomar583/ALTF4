"use client";

import { useEffect, useState } from "react";

import type { IncidentState } from "../lib/incident-state";
import { Warn } from "./icons";

const SHOWN_MS = 8000;

export function RecoveryBanner({ recovered, slot, revision }: {
  recovered: IncidentState["recovered"];
  slot: string | null;
  revision: string | null;
}) {
  const [visible, setVisible] = useState(false);
  useEffect(() => {
    // A reload replays history, so only a fresh restart is announced.
    const left = recovered ? recovered.at + SHOWN_MS - Date.now() : 0;
    if (left <= 0) return setVisible(false);
    setVisible(true);
    const timer = window.setTimeout(() => setVisible(false), Math.min(left, SHOWN_MS));
    return () => window.clearTimeout(timer);
  }, [recovered]);
  if (!visible || !recovered) return null;
  return (
    <p role="status" className="appear xl:absolute xl:inset-x-0 xl:top-full z-10 border-b border-line bg-panel-2 py-1.5 text-center">
      <Warn /> Brain restarted · {recovered.step ? `resumed at ${recovered.step}` : "resumed from the start"} · live slot {slot?.toUpperCase() ?? "—"} @{" "}
      <span className="tnum">{revision?.slice(0, 7) ?? "—"}</span>
    </p>
  );
}
