"use client";

import { useEffect, useRef } from "react";

import { Markdown } from "./markdown";

export function ReportDrawer({ report, onClose }: {
  report: { markdown: string; prUrl: string | null };
  onClose: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    dialog.current?.showModal();
  }, []);
  return (
    <dialog
      ref={dialog}
      onClose={onClose}
      onClick={(event) => event.target === dialog.current && dialog.current.close()}
      aria-label="Incident report"
      className="m-0 ml-auto h-dvh max-h-none w-[min(56rem,92vw)] max-w-none border-l border-line bg-panel text-ink backdrop:bg-black/70"
    >
      <div className="flex h-full flex-col">
        <div className="flex shrink-0 items-center justify-between gap-4 border-b border-line px-6 py-3">
          {report.prUrl && /^https:\/\//.test(report.prUrl) ? (
            <a href={report.prUrl} target="_blank" rel="noreferrer" className="truncate underline">Open the pull request</a>
          ) : (
            <p className="text-dim">No pull request was opened</p>
          )}
          <button type="button" onClick={() => dialog.current?.close()} className="rounded-md border border-dim px-3 py-1.5">
            Close
          </button>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto px-6 pb-8">
          <Markdown text={report.markdown} />
        </div>
      </div>
    </dialog>
  );
}
