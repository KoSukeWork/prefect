"""Back up the old stack and import into a new database volume; never delete volumes."""

import base64
import json
import subprocess
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ENV = dict(
    line.split("=", 1)
    for line in (ROOT / ".env").read_text().splitlines()
    if line and not line.startswith("#") and "=" in line
)


def compose(filename, *args):
    return subprocess.run(
        ["docker", "compose", "-f", str(ROOT / filename), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def main():
    old = "compose.multi.yaml"
    if compose("compose.yaml", "ps", "-q", "workbench"):
        raise RuntimeError(
            "Single-container workbench already exists; refusing to repeat migration"
        )
    token = base64.b64encode(("admin:" + ENV["PREFECT_PASSWORD"]).encode()).decode()

    def query(path, body):
        req = urllib.request.Request(
            f"http://127.0.0.1:{ENV.get('PREFECT_PORT', '4200')}/api" + path,
            data=json.dumps(body).encode(),
            headers={
                "Authorization": "Basic " + token,
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=15) as response:
            return json.load(response)

    runs = query(
        "/flow_runs/filter",
        {
            "flow_runs": {
                "state": {
                    "type": {"any_": ["RUNNING", "PENDING", "CANCELLING", "PAUSED"]}
                }
            },
            "limit": 1,
        },
    )
    if runs:
        raise RuntimeError("Finish or cancel active runs before migration")
    snapshot = {
        "deployments": query("/deployments/filter", {}),
        "run_count": query("/flow_runs/count", {}),
        "flow_count": query("/flows/count", {}),
    }
    backup = (
        ROOT
        / "backups"
        / ("single-migration-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    )
    backup.mkdir(parents=True)
    (backup / "snapshot.json").write_text(
        json.dumps(snapshot, indent=2), encoding="utf-8"
    )
    # Quiesce every writer, keeping PostgreSQL available for its consistent dump.
    print("Stopping legacy application services", flush=True)
    compose(old, "stop", "-t", "20", "manager", "editor", "worker", "server")
    compose(
        old,
        "exec",
        "-T",
        "postgres",
        "pg_dump",
        "-U",
        "prefect",
        "-Fc",
        "-f",
        "/tmp/workbench-migration.dump",
        "prefect",
    )
    dump = backup / "database.dump"
    compose(old, "cp", "postgres:/tmp/workbench-migration.dump", str(dump))
    compose(old, "cp", str(dump), "editor:/workspace/.workbench/legacy-database.dump")
    print(f"Database backup: {dump}", flush=True)
    compose(old, "down")
    print("Starting single container and importing backup", flush=True)
    print(
        compose("compose.yaml", "up", "-d", "--wait", "--wait-timeout", "300"),
        flush=True,
    )
    print(
        f"Migration complete; old database volume and backup retained: {backup}",
        flush=True,
    )


if __name__ == "__main__":
    main()
