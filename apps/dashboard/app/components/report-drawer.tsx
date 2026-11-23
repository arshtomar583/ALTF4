"use client";

import { type ReactNode, useEffect, useRef } from "react";

import { Link } from "./icons";
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
  const close = () => {
    dialog.current?.close();
    onClose();
  };
  return (
    <dialog
      ref={dialog}
      aria-label={label}
      onCancel={(event) => {
        event.preventDefault();
        close();
      }}
      onClick={(event) => event.target === dialog.current && close()}
      className={`appear border-line bg-panel text-text backdrop:bg-black/70 ${className}`}
    >
      {children(close)}
    </dialog>
  );
}

export function Drawer({ title, markdown, prUrl, onClose }: {
  title: string;
  markdown: string;
  prUrl?: string | null;
  onClose: () => void;
}) {
  return (
    <Dialog label={title} onClose={onClose} className="m-0 ml-auto h-dvh max-h-none w-[40rem] max-w-[92vw] border-l">
      {(close) => (
        <div className="flex h-full flex-col">
          <div className="flex shrink-0 items-center gap-4 border-b border-line px-5 py-3">
            <h2 className="flex-1 text-title font-semibold">{title}</h2>
            {prUrl && /^https:\/\//.test(prUrl) ? (
              <a href={prUrl} target="_blank" rel="noreferrer" className="control inline-flex min-h-11 items-center gap-1.5 text-meta underline">
                <Link /> Pull request
              </a>
            ) : null}
            {prUrl === null ? <span className="text-meta text-text-2">No pull request</span> : null}
            <button type="button" onClick={close} className="control min-h-11 rounded-control border border-line px-4">Close</button>
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto px-5 pb-8">
            <Markdown text={markdown} />
          </div>
        </div>
      )}
    </Dialog>
  );
}
