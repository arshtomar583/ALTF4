const { spawn } = require("node:child_process");
const { readFileSync } = require("node:fs");
const { join } = require("node:path");

const slot = process.env.PHOENIX_SLOT;
const root = "/app/revisions";
const marker = join(root, "slot-" + slot + ".revision");
const server = join(root, "slot-" + slot, "services", "shoplite", "src", "server.js");
let child;
let startedRevision;
let stopping = false;

function reconcile() {
  const revision = readFileSync(marker, "ascii").trim();
  if (stopping || revision === startedRevision) return;
  if (child) {
    child.kill();
    return;
  }

  child = spawn(process.execPath, [server], {
    env: { ...process.env, SHOP_REVISION: revision },
    stdio: "inherit",
  });
  startedRevision = revision;
  child.once("exit", () => {
    child = undefined;
    if (stopping) process.exit(0);
  });
}

const watcher = setInterval(reconcile, 200);
function shutdown() {
  stopping = true;
  clearInterval(watcher);
  if (child) child.kill();
  else process.exit(0);
}

process.once("SIGTERM", shutdown);
process.once("SIGINT", shutdown);
reconcile();
