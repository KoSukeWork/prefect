"""Immutable shared Python environments, promoted only after a successful build."""

import importlib.metadata
import json
import os
import re
import subprocess
import uuid
from pathlib import Path

from packaging.requirements import Requirement

ROOT = Path("/opt/workbench-env")


def status():
    current = ROOT / "current"
    if current.exists():
        return json.loads((current / "environment.json").read_text())
    return {"id": "base", "requirements": [], "python": "/opt/venv/bin/python"}


def install(requirements, log):
    cleaned = []
    for line in requirements.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        req = Requirement(line)
        if (
            req.url
            or req.marker
            or re.sub(r"[-_.]+", "-", req.name.lower()) == "prefect"
        ):
            raise ValueError(
                "只支持包名、版本和 extras；不允许修改 Prefect 或使用 URL / 环境标记。"
            )
        cleaned.append(str(req))
    generation = ROOT / ("env-" + uuid.uuid4().hex)
    generation.mkdir(parents=True)
    wheel = next(Path("/opt/prefect-dist").glob("prefect-*.whl"))
    constraints = sorted(
        {
            f"{d.metadata['Name']}=={d.version}"
            for d in importlib.metadata.distributions()
            if d.metadata["Name"].lower() != "prefect"
        }
    )
    project = (
        '[project]\nname = "workbench-business"\nversion = "0.0.0"\n'
        'requires-python = ">=3.12,<3.13"\n'
        f"dependencies = {json.dumps(['prefect', *cleaned])}\n"
        "[tool.uv.sources]\n"
        f"prefect = {{ path = {json.dumps(str(wheel))} }}\n"
        "[tool.uv]\n"
        f"constraint-dependencies = {json.dumps(constraints)}\n"
    )
    (generation / "pyproject.toml").write_text(project)
    env = {
        **os.environ,
        "UV_PROJECT_ENVIRONMENT": str(generation / ".venv"),
        "UV_PYTHON_DOWNLOADS": "never",
        "UV_LINK_MODE": "copy",
    }
    for command in [
        [
            "uv",
            "lock",
            "--project",
            str(generation),
            "--python",
            "/usr/local/bin/python",
        ],
        [
            "uv",
            "sync",
            "--project",
            str(generation),
            "--frozen",
            "--no-dev",
            "--python",
            "/usr/local/bin/python",
        ],
        [
            str(generation / ".venv/bin/python"),
            "-c",
            "import prefect; print(prefect.__version__)",
        ],
    ]:
        with log.open("a") as output:
            subprocess.run(
                command, env=env, stdout=output, stderr=output, check=True, timeout=600
            )
    record = {
        "id": generation.name,
        "requirements": cleaned,
        "python": str(generation / ".venv/bin/python"),
    }
    (generation / "environment.json").write_text(json.dumps(record))
    temporary = ROOT / ("next-" + uuid.uuid4().hex)
    temporary.symlink_to(generation.name)
    temporary.replace(ROOT / "current")
    return record
