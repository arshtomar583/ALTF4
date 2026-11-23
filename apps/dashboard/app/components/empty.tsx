import { Check } from "./icons";

export function Empty({ waiting, last }: { waiting: boolean; last: string | null }) {
  return (
    <div className="grid h-full place-content-center rounded-panel border border-line bg-panel p-8 text-center">
      <p className="text-state leading-tight font-semibold">
        {waiting ? "Waiting for the first checkout" : <><Check className="h-6 w-6" /> All systems healthy · waiting for an incident</>}
      </p>
      <p className="mt-2 text-text-2">{last ? `Last incident: ${last}` : "No incident has been recorded."}</p>
    </div>
  );
}
