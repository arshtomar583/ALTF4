import { Fragment, type ReactNode } from "react";

import { Code } from "./ui";

/** Renders the subset of Markdown the report uses. Text never becomes HTML. */
function inline(text: string): ReactNode[] {
  return text.split(/(\*\*[^*]+\*\*|`[^`]+`|\[[^\]]+\]\(https?:\/\/[^)\s]+\))/).map((part, index) => {
    if (part.startsWith("**")) return <strong key={index}>{part.slice(2, -2)}</strong>;
    if (part.startsWith("`")) return <code key={index} className="font-mono">{part.slice(1, -1)}</code>;
    const link = part.match(/^\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)$/);
    if (link) return <a key={index} href={link[2]} target="_blank" rel="noreferrer" className="underline">{link[1]}</a>;
    return <Fragment key={index}>{part}</Fragment>;
  });
}

function cells(row: string): string[] {
  return row
    .replace(/^\||\|$/g, "")
    .split(/(?<!\\)\|/)
    .map((cell) => cell.trim().replaceAll("\\|", "|"));
}

export function Markdown({ text }: { text: string }) {
  const lines = text.split("\n");
  const blocks: ReactNode[] = [];
  let index = 0;
  const take = (test: (line: string) => boolean) => {
    const taken: string[] = [];
    while (index < lines.length && test(lines[index])) taken.push(lines[index++]);
    return taken;
  };
  while (index < lines.length) {
    const line = lines[index];
    const key = index;
    const fence = line.match(/^(`{3,})(.*)$/);
    const heading = line.match(/^(#{1,4}) (.*)$/);
    if (fence) {
      index++;
      const body = take((item) => item !== fence[1]);
      index++;
      blocks.push(<Code key={key} text={body.join("\n")} diff={fence[2] === "diff"} />);
    } else if (heading) {
      index++;
      const size = heading[1].length === 1 ? "text-2xl" : heading[1].length === 2 ? "text-xl" : "text-base";
      blocks.push(<h3 key={key} className={`mt-5 mb-1 font-semibold ${size}`}>{inline(heading[2])}</h3>);
    } else if (line.startsWith("|")) {
      const [head, , ...rows] = take((item) => item.startsWith("|"));
      blocks.push(
        <div key={key} className="my-2 overflow-x-auto">
          <table className="w-full text-left">
            <thead className="text-sm text-dim">
              <tr>{cells(head).map((cell, column) => <th key={column} className="pr-4 pb-1 font-medium">{inline(cell)}</th>)}</tr>
            </thead>
            <tbody>
              {rows.map((row, position) => (
                <tr key={position} className="border-t border-line align-top">
                  {cells(row).map((cell, column) => <td key={column} className="py-1 pr-4">{inline(cell)}</td>)}
                </tr>
              ))}
            </tbody>
          </table>
        </div>,
      );
    } else if (line.startsWith("- ")) {
      const items = take((item) => item.startsWith("- "));
      blocks.push(
        <ul key={key} className="my-1 list-disc pl-5">
          {items.map((item, position) => <li key={position}>{inline(item.slice(2))}</li>)}
        </ul>,
      );
    } else {
      index++;
      if (line.trim()) blocks.push(<p key={key} className="my-1">{inline(line)}</p>);
    }
  }
  return <>{blocks}</>;
}
