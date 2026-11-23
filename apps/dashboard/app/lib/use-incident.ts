"use client";

import { useCallback, useEffect, useReducer, useState } from "react";

import { play } from "../mock/incident";
import type { PhoenixEvent } from "./events";
import { initialState, reducer } from "./incident-state";

export const BRAIN = process.env.NEXT_PUBLIC_BRAIN_URL ?? "http://127.0.0.1:8000";
export type Connection = "Connecting" | "Live" | "Reconnecting" | "Recorded story";

export async function post(path: string, body?: object): Promise<string | null> {
  try {
    const response = await fetch(BRAIN + path, {
      method: "POST",
      headers: body ? { "Content-Type": "application/json" } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    });
    if (response.ok) return null;
    const answer = await response.json().catch(() => null);
    return typeof answer?.detail === "string" ? answer.detail : `The brain answered ${response.status}`;
  } catch {
    return "The brain is not reachable";
  }
}

/** Read the event stream with fetch: unlike EventSource, it can ask for the whole history. */
async function stream(
  lastId: number,
  signal: AbortSignal,
  onOpen: () => void,
  onEvents: (events: PhoenixEvent[]) => void,
): Promise<void> {
  const response = await fetch(BRAIN + "/events", {
    headers: { "Last-Event-ID": String(lastId) },
    cache: "no-store",
    signal,
  });
  if (!response.ok || !response.body) throw new Error("No event stream");
  onOpen();
  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) return;
    buffer += value;
    const messages = buffer.split("\n\n");
    buffer = messages.pop() ?? "";
    const events = messages
      .flatMap((message) => message.split("\n"))
      .filter((line) => line.startsWith("data: "))
      .map((line) => JSON.parse(line.slice(6)) as PhoenixEvent);
    if (events.length) onEvents(events);
  }
}

export function useIncident() {
  const [state, dispatch] = useReducer(reducer, initialState);
  const [connection, setConnection] = useState<Connection>("Connecting");
  const [incidentOpen, setIncidentOpen] = useState<boolean | null>(null);

  const checkIncident = useCallback(async () => {
    try {
      const response = await fetch(BRAIN + "/incidents/current", { cache: "no-store" });
      setIncidentOpen(response.ok ? (await response.json()) !== null : null);
    } catch {
      setIncidentOpen(null);
    }
  }, []);

  useEffect(() => {
    if (new URLSearchParams(window.location.search).get("mock") === "1") {
      setConnection("Recorded story");
      dispatch({ type: "reset" });
      return play((event) => dispatch({ type: "events", events: [event] }));
    }

    const abort = new AbortController();
    let lastId = 0;
    const receive = (events: PhoenixEvent[]) => {
      // Lower ids than already seen mean the brain started over with an empty store.
      if (events[0].id <= lastId) {
        dispatch({ type: "reset" });
        lastId = 0;
        throw new Error("The event store was reset");
      }
      lastId = events[events.length - 1].id;
      dispatch({ type: "events", events });
      if (events.some((event) => /^(chaos|incident|release|approval)/.test(event.type))) void checkIncident();
    };
    const connect = async () => {
      while (!abort.signal.aborted) {
        try {
          await stream(lastId, abort.signal, () => { setConnection("Live"); void checkIncident(); }, receive);
        } catch {
          // Every failure is handled the same way: say so and reconnect.
        }
        if (abort.signal.aborted) return;
        setConnection("Reconnecting");
        await new Promise((resolve) => setTimeout(resolve, 1000));
      }
    };
    void connect();
    return () => abort.abort();
  }, [checkIncident]);

  return { state, connection, incidentOpen, recorded: connection === "Recorded story" };
}
