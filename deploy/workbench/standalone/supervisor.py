"""Run one local workbench container with ordered startup and shutdown."""

import json
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import quote

PYTHON = "/opt/venv/bin/python"
PG = "/usr/lib/postgresql/17/bin/"
DATABASE = Path("/var/lib/workbench-postgres")
READY = Path("/tmp/workbench-ready.json")
PROCESSES = []
STOPPING = False


def stop_signal(signum, frame):
    global STOPPING
    STOPPING = True


def check_children():
    if STOPPING:
        raise InterruptedError("Container shutdown requested")
    for name, process in PROCESSES:
        if process.poll() is not None:
            raise RuntimeError(f"{name} exited with code {process.returncode}")


def start(name, command, env):
    print(f"[workbench] Starting {name}", flush=True)
    process = subprocess.Popen(command, env=env, start_new_session=True)
    PROCESSES.append((name, process))


def wait_http(url, timeout=180):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        check_children()
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status == 200:
                    return
        except (OSError, urllib.error.URLError):
            pass
        time.sleep(0.5)
    raise TimeoutError(f"Service did not become ready: {url}")


def database(env):
    if not (DATABASE / "PG_VERSION").exists():
        password = Path("/tmp/workbench-pg-password")
        password.touch(mode=0o600)
        password.write_text(env["POSTGRES_PASSWORD"])
        try:
            subprocess.run(
                [
                    PG + "initdb",
                    "-D",
                    str(DATABASE),
                    "-U",
                    "prefect",
                    "--encoding=UTF8",
                    "--locale=C.UTF-8",
                    "--auth-local=trust",
                    "--auth-host=scram-sha-256",
                    "--pwfile",
                    str(password),
                ],
                check=True,
            )
        finally:
            password.unlink(missing_ok=True)
    start(
        "postgres",
        [
            PG + "postgres",
            "-D",
            str(DATABASE),
            "-h",
            "127.0.0.1",
            "-k",
            "/tmp/workbench-postgres",
        ],
        env,
    )
    deadline = time.monotonic() + 60
    while subprocess.run(
        [PG + "pg_isready", "-h", "/tmp/workbench-postgres", "-U", "prefect"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    ).returncode:
        check_children()
        if time.monotonic() > deadline:
            raise TimeoutError("PostgreSQL did not become ready")
        time.sleep(0.5)
    marker = DATABASE / "workbench-initialized"
    if not marker.exists():
        # Refuse to reuse a partially restored database after an interrupted import.
        subprocess.run(
            [
                PG + "createdb",
                "-h",
                "/tmp/workbench-postgres",
                "-U",
                "prefect",
                "prefect",
            ],
            check=True,
        )
        dump = Path("/workspace/.workbench/legacy-database.dump")
        if dump.exists():
            print("[workbench] Importing legacy database backup", flush=True)
            subprocess.run(
                [
                    PG + "pg_restore",
                    "-h",
                    "/tmp/workbench-postgres",
                    "-U",
                    "prefect",
                    "-d",
                    "prefect",
                    "--exit-on-error",
                    "--no-owner",
                    "--no-acl",
                    str(dump),
                ],
                check=True,
            )
        marker.touch()


def shutdown():
    READY.unlink(missing_ok=True)
    for name, process in reversed(PROCESSES):
        print(f"[workbench] Stopping {name}", flush=True)
        sig = signal.SIGINT if name in {"worker", "postgres"} else signal.SIGTERM
        try:
            os.killpg(process.pid, sig)
            process.wait(timeout=20 if name == "worker" else 5)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            pass
        finally:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()


def main():
    signal.signal(signal.SIGTERM, stop_signal)
    signal.signal(signal.SIGINT, stop_signal)
    READY.unlink(missing_ok=True)
    env = dict(os.environ)
    password = env["PREFECT_PASSWORD"]
    env.update(
        PREFECT_API_URL="http://127.0.0.1:4200/api",
        PREFECT_API_AUTH_STRING="admin:" + password,
        PREFECT_UI_URL=env.get("WORKBENCH_PUBLIC_URL", "http://localhost:8090").rstrip(
            "/"
        )
        + "/prefect",
        PREFECT_SERVER_DATABASE_CONNECTION_URL="postgresql+asyncpg://prefect:"
        + quote(env["POSTGRES_PASSWORD"], safe="")
        + "@127.0.0.1:5432/prefect",
        PREFECT_SERVER_UI_API_URL="/prefect/api",
        PREFECT_SERVER_UI_SERVE_BASE="/prefect",
        PREFECT_SERVER_UI_STATIC_DIRECTORY="/home/app/.prefect/ui",
        PREFECT_SERVER_ALLOW_EPHEMERAL_MODE="false",
        PREFECT_WORKER_QUERY_SECONDS="3",
        PREFECT_WORKER_PREFETCH_SECONDS="5",
        WORKBENCH_EDITOR_URL="/editor/",
        WORKBENCH_PREFECT_URL="/prefect/",
    )
    # Only the authenticated gateway is externally reachable. Internal APIs bind
    # to loopback; the SDK credential remains the workbench login secret.
    env.pop("PREFECT_SERVER_API_AUTH_STRING", None)
    os.environ.update(env)
    try:
        subprocess.run(
            [PYTHON, "/usr/local/bin/workbench", "init"], env=env, check=True
        )
        database(env)
        start(
            "server",
            [
                PYTHON,
                "-m",
                "prefect",
                "server",
                "start",
                "--host",
                "127.0.0.1",
                "--port",
                "4200",
            ],
            env,
        )
        wait_http("http://127.0.0.1:4200/api/health")
        start(
            "worker",
            [
                PYTHON,
                "-m",
                "prefect",
                "worker",
                "start",
                "--pool",
                "local-process",
                "--type",
                "process",
                "--limit",
                env.get("WORKER_LIMIT", "4"),
                "--install-policy",
                "never",
                "--with-healthcheck",
            ],
            {
                **env,
                "PREFECT_WORKER_WEBSERVER_HOST": "127.0.0.1",
                "PREFECT_WORKER_WEBSERVER_PORT": "8082",
            },
        )
        wait_http("http://127.0.0.1:8082/health")
        start(
            "editor",
            [
                "/bin/sh",
                "/usr/local/bin/editor-entrypoint.sh",
                "code-server",
                "--bind-addr",
                "127.0.0.1:8081",
                "--auth",
                "none",
                "--disable-telemetry",
                "--locale",
                "zh-cn",
                "/workspace",
            ],
            env,
        )
        start(
            "manager",
            [
                PYTHON,
                "-m",
                "uvicorn",
                "app:app",
                "--app-dir",
                "/opt/workbench/manager",
                "--host",
                "127.0.0.1",
                "--port",
                "8091",
            ],
            env,
        )
        wait_http("http://127.0.0.1:8081/healthz")
        wait_http("http://127.0.0.1:8091/")
        Path("/tmp/workbench-nginx").mkdir(exist_ok=True)
        start(
            "gateway",
            [
                "nginx",
                "-c",
                "/opt/workbench/standalone/nginx.conf",
                "-g",
                "daemon off;",
            ],
            env,
        )
        wait_http("http://127.0.0.1:8090/")
        READY.write_text(json.dumps({name: p.pid for name, p in PROCESSES}))
        print("[workbench] Ready on port 8090", flush=True)
        while not STOPPING:
            check_children()
            time.sleep(0.5)
    finally:
        shutdown()


if __name__ == "__main__":
    try:
        main()
    except InterruptedError:
        sys.exit(0)
