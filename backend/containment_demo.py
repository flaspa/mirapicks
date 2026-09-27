"""Containment demo for Challenge 1: hostile code inside the CURRENT Mira Picks sandbox.

Runs a deliberately misbehaving script in the same disposable container, with the same flags, as every agent
sandbox (backend.sandbox._docker_command). Nothing touches the host: the script only acts inside the container.

    python -m backend.containment_demo

The script tries to: identify its user, overwrite the sandbox's own code, write system files, find secrets in its
environment, read the host's .env, then spin in an infinite loop. The host kills it at the timeout and the
container is removed.
"""

from __future__ import annotations

import os
import subprocess
import time
import uuid

from backend.sandbox import ARTIFACTS_DIR, SCOUT_IMAGE, _docker_command

TIMEOUT_S = 10
HOSTILE_JS = r"""
const fs = require("fs");
const log = (step, ok, detail) => console.log(`${ok ? "ALLOWED" : "BLOCKED"}  ${step.padEnd(34)} ${detail}`);
const attempt = (step, fn) => { try { log(step, true, fn() || "ok"); } catch (e) { log(step, false, e.code || e.message); } };
console.log(`user inside sandbox: uid=${process.getuid()} (not root)`);
attempt("overwrite sandbox code /app/dist", () => fs.writeFileSync("/app/dist/scout.js", "pwned"));
attempt("write system file /etc/passwd", () => fs.appendFileSync("/etc/passwd", "evil:x:0:0::/:/bin/sh\n"));
attempt("create file in /usr/bin", () => fs.writeFileSync("/usr/bin/backdoor", "#!/bin/sh"));
attempt("read host secrets /opt/mirapicks/.env", () => fs.readFileSync("/opt/mirapicks/.env", "utf8").slice(0, 0) + "READ!");
const secrets = Object.keys(process.env).filter((k) => /KEY|TOKEN|SECRET|PASSWORD/i.test(k));
log("find API keys in environment", secrets.length > 0, secrets.length ? secrets.join(",") : "none present");
attempt("write own scratch space /out", () => { fs.writeFileSync("/out/scratch.txt", "sandbox-owned"); return "ok (per-run folder only)"; });
console.log("starting infinite loop... (host timeout will kill this container)");
while (true) {}
"""


def main() -> int:
    run_id = "containment-" + uuid.uuid4().hex[:6]
    run_dir = (ARTIFACTS_DIR / run_id).resolve()
    run_dir.mkdir(parents=True)
    os.chmod(run_dir, 0o777)
    cmd = _docker_command(run_id, run_dir, "unused")[:-2] + ["--entrypoint", "node", SCOUT_IMAGE, "-e", HOSTILE_JS]
    print(f"sandbox: disposable container mira-scout-{run_id} (read-only, non-root, no secrets, 1 CPU, 1 GB, "
          f"512 pids, {TIMEOUT_S}s timeout)\n")
    t0 = time.time()
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        out, _ = proc.communicate(timeout=TIMEOUT_S)
    except subprocess.TimeoutExpired:
        subprocess.run(["docker", "kill", f"mira-scout-{run_id}"], capture_output=True, check=False)
        out, _ = proc.communicate()
        out += f"\nHOST: timeout after {time.time() - t0:.1f}s -> container killed\n"
    print(out.strip())
    left = subprocess.run(["docker", "ps", "-a", "-q", "--filter", f"name=mira-scout-{run_id}"],
                          capture_output=True, text=True).stdout.strip()
    print(f"HOST: container still present afterwards? {'yes' if left else 'no (removed by --rm)'}")
    print(f"HOST: files the sandbox could write: {sorted(p.name for p in run_dir.iterdir())} in {run_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
