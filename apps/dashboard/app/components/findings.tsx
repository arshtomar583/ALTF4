import type { Evidence, Finding } from "../lib/events";
import { agent, Code, Panel, Scroll, Waiting } from "./ui";

/** Indentation inside the file says nothing here and costs a projector line. */
function tidy(excerpt: string): string {
  return excerpt.replace(/^([+-]?)[ \t]+/gm, (_, sign: string) => (sign ? `${sign} ` : ""));
}

function Proof({ item }: { item: Evidence }) {
  const link = item.url && /^https?:\/\//.test(item.url) ? item.url : null;
  return (
    <div className="mt-1.5">
      <p className="truncate text-sm text-dim">
        {item.kind}
        {item.ref ? ` · ${item.ref}` : ""}
      </p>
      {item.kind === "source" ? (
        <p className="line-clamp-2">{item.excerpt}</p>
      ) : (
        <Code text={tidy(item.excerpt)} diff={item.kind === "diff"} />
      )}
      {link ? (
        <a href={link} target="_blank" rel="noreferrer" className="block truncate text-sm underline underline-offset-2">
          {link}
        </a>
      ) : null}
    </div>
  );
}

function FindingCard({ letter, actor, finding, active, over }: {
  letter: string;
  actor: string;
  finding: Finding | null;
  active: boolean;
  over: boolean;
}) {
  const { name, color } = agent(actor);
  const title = (
    <span className="flex items-center gap-2">
      <span aria-hidden className="h-2 w-2 rounded-full" style={{ background: color }} />
      <span className="truncate">Finding {letter} · {name}</span>
      {finding ? (
        <span className="ml-auto flex shrink-0 items-center gap-2">
          <span className="sr-only">Confidence</span>
          <span aria-hidden className="h-1.5 w-16 rounded-full bg-line">
            <span className="block h-full rounded-full bg-dim" style={{ width: `${Math.round(finding.confidence * 100)}%` }} />
          </span>
          <span className="font-mono text-ink">{Math.round(finding.confidence * 100)}%</span>
        </span>
      ) : null}
    </span>
  );
  if (!finding) {
    return (
      <Panel title={title}>
        {over ? (
          <p className="px-4 pt-1 text-dim">⚠ No finding</p>
        ) : active ? (
          <Waiting text="Investigating…" />
        ) : (
          <p className="px-4 pt-1 text-dim">Starts once checkout is restored</p>
        )}
      </Panel>
    );
  }
  return (
    <Panel title={title}>
      <Scroll label={`Finding ${letter}`}>
        <p className="line-clamp-3 leading-snug">{finding.claim}</p>
        {finding.evidence.map((item, index) => (
          <Proof key={index} item={item} />
        ))}
      </Scroll>
    </Panel>
  );
}

export function Findings({ code, runtime, active, over }: {
  code: Finding | null;
  runtime: Finding | null;
  active: boolean;
  over: boolean;
}) {
  return (
    <div className="grid min-h-0 grid-cols-2 gap-3">
      <FindingCard letter="A" actor="code_investigator" finding={code} active={active} over={over} />
      <FindingCard letter="B" actor="runtime_investigator" finding={runtime} active={active} over={over} />
    </div>
  );
}
