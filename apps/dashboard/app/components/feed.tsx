"use client";

import { memo, useState } from "react";

import type { PhoenixEvent } from "../lib/events";
import { Chevron } from "./icons";
import { agent, Dot } from "./ui";

const SHOWN = 40;
type Group = { key: number; actor: string; events: PhoenixEvent[]; tools: boolean };

/** Neighbouring tool calls fold into one line per agent. */
function group(events: PhoenixEvent[]): Group[] {
  const groups: Group[] = [];
  let run: Group[] = [];
  for (const event of events) {
    const tools = event.type === "agent.tool";
    const mine = tools ? run.find((item) => item.actor === event.actor) : undefined;
    if (mine) {
      mine.events.push(event);
      continue;
    }
    if (groups.length === SHOWN) break;
    const added = { key: event.id, actor: event.actor, events: [event], tools };
    groups.push(added);
    run = tools ? [...run, added] : [];
  }
  return groups;
}

function Line({ event }: { event: PhoenixEvent }) {
  return (
    <div className="grid grid-cols-[4.6rem_0.65rem_minmax(0,1fr)] items-baseline gap-x-2 py-1">
      <time className="tnum text-meta text-text-3">{new Date(event.recorded_at ?? event.occurred_at).toLocaleTimeString("en-GB")}</time>
      <Dot actor={event.actor} />
      <p className="text-meta leading-snug wrap-anywhere 2xl:text-base 2xl:leading-snug">
        <span className="sr-only">{agent(event.actor).name}: </span>
        {event.summary ?? event.type}
      </p>
    </div>
  );
}

const Lines = memo(function Lines({ events }: { events: PhoenixEvent[] }) {
  if (events.length === 0) return <p className="text-text-2">Nothing yet. Checkouts are being watched.</p>;
  return (
    <ol>
      {group(events).map(({ key, actor, events: items, tools }) => (
        <li key={key}>
          {tools ? (
            <details>
              <summary className="flex min-h-11 cursor-pointer items-center gap-2 text-meta text-text-2">
                <Dot actor={actor} /> {agent(actor).name} · {items.length} tool {items.length === 1 ? "call" : "calls"}
              </summary>
              {items.map((event) => <Line key={event.id} event={event} />)}
            </details>
          ) : (
            <Line event={items[0]} />
          )}
        </li>
      ))}
    </ol>
  );
});

/** A column on a wide screen; one line under the story on a small one. */
export const Feed = memo(function Feed({ events }: { events: PhoenixEvent[] }) {
  const [open, setOpen] = useState(false);
  const latest = events.find((event) => event.type !== "agent.tool");
  return (
    <section aria-label="Event feed" className="relative col-start-2 row-start-2 min-h-0 min-w-0 2xl:col-start-3 2xl:row-span-2 2xl:row-start-1">
      <div className="hidden h-full flex-col rounded-panel border border-line bg-panel 2xl:flex">
        <h2 className="shrink-0 px-4 pt-2 text-title font-semibold">Feed</h2>
        <div role="region" aria-label="All events" tabIndex={0} className="relative min-h-0 flex-1 overflow-y-auto px-4 pb-3">
          <Lines events={events} />
        </div>
      </div>
      <div className="h-full 2xl:hidden">
        {open ? (
          <div className="absolute inset-x-0 bottom-full z-10 mb-2 max-h-[22rem] overflow-y-auto rounded-panel border border-text-3 bg-panel-2 px-4 py-2">
            <Lines events={events} />
          </div>
        ) : null}
        <button
          type="button"
          aria-expanded={open}
          onClick={() => setOpen(!open)}
          className="control flex h-full w-full items-center gap-3 rounded-panel border border-line bg-panel px-4 text-left text-meta"
        >
          <span className="label">Feed</span>
          {latest ? (
            <>
              <time className="tnum text-text-3">{new Date(latest.recorded_at ?? latest.occurred_at).toLocaleTimeString("en-GB")}</time>
              <Dot actor={latest.actor} />
              <span className="min-w-0 flex-1 truncate">{latest.summary ?? latest.type}</span>
            </>
          ) : (
            <span className="min-w-0 flex-1 text-text-2">Nothing yet. Checkouts are being watched.</span>
          )}
          <span className={open ? "rotate-180" : ""}><Chevron /></span>
        </button>
      </div>
    </section>
  );
});
