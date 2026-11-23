"use client";

import { memo, useState } from "react";

import type { Evidence, Finding } from "../lib/events";
import { Link, Warn } from "./icons";
import { agent, Code, Dot, Panel, Scroll, Skeleton } from "./ui";

/** Indentation inside the file says nothing here and costs a projector line. */
function tidy(excerpt: string): string {
  return excerpt.replace(/^([+-]?)[ \t]+/gm, (_, sign: string) => sign);
}

function host(url: string): string | null {
  try {
    const parsed = new URL(url);
    return /^https?:$/.test(parsed.protocol) ? parsed.hostname.replace(/^www\./, "") : null;
  } catch {
    return null;
  }
}

function Proof({ item }: { item: Evidence }) {
  const domain = item.url ? host(item.url) : null;
  return (
    <div className="mt-1.5">
      <p className="label truncate">
        {item.kind}
        {item.ref ? <span className="tnum tracking-normal normal-case"> · {item.ref}</span> : null}
      </p>
      {item.kind === "source" ? (
        <p className="line-clamp-2 text-meta text-text-2">{item.excerpt}</p>
      ) : (
        <Code text={tidy(item.excerpt)} diff={item.kind === "diff"} />
      )}
      {domain && item.url ? (
        <a
          href={item.url}
          target="_blank"
          rel="noreferrer"
          className="control mt-1 inline-flex min-h-11 items-center gap-1.5 rounded-control border border-line px-3 text-meta text-text"
        >
          <Link /> {domain}
        </a>
      ) : null}
    </div>
  );
}

function FindingCard({ letter, actor, finding, active, over, lit, dim }: {
  letter: string;
  actor: string;
  finding: Finding | null;
  active: boolean;
  over: boolean;
  lit: boolean;
  dim: boolean;
}) {
  const [open, setOpen] = useState(false);
  const title = (
    <span className="inline-flex items-center gap-2">
      <Dot actor={actor} />
      <span className="text-text-3">{letter}</span> {agent(actor).name}
    </span>
  );
  if (!finding) {
    return (
      <Panel title={title} spot={actor} lit={lit} dim={dim}>
        {over ? (
          <p className="px-4 pt-1 text-text-2"><Warn /> No finding</p>
        ) : (
          <Skeleton text={active ? "Investigating…" : "Starts once checkout is restored"} />
        )}
      </Panel>
    );
  }
  const percent = Math.round(finding.confidence * 100);
  const aside = (
    <span className="flex shrink-0 items-center gap-2 text-meta text-text-2">
      <span className="sr-only">Confidence</span>
      <span aria-hidden className="h-1.5 w-16 overflow-hidden rounded-full bg-line">
        <span className="block h-full origin-left bg-text-2" style={{ transform: `scaleX(${finding.confidence})` }} />
      </span>
      <span className="tnum text-text">{percent}%</span>
    </span>
  );
  return (
    <Panel title={title} aside={aside} spot={actor} lit={lit} dim={dim}>
      <Scroll label={`Finding ${letter}`}>
        <div className="appear">
          <p className={`leading-snug ${open ? "" : "line-clamp-3"}`}>{finding.claim}</p>
          {finding.claim.length > 150 ? (
            <button type="button" aria-expanded={open} onClick={() => setOpen(!open)} className="control -my-2 min-h-11 text-meta text-text-2 underline underline-offset-2">
              {open ? "Show less" : "Show the full claim"}
            </button>
          ) : null}
          {finding.evidence.map((item, index) => <Proof key={index} item={item} />)}
        </div>
      </Scroll>
    </Panel>
  );
}

export const Findings = memo(function Findings({ code, runtime, active, over, focus }: {
  code: Finding | null;
  runtime: Finding | null;
  active: boolean;
  over: boolean;
  focus: string | null;
}) {
  return (
    <div className="grid min-h-0 grid-cols-2 gap-3">
      <FindingCard letter="A" actor="code_investigator" finding={code} active={active} over={over} lit={focus === "code"} dim={focus !== null && focus !== "code"} />
      <FindingCard letter="B" actor="runtime_investigator" finding={runtime} active={active} over={over} lit={focus === "runtime"} dim={focus !== null && focus !== "runtime"} />
    </div>
  );
});
