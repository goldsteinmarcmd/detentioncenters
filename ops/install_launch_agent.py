"""Install the daily data-refresh LaunchAgent for a dedicated worker clone."""

from __future__ import annotations

import argparse
import os
import plistlib
import subprocess
from pathlib import Path

LABEL = "io.github.goldsteinmarcmd.detentioncenters.refresh"
TEMPLATE = Path(__file__).with_name(f"{LABEL}.plist.template")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repo", type=Path, help="Absolute path to the worker clone")
    args = parser.parse_args()

    repo = args.repo.expanduser().resolve()
    refresh = repo / "scripts" / "refresh-and-publish.sh"
    if not refresh.exists() or not (repo / ".git").exists():
        parser.error(f"{repo} is not a bootstrapped detentioncenters worker clone")

    logs = Path.home() / "Library" / "Logs" / "detentioncenters"
    agents = Path.home() / "Library" / "LaunchAgents"
    logs.mkdir(parents=True, exist_ok=True)
    agents.mkdir(parents=True, exist_ok=True)
    destination = agents / f"{LABEL}.plist"

    content = TEMPLATE.read_text().replace("__REPO_DIR__", str(repo)).replace(
        "__LOG_DIR__", str(logs)
    )
    payload = plistlib.loads(content.encode())
    destination.write_bytes(plistlib.dumps(payload, sort_keys=False))

    domain = f"gui/{os.getuid()}"
    subprocess.run(
        ["launchctl", "bootout", domain, str(destination)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    subprocess.run(["launchctl", "bootstrap", domain, str(destination)], check=True)
    subprocess.run(["launchctl", "enable", f"{domain}/{LABEL}"], check=True)
    print(f"Installed {destination}")
    print(f"Daily logs: {logs}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
