import { Dialog } from "./report-drawer";

const KEYS = [
  ["B", "Break checkout, after a confirmation"],
  ["A", "Approve the release, when a fix is verified"],
  ["D", "Turn director mode on or off"],
  ["R", "Open the report, when it is ready"],
  ["?", "Show these shortcuts"],
];

export function Help({ director, onClose }: { director: boolean; onClose: () => void }) {
  return (
    <Dialog label="Keyboard shortcuts" onClose={onClose} className="m-auto w-[30rem] max-w-[92vw] rounded-panel border p-5">
      {(close) => (
        <>
          <h2 className="text-title font-semibold">Keyboard shortcuts</h2>
          <dl className="my-3 grid grid-cols-[3rem_1fr] items-center gap-y-2">
            {KEYS.map(([key, text]) => (
              <div key={key} className="contents">
                <dt><kbd className="tnum rounded-sm border border-text-3 px-2 py-0.5">{key}</kbd></dt>
                <dd>{text}</dd>
              </div>
            ))}
          </dl>
          <p className="text-meta text-text-2">
            Director mode is {director ? "on" : "off"}. It lights the panel that heard from its agent last and dims the others.
          </p>
          <button type="button" onClick={close} className="control mt-4 min-h-11 w-full rounded-control border border-line">Close</button>
        </>
      )}
    </Dialog>
  );
}

export function ConfirmBreak({ enabled, onConfirm, onClose }: { enabled: boolean; onConfirm: () => void; onClose: () => void }) {
  return (
    <Dialog label="Break checkout?" onClose={onClose} className="m-auto w-[26rem] max-w-[92vw] rounded-panel border p-5">
      {(close) => (
        <>
          <h2 className="text-title font-semibold">Break checkout?</h2>
          <p className="mt-1 text-text-2">This deploys a bad commit to the live shop.</p>
          <div className="mt-4 grid grid-cols-2 gap-3">
            <button type="button" onClick={close} className="control min-h-11 rounded-control border border-line">Cancel</button>
            <button
              type="button"
              disabled={!enabled}
              onClick={() => {
                onConfirm();
                close();
              }}
              className="control min-h-11 rounded-control border-2 border-down font-semibold"
            >
              Break it
            </button>
          </div>
        </>
      )}
    </Dialog>
  );
}
