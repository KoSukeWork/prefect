"""Real HTTP, worker and shared-environment acceptance for the management UI."""

import json
import time
import urllib.error
import urllib.request
import uuid

from acceptance import AUTH, ENV, ROOT, SINGLE, api, assert_logged, compose, wait_run

BASE = f"http://127.0.0.1:{ENV.get('WORKBENCH_PORT' if SINGLE else 'MANAGER_PORT', '8090')}"
PREFIX = "manager-test-" + uuid.uuid4().hex[:8]
CHECKS = []


def request(path, body=None, auth=True, csrf=True):
    headers = {"Content-Type": "application/json"}
    if auth:
        headers["Authorization"] = "Basic " + AUTH
    if csrf:
        headers["X-Workbench"] = "1"
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers=headers,
    )
    with urllib.request.urlopen(req, timeout=40) as response:
        return json.load(response)


def rejects(code, *args, **kwargs):
    try:
        request(*args, **kwargs)
    except urllib.error.HTTPError as exc:
        assert exc.code == code, exc.read().decode()
    else:
        raise AssertionError(f"Expected HTTP {code}")


def job(path, body, success=True, timeout=660):
    item = request(path, body)
    deadline = time.monotonic() + timeout
    while item["state"] == "running" and time.monotonic() < deadline:
        time.sleep(1)
        item = request("/api/jobs/" + item["id"])
    assert item["state"] == ("completed" if success else "failed"), item
    return item.get("result", item)


def passed(name):
    CHECKS.append(name)
    print("PASS " + name, flush=True)


def source(version, extra=False):
    return (
        "import sys, time, fcntl\nfrom prefect import flow, get_run_logger\n"
        + ("import humanfriendly\n" if extra else "")
        + f"@flow(name={PREFIX!r})\n"
        + "def demo(seconds: int = 0):\n"
        + f"    with open('/workspace/.workbench/{PREFIX}.lock', 'w') as lock:\n"
        + "        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)\n"
        + f"        get_run_logger().info({version!r})\n"
        + (
            "        get_run_logger().info('EXTRA=' + humanfriendly.__version__)\n"
            if extra
            else ""
        )
        + "        for _ in range(seconds):\n"
        + "            get_run_logger().info('ENV=' + sys.executable)\n"
        + "            time.sleep(1)\n"
        + "        get_run_logger().info('DONE_ENV=' + sys.executable)\n"
    )


def main():
    rejects(401, "/api/status", auth=False)
    rejects(403, "/api/login", {"password": "wrong"}, auth=False, csrf=False)
    rejects(401, "/api/login", {"password": "wrong"}, auth=False)
    rejects(400, "/api/file?path=../etc/passwd.py")
    rejects(400, "/api/file", {"path": "flows/invalid.py", "content": "def broken("})
    passed("authentication, CSRF, traversal and syntax rejection")

    filename = f"flows/{PREFIX}.py"
    revision = request("/api/file", {"path": filename, "content": source("VERSION_A")})[
        "revision"
    ]
    rejects(409, "/api/file", {"path": filename, "content": source("BAD")})
    deployment = job(
        "/api/register", {"entrypoint": filename + ":demo", "name": "acceptance"}
    )["id"]
    action = f"/api/deployments/{deployment}/action"
    run = job(action, {"action": "start"})["run_id"]
    wait_run(run)
    assert_logged(run, "VERSION_A")
    passed("create script, revision protection, register and execute real flow")

    # Include a schedule and default parameters to protect existing configuration.
    api(f"/deployments/{deployment}", {"parameters": {"seconds": 0}}, "PATCH")
    api(
        f"/deployments/{deployment}/schedules",
        [{"schedule": {"cron": "0 0 1 1 *", "timezone": "UTC"}, "active": False}],
    )
    old = job(action, {"action": "start", "parameters": {"seconds": 300}})["run_id"]
    wait_run(old, "RUNNING")
    assert_logged(old, "VERSION_A")
    duplicate = job(action, {"action": "start"})
    assert "run_id" not in duplicate, duplicate
    revision = request(
        "/api/file",
        {"path": filename, "content": source("VERSION_B"), "revision": revision},
    )["revision"]
    new = job(action, {"action": "restart", "parameters": {"seconds": 300}})["run_id"]
    wait_run(old, "CANCELLED")
    wait_run(new, "RUNNING")
    assert_logged(new, "VERSION_B")
    item = api(f"/deployments/{deployment}")
    assert len(item["schedules"]) == 1 and item["parameters"] == {"seconds": 0}, item
    passed(
        "duplicate start prevented; restart cancels old process, loads new code and preserves configuration"
    )

    old_env = request("/api/status")["environment"]["python"]
    current = job("/api/environment", {"requirements": "humanfriendly==10.0"})
    assert api(f"/flow_runs/{new}")["state"]["type"] == "RUNNING"
    assert_logged(new, "ENV=" + old_env)
    commands = [
        compose(
            "exec",
            "-T",
            service,
            "/opt/workbench/bin/python",
            "-c",
            "import sys, humanfriendly; print(sys.executable); print(humanfriendly.__version__)",
        )
        for service in ["editor", "worker"]
    ]
    assert commands[0] == commands[1] == current["python"] + "\n10.0", commands
    passed(
        "dependency installation promotes identical editor/worker environment while existing run stays alive"
    )

    revision = request(
        "/api/file",
        {
            "path": filename,
            "content": source("VERSION_C", extra=True),
            "revision": revision,
        },
    )["revision"]
    latest = job(action, {"action": "restart"})["run_id"]
    wait_run(new, "CANCELLED")
    wait_run(latest)
    assert_logged(latest, "EXTRA=10.0")
    assert_logged(latest, "DONE_ENV=" + current["python"])
    passed("new execution imports added package using promoted interpreter")

    job("/api/environment", {"requirements": "pydantic==1.10.0"}, success=False)
    assert request("/api/status")["environment"]["id"] == current["id"]
    passed("conflicting dependency fails without changing active environment")

    long_run = job(action, {"action": "start", "parameters": {"seconds": 300}})[
        "run_id"
    ]
    wait_run(long_run, "RUNNING")
    job(action, {"action": "stop"})
    wait_run(long_run, "CANCELLED")
    assert api(f"/deployments/{deployment}")["paused"]
    passed("stop cancels process and pauses scheduling")

    compose(
        "up",
        "-d",
        "--force-recreate",
        "--no-deps",
        "--wait",
        "--wait-timeout",
        "180",
        "manager",
        "worker",
        "editor",
    )
    assert request("/api/status")["environment"]["id"] == current["id"]
    assert request("/api/file?path=" + filename)["revision"] == revision
    run = job(action, {"action": "start"})["run_id"]
    wait_run(run)
    assert_logged(run, "EXTRA=10.0")
    passed(
        "environment, code and registrations survive container recreation and execute successfully"
    )

    api(f"/deployments/{deployment}", method="DELETE")
    compose(
        "exec",
        "-T",
        "editor",
        "/opt/venv/bin/python",
        "-c",
        f"from pathlib import Path; Path('/workspace/{filename}').unlink(); Path('/workspace/.workbench/{PREFIX}.lock').unlink(missing_ok=True)",
    )
    (ROOT / "manager-acceptance-results.json").write_text(
        json.dumps(
            {
                "passed": CHECKS,
                "test_flow": PREFIX,
                "environment": current,
                "finished": time.strftime("%Y-%m-%d %H:%M:%S"),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    passed(
        "temporary test deployment and source cleaned up; audit run history retained"
    )


if __name__ == "__main__":
    main()
