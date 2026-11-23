import type { PhoenixEvent } from "../lib/events";

type Recording = { recorded_at: string | null; events: PhoenixEvent[] };

/** Play a real incident's own events with their own timing. Returns a stop function. */
export function replay(recording: Recording, emit: (event: PhoenixEvent) => void): () => void {
  const first = recording.events[0];
  if (!first) return () => undefined;
  // Moved to the present so the running clocks work; the banner states when it really happened.
  const shift = Date.now() - Date.parse(first.occurred_at);
  const timers = recording.events.map((event) => {
    const at = Date.parse(event.occurred_at) + shift;
    return window.setTimeout(
      () => emit({ ...event, occurred_at: new Date(at).toISOString() }),
      Math.max(0, at - Date.now()),
    );
  });
  return () => timers.forEach((timer) => window.clearTimeout(timer));
}
