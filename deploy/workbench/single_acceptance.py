"""Validate single-port authentication, migration, and supervisor recovery."""

import http.cookiejar
import json
import re
import subprocess
import time
import urllib.error
import urllib.request
from urllib.parse import urljoin

from acceptance import ENV, ORIGIN, ROOT, api, compose

CHECKS = []


def passed(message):
    CHECKS.append(message)
    print("PASS " + message, flush=True)


def inspect():
    return json.loads(
        subprocess.check_output(["docker", "inspect", compose("ps", "-q", "workbench")])
    )[0]


def main():
    containers = compose("ps", "--services", "--status", "running").splitlines()
    assert containers == ["workbench"], containers
    details = inspect()
    ports = details["HostConfig"]["PortBindings"]
    assert (
        list(ports) == ["8090/tcp"] and ports["8090/tcp"][0]["HostIp"] == "127.0.0.1"
    ), ports
    assert (
        details["Config"]["User"] == "app" and not details["HostConfig"]["Privileged"]
    )
    assert all("docker.sock" not in m["Destination"] for m in details["Mounts"])
    listeners = compose("exec", "-T", "workbench", "ss", "-lntH")
    for line in listeners.splitlines():
        address = line.split()[3]
        if address.endswith(":8090") or address.startswith("127."):
            continue
        raise AssertionError("Unexpected externally reachable listener: " + line)
    passed("one non-root container, one published port, internal services on loopback")

    try:
        urllib.request.urlopen(ORIGIN + "/prefect/api/flow_runs/filter", data=b"{}")
    except urllib.error.HTTPError as error:
        assert error.code == 401, error
    else:
        raise AssertionError("Unauthenticated Prefect API was exposed")
    for path in ["/editor/", "/editor/healthz", "/prefect/v2/"]:
        with urllib.request.urlopen(ORIGIN + path) as response:
            assert response.url == ORIGIN + "/", response.url
    jar = http.cookiejar.CookieJar()
    browser = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    login = urllib.request.Request(
        ORIGIN + "/api/login",
        data=json.dumps({"password": ENV["PREFECT_PASSWORD"]}).encode(),
        headers={"Content-Type": "application/json", "X-Workbench": "1"},
    )
    with browser.open(login) as response:
        assert json.load(response)["ok"]
    with browser.open(ORIGIN + "/editor/healthz") as response:
        assert json.load(response)["status"] in {"alive", "expired"}
    with browser.open(ORIGIN + "/prefect/ui-settings") as response:
        settings = json.load(response)
        assert settings["api_url"] == "/prefect/api" and settings["auth"] is None, (
            settings
        )
    for path in ["/prefect/", "/prefect/v2/"]:
        with browser.open(ORIGIN + path) as response:
            html = response.read().decode()
        assets = re.findall(r'<script[^>]+src="([^"]+)"', html)
        assert assets, html[:300]
        with browser.open(urljoin(ORIGIN + path, assets[0])) as response:
            assert (
                response.status == 200
                and "javascript" in response.headers["Content-Type"]
            )
    malicious = urllib.request.Request(
        ORIGIN + "/prefect/api/flow_runs/filter",
        data=b"{}",
        headers={"Content-Type": "application/json", "Sec-Fetch-Site": "cross-site"},
    )
    try:
        browser.open(malicious)
    except urllib.error.HTTPError as error:
        assert error.code == 403, error
    else:
        raise AssertionError("Gateway accepted a cross-site request")
    malicious.add_header("Sec-Fetch-Site", "same-site")
    malicious.add_header("Origin", "http://127.0.0.1:9999")
    try:
        browser.open(malicious)
    except urllib.error.HTTPError as error:
        assert error.code == 403, error
    else:
        raise AssertionError("Gateway accepted a different localhost origin")
    passed(
        "one session authenticates editor, both Prefect UIs and API; unauthenticated and cross-site requests rejected"
    )

    backups = sorted((ROOT / "backups").glob("single-migration-*/snapshot.json"))
    if backups:
        snapshot = json.loads(backups[-1].read_text())
        for deployment in snapshot["deployments"]:
            assert api("/deployments/" + deployment["id"])["name"] == deployment["name"]
        assert api("/flow_runs/count", {}) >= snapshot["run_count"]
        assert api("/flows/count", {}) >= snapshot["flow_count"]
        passed("pre-migration deployments, flow records and run history preserved")

    active = api(
        "/flow_runs/filter",
        {
            "flow_runs": {
                "state": {"type": {"any_": ["RUNNING", "PENDING", "CANCELLING"]}}
            },
            "limit": 1,
        },
    )
    assert not active, "Finish active runs before testing process recovery"
    before = inspect()
    compose(
        "exec",
        "-T",
        "workbench",
        "/opt/venv/bin/python",
        "-c",
        "import json, os, signal; from pathlib import Path; os.kill(json.loads(Path('/tmp/workbench-ready.json').read_text())['manager'], signal.SIGKILL)",
    )
    deadline = time.monotonic() + 150
    while time.monotonic() < deadline:
        current = inspect()
        if (
            current["RestartCount"] > before["RestartCount"]
            and current["State"].get("Health", {}).get("Status") == "healthy"
        ):
            break
        time.sleep(2)
    else:
        raise AssertionError("Supervisor did not recover from a terminated manager")
    with browser.open(ORIGIN + "/api/status") as response:
        status = json.load(response)
        assert status["deployments"]
    passed(
        "critical-process failure triggers orderly shutdown, automatic restart and healthy authenticated recovery"
    )
    # A disposable instance has no inherited data, credentials or network access.
    temporary = "prefect-workbench-fresh-acceptance"
    subprocess.run(
        [
            "docker",
            "run",
            "-d",
            "--name",
            temporary,
            "--network",
            "none",
            "--init",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "-e",
            "PREFECT_PASSWORD=disposable-acceptance",
            "-e",
            "POSTGRES_PASSWORD=disposable-acceptance",
            details["Image"],
        ],
        check=True,
        capture_output=True,
    )
    try:
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            health = subprocess.run(
                [
                    "docker",
                    "exec",
                    temporary,
                    "/opt/venv/bin/python",
                    "/opt/workbench/standalone/health.py",
                ],
                check=False,
                capture_output=True,
            )
            if health.returncode == 0:
                break
            time.sleep(2)
        else:
            raise AssertionError(
                "Fresh offline container did not initialize: "
                + health.stderr.decode(errors="replace")
            )
        subprocess.run(
            [
                "docker",
                "exec",
                temporary,
                "/opt/venv/bin/python",
                "-c",
                "from pathlib import Path; assert Path('/workspace/flows/hello.py').exists(); assert Path('/home/app/.local/share/code-server/languagepacks.json').exists()",
            ],
            check=True,
            capture_output=True,
        )
        passed(
            "fresh container initializes database, example workspace and Chinese editor without network access"
        )
    finally:
        subprocess.run(
            ["docker", "rm", "-f", temporary], check=True, capture_output=True
        )
    (ROOT / "single-acceptance-results.json").write_text(
        json.dumps(
            {
                "checks": CHECKS,
                "result": "passed",
                "finished": time.strftime("%Y-%m-%d %H:%M:%S"),
            },
            indent=2,
        ),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
