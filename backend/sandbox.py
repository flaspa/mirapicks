"""Launch a disposable, locked-down Scout container for one URL.

Untrusted execution (the browser and the page it loads) happens only inside
the container. This module just starts it, enforces limits, and reads back
bounded JSON on stdout plus a screenshot from a per-run artifacts folder.

CLI for local testing:
    python -m backend.sandbox https://example.com/
"""

from __future__ import annotations

import ipaddress
import json
import os
import socket
import subprocess
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

REPO_ROOT = Path(__file__).resolve().parent.parent
ARTIFACTS_DIR = Path(os.getenv("MIRA_ARTIFACTS_DIR", REPO_ROOT / "artifacts"))
SCOUT_IMAGE = os.getenv("SCOUT_IMAGE", "mira-scout:0.1")
SCOUT_NETWORK = os.getenv("SCOUT_NETWORK", "bridge")
SCOUT_TIMEOUT_S = int(os.getenv("SCOUT_TIMEOUT_S", "60"))

MAX_STDOUT_BYTES = 64 * 1024
MAX_SCREENSHOT_BYTES = 5 * 1024 * 1024
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


class ScoutError(RuntimeError):
    pass


@dataclass
class ScoutRun:
    run_id: str
    run_dir: Path
    exit_code: int
    evidence: dict
    screenshot_path: Path | None


def validate_target_url(url: str) -> None:
    """Reject non-http(s) URLs and hosts that resolve to non-public addresses.

    This is a first line of defence only. DNS can change between this check and
    the container's own lookup, so the VM firewall must also block the Scout
    network from private ranges and the 169.254.169.254 metadata service.
    """
    if len(url) > 2048:
        raise ScoutError("URL too long")
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ScoutError("only absolute http(s) URLs are allowed")
    try:
        infos = socket.getaddrinfo(parts.hostname, parts.port or 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise ScoutError(f"cannot resolve host: {parts.hostname}") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:
            raise ScoutError(f"host resolves to a non-public address: {ip}")


def _docker_command(run_id: str, run_dir: Path, url: str) -> list[str]:
    return [
        "docker", "run",
        "--rm",
        "--name", f"mira-scout-{run_id}",
        "--init",
        "--network", SCOUT_NETWORK,
        # Public resolver: the host's resolv.conf may point at a resolver the container can't reach.
        "--dns", "1.1.1.1",
        "--read-only",
        "--tmpfs", "/tmp:rw,nosuid,nodev,size=256m",
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges",
        "--pids-limit", "512",
        "--memory", "1g",
        "--memory-swap", "1g",
        "--cpus", "1",
        "--shm-size", "512m",
        "--user", "pwuser",
        # The only host path the sandbox can touch: this run's empty folder.
        "--mount", f"type=bind,source={run_dir},target=/out",
        # No --env: the sandbox receives no secrets.
        SCOUT_IMAGE,
        url,
    ]


def run_scout(url: str) -> ScoutRun:
    validate_target_url(url)

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    run_dir = (ARTIFACTS_DIR / run_id).resolve()
    run_dir.mkdir(parents=True, exist_ok=False)
    # pwuser (uid 1000) inside the container must be able to write the screenshot.
    os.chmod(run_dir, 0o777)

    cmd = _docker_command(run_id, run_dir, url)
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=SCOUT_TIMEOUT_S, check=False)
    except subprocess.TimeoutExpired as exc:
        subprocess.run(["docker", "kill", f"mira-scout-{run_id}"], capture_output=True, check=False)
        raise ScoutError(f"scout exceeded {SCOUT_TIMEOUT_S}s and was killed") from exc

    if len(proc.stdout) > MAX_STDOUT_BYTES:
        raise ScoutError("scout stdout exceeded the size budget")
    lines = proc.stdout.decode("utf-8", errors="replace").strip().splitlines()
    if not lines:
        stderr_tail = proc.stderr.decode("utf-8", errors="replace")[-1000:]
        raise ScoutError(f"scout produced no output (exit {proc.returncode}): {stderr_tail}")
    try:
        evidence = json.loads(lines[-1])
    except json.JSONDecodeError as exc:
        raise ScoutError("scout output was not valid JSON") from exc
    if not isinstance(evidence, dict) or evidence.get("schema_version") != 1:
        raise ScoutError("scout output did not match schema_version 1")

    screenshot_path = _checked_screenshot(run_dir, evidence)
    (run_dir / "evidence.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    return ScoutRun(run_id, run_dir, proc.returncode, evidence, screenshot_path)


def _checked_screenshot(run_dir: Path, evidence: dict) -> Path | None:
    """Accept only the expected file name, a regular file, a sane size, real PNG bytes."""
    shot = evidence.get("screenshot")
    if not shot:
        return None
    if not isinstance(shot, dict) or shot.get("file") != "screenshot.png":
        raise ScoutError("unexpected screenshot reference")
    path = run_dir / "screenshot.png"
    if path.is_symlink() or not path.is_file():
        raise ScoutError("screenshot missing or not a regular file")
    size = path.stat().st_size
    if size == 0 or size > MAX_SCREENSHOT_BYTES:
        raise ScoutError(f"screenshot size out of bounds: {size}")
    with path.open("rb") as fh:
        if fh.read(8) != PNG_MAGIC:
            raise ScoutError("screenshot is not a PNG")
    return path


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: python -m backend.sandbox <url>", file=sys.stderr)
        return 2
    try:
        run = run_scout(argv[1])
    except ScoutError as exc:
        print(f"scout failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(run.evidence, indent=2))
    print(f"\nrun_id={run.run_id} exit={run.exit_code}", file=sys.stderr)
    print(f"artifacts={run.run_dir}", file=sys.stderr)
    return 0 if run.evidence.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
