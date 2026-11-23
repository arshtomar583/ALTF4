import type { PhoenixEvent } from "../lib/events";
import { agent, Dot, Panel, Scroll } from "./ui";

type Group = { key: number; events: PhoenixEvent[]; quiet: boolean };

/** Tool calls and token counts are detail, so neighbours fold into one line. */
function group(events: PhoenixEvent[]): Group[] {
  const groups: Group[] = [];
  for (const event of events) {
    const quiet = event.type === "agent.tool" || (event.type === "agent.note" && Boolean(event.data.usage));
    const last = groups.at(-1);
    if (quiet && last?.quiet) last.events.push(event);
    else groups.push({ key: event.id, events: [event], quiet });
  }
  return groups;
}

function time(event: PhoenixEvent) {
  return new Date(event.recorded_at ?? event.occurred_at).toLocaleTimeString("en-GB");
}

function Line({ event }: { event: PhoenixEvent }) {
  return (
    <div className="flex gap-2.5 py-1">
      <Dot actor={event.actor} />
      <div className="min-w-0">
        <p className="leading-snug wrap-anywhere">{event.summary ?? event.type}</p>
        <p className="text-sm text-dim">
          <span className="font-mono">{time(event)}</span> {agent(event.actor).name}
        </p>
      </div>
    </div>
  );
}

export function Feed({ events }: { events: PhoenixEvent[] }) {
  return (
    <Panel title="What is happening">
      <Scroll label="Event feed">
        {events.length === 0 ? <p className="text-dim">Nothing yet. Checkouts are being watched.</p> : null}
        <ol>
          {group(events).map(({ key, events: items, quiet }) => (
            <li key={key}>
              {quiet ? (
                <details className="py-1">
                  <summary className="cursor-pointer text-sm text-dim">
                    {items.length} tool {items.length === 1 ? "step" : "steps"}
                  </summary>
                  {items.map((event) => (
                    <Line key={event.id} event={event} />
                  ))}
                </details>
              ) : (
                <Line event={items[0]} />
              )}
            </li>
          ))}
        </ol>
      </Scroll>
    </Panel>
  );
}
