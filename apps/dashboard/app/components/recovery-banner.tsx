"use client";

import { useEffect, useState } from "react";

import type { IncidentState } from "../lib/incident-state";

const SHOWN_MS = 8000;

export function RecoveryBanner({ recovered }: { recovered: IncidentState["recovered"] }) {
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
    <p role="status" className="absolute top-24 left-1/2 z-10 -translate-x-1/2 rounded-b-md border border-t-0 border-busy bg-panel px-4 py-2">
      ⚠ Brain restarted. {recovered.step ? `Resumed at ${recovered.step}` : "Resumed from the start"}
    </p>
  );
}
