"use client";

import { type ReactNode, useEffect, useRef } from "react";

import { download } from "../lib/download";
import { Download, Link, Page } from "./icons";
import { Markdown } from "./markdown";

export function Dialog({ label, className, onClose, children }: {
  label: string;
  className: string;
  onClose: () => void;
  children: (close: () => void) => ReactNode;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    dialog.current?.showModal();
  }, []);
  const close = () => dialog.current?.close();
  return (
    <dialog
      ref={dialog}
      aria-label={label}
      onClose={onClose}
      onClick={(event) => event.target === dialog.current && close()}
      className={`appear border-line bg-panel text-text backdrop:bg-black/70 ${className}`}
    >
      {children(close)}
    </dialog>
  );
}

export function Drawer({ title, file, markdown, prUrl, onClose }: {
  title: string;
  file: string;
  markdown: string;
  prUrl?: string | null;
  onClose: () => void;
}) {
  return (
    <Dialog label={title} onClose={onClose} className="m-0 ml-auto h-dvh max-h-none w-[46rem] max-w-[92vw] border-l">
      {(close) => (
        <div className="flex h-full flex-col">
          <div className="flex shrink-0 items-center gap-2.5 border-b border-line px-6 py-3">
            <h2 className="flex-1 text-title font-semibold">{title}</h2>
            {prUrl && /^https:\/\//.test(prUrl) ? (
              <a href={prUrl} target="_blank" rel="noreferrer" className="control inline-flex min-h-11 items-center gap-1.5 text-meta underline">
                <Link /> Pull request
              </a>
            ) : null}
            <button type="button" onClick={() => download(file, markdown)} className="control inline-flex min-h-11 items-center gap-2 rounded-control bg-text px-4 font-medium text-bg">
              <Download /> Download
            </button>
            <button type="button" onClick={() => window.print()} className="control inline-flex min-h-11 items-center gap-2 rounded-control border border-line px-4">
              <Page /> Save as PDF
            </button>
            <button type="button" onClick={close} className="control min-h-11 rounded-control border border-line px-4">Close</button>
          </div>
          <div className="report min-h-0 flex-1 overflow-y-auto px-6 pb-8">
            <Markdown text={markdown} />
          </div>
        </div>
      )}
    </Dialog>
  );
}
