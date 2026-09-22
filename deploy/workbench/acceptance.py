"""Run against the local stack: uv run --no-project acceptance.py.

Creates uniquely named demo flows; never sends external messages. Recreates this
Compose project's containers to verify persistence, without removing volumes.
"""

import base64
import json
import os
import subprocess
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ENV = dict(
    line.split("=", 1)
    for line in (ROOT / ".env").read_text().splitlines()
    if line and not line.startswith("#") and "=" in line
)
SINGLE = os.environ.get("WORKBENCH_LAYOUT", "single") == "single"
ORIGIN = f"http://127.0.0.1:{ENV.get('WORKBENCH_PORT', '8090')}"
BASE = (
    ORIGIN + "/prefect"
    if SINGLE
    else f"http://127.0.0.1:{ENV.get('PREFECT_PORT', '4200')}"
)
AUTH = base64.b64encode(f"admin:{ENV['PREFECT_PASSWORD']}".encode()).decode()
PREFIX = f"acceptance-{uuid.uuid4().hex[:8]}"
CHECKS = []


def compose(*args):
    if SINGLE:
        args = tuple(
            "workbench" if a in {"server", "worker", "editor", "manager"} else a
            for a in args
        )
        if args[0] == "up":
            args = tuple(
                a
                for i, a in enumerate(args)
                if a != "workbench" or "workbench" not in args[:i]
            )
    filename = "compose.yaml" if SINGLE else "compose.multi.yaml"
    result = subprocess.run(
        ["docker", "compose", "-f", str(ROOT / filename), *args],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return result.stdout.strip()


def api(path, data=None, method=None):
    request = urllib.request.Request(
        BASE + "/api" + path,
        data=json.dumps(data).encode() if data is not None else None,
        headers={"Authorization": f"Basic {AUTH}", "Content-Type": "application/json"},
        method=method,
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        body = response.read()
        return json.loads(body) if body else None


def passed(name):
    CHECKS.append(name)
    print(f"PASS {name}", flush=True)


def write_flow(version):
    source = (
        "import time\nfrom prefect import flow, get_run_logger\n"
        f"@flow(name={PREFIX!r})\n"
        "def demo(seconds: int = 0):\n"
        f"    get_run_logger().info({version!r})\n"
        "    time.sleep(seconds)\n"
        f"    return {version!r}\n"
    )
    script = (
        "from pathlib import Path; "
        f"Path('/workspace/flows/{PREFIX}.py').write_text({source!r})"
    )
    compose("exec", "-T", "editor", "python", "-c", script)


def wait_run(run_id, target="COMPLETED", timeout=120):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        run = api(f"/flow_runs/{run_id}")
        state = run["state"]["type"]
        if state == target:
            return run
        if state in {"FAILED", "CRASHED", "CANCELLED", "COMPLETED"}:
            raise AssertionError(
                f"Run {run_id}: expected {target}, got {state}: {run['state']}"
            )
        time.sleep(1)
    raise AssertionError(f"Timed out waiting for {target}: {run_id}")


def start(deployment_id, seconds=0, scheduled=None):
    body = {"parameters": {"seconds": seconds}}
    if scheduled:
        body["state"] = {
            "type": "SCHEDULED",
            "state_details": {"scheduled_time": scheduled.isoformat()},
        }
    return api(f"/deployments/{deployment_id}/create_flow_run", body)["id"]


def assert_logged(run_id, message):
    # Run state and buffered log delivery are independent API writes.
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        logs = api("/logs/filter", {"logs": {"flow_run_id": {"any_": [run_id]}}})
        if any(message in entry["message"] for entry in logs):
            return
        time.sleep(1)
    raise AssertionError(f"Expected {message!r} in run logs: {logs}")


def main():
    compose("config", "--quiet")
    for service in ["workbench"] if SINGLE else ["server", "worker", "editor"]:
        cid = compose("ps", "-q", service)
        details = json.loads(subprocess.check_output(["docker", "inspect", cid]))[0]
        assert details["State"]["Health"]["Status"] == "healthy"
        assert details["Config"]["User"] == "app"
        assert not details["HostConfig"]["Privileged"]
        assert all("docker.sock" not in m["Destination"] for m in details["Mounts"])
    passed("healthy non-root services, no Docker socket or privileged mode")

    try:
        urllib.request.urlopen(BASE + "/api/deployments/filter", data=b"{}", timeout=10)
    except urllib.error.HTTPError as exc:
        assert exc.code == 401, exc
    else:
        raise AssertionError("Prefect API accepted an unauthenticated request")
    editor_url = (
        ORIGIN + "/editor/"
        if SINGLE
        else f"http://127.0.0.1:{ENV.get('EDITOR_PORT', '8080')}"
    )
    with urllib.request.urlopen(editor_url, timeout=10) as response:
        assert response.url == ORIGIN + "/" if SINGLE else "/login" in response.url
    for path in ["/", "/v2/"]:
        request = urllib.request.Request(
            BASE + path, headers={"Authorization": f"Basic {AUTH}"}
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            assert b"<html" in response.read().lower()
    passed("Prefect API and editor authentication; both packaged UI bundles served")

    versions = [
        compose(
            "exec",
            "-T",
            s,
            "python",
            "-c",
            "import prefect; print(prefect.__version__)",
        )
        for s in ["editor", "worker"]
    ]
    assert versions[0] == versions[1], versions
    passed(f"identical editor/worker Prefect version: {versions[0]}")
    shell_version = compose(
        "exec",
        "-T",
        "editor",
        "bash",
        "-lc",
        "python -c 'import prefect; print(prefect.__version__)'",
    )
    assert shell_version == versions[0], shell_version
    passed("browser login shell resolves the same Prefect Python environment")

    write_flow("version-one")
    for name in ["short", "long"]:
        compose(
            "exec",
            "-T",
            "editor",
            "python",
            "/usr/local/bin/workbench",
            "deploy",
            f"flows/{PREFIX}.py:demo",
            "--name",
            name,
        )
    short = api(f"/deployments/name/{PREFIX}/short")["id"]
    long = api(f"/deployments/name/{PREFIX}/long")["id"]
    first = start(short)
    wait_run(first)
    assert_logged(first, "version-one")
    passed(
        "file created in editor, deployment registered, worker executes and stores logs"
    )

    write_flow("version-two")
    second = start(short)
    wait_run(second)
    assert_logged(second, "version-two")
    passed(
        "saved code takes effect in the next run without rebuilding or registering again"
    )

    long_run = start(long, seconds=300)
    wait_run(long_run, "RUNNING")
    short_run = start(short)
    wait_run(short_run)
    assert api(f"/flow_runs/{long_run}")["state"]["type"] == "RUNNING"
    duplicate = start(long)
    time.sleep(8)
    assert api(f"/flow_runs/{duplicate}")["state"]["type"] != "RUNNING"
    api(f"/flow_runs/{duplicate}/set_state", {"state": {"type": "CANCELLING"}})
    wait_run(duplicate, "CANCELLED")
    api(f"/flow_runs/{long_run}/set_state", {"state": {"type": "CANCELLING"}})
    wait_run(long_run, "CANCELLED")
    replacement = start(long)
    wait_run(replacement)
    passed(
        "long and short tasks coexist; deployment concurrency=1; cancel and restart succeeds"
    )

    # A real deployment interval schedule, generated by the server scheduler.
    schedule = api(
        f"/deployments/{short}/schedules",
        [
            {
                "schedule": {
                    "interval": 10,
                    "anchor_date": (
                        datetime.now(timezone.utc) + timedelta(seconds=10)
                    ).isoformat(),
                    "timezone": "Asia/Shanghai",
                },
                "active": True,
            }
        ],
    )[0]
    try:
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            runs = api(
                "/flow_runs/filter",
                {
                    "deployments": {"id": {"any_": [short]}},
                    "flow_runs": {
                        "state": {"type": {"any_": ["COMPLETED"]}},
                    },
                },
            )
            if any(run["auto_scheduled"] for run in runs):
                break
            time.sleep(2)
        else:
            raise AssertionError("Scheduler did not execute the interval deployment")
    finally:
        api(f"/deployments/{short}/schedules/{schedule['id']}", method="DELETE")
    passed("server-created interval schedule executes successfully")

    before = compose("exec", "-T", "editor", "cat", f"/workspace/flows/{PREFIX}.py")
    compose("up", "-d", "--force-recreate", "--wait", "--wait-timeout", "240")
    after = compose("exec", "-T", "editor", "cat", f"/workspace/flows/{PREFIX}.py")
    assert before == after
    assert api(f"/deployments/{short}")["id"] == short
    assert api(f"/flow_runs/{first}")["state"]["type"] == "COMPLETED"
    final = start(short)
    wait_run(final)
    assert_logged(final, "version-two")
    passed(
        "database, source, deployment and history survive full container recreation; new run succeeds"
    )

    # Keep the useful hello/listener examples, remove only this run's test deployments.
    for deployment_id in [short, long]:
        api(f"/deployments/{deployment_id}", method="DELETE")
    compose(
        "exec",
        "-T",
        "editor",
        "python",
        "-c",
        f"from pathlib import Path; Path('/workspace/flows/{PREFIX}.py').unlink()",
    )
    report = {
        "tested_at": datetime.now(timezone.utc).isoformat(),
        "checks": CHECKS,
        "result": "passed",
        "test_flow": PREFIX,
    }
    (ROOT / "acceptance-results.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, indent=2), flush=True)


if __name__ == "__main__":
    main()
