#!/usr/bin/env python
"""Small workspace helper; Prefect owns scheduling and run orchestration."""

import argparse
import shutil
from pathlib import Path

from prefect import flow


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init")
    deploy = commands.add_parser("deploy")
    deploy.add_argument(
        "entrypoint", help="Relative path:function, e.g. flows/hello.py:hello"
    )
    deploy.add_argument("--name", default="local")
    deploy.add_argument("--cron")
    args = parser.parse_args()
    if args.command == "init":
        for source in Path("/opt/workbench-seed").rglob("*"):
            destination = Path("/workspace") / source.relative_to("/opt/workbench-seed")
            if source.is_dir():
                destination.mkdir(parents=True, exist_ok=True)
            elif not destination.exists():
                shutil.copyfile(source, destination)
        return
    file_path, separator, function = args.entrypoint.partition(":")
    resolved = (Path("/workspace") / file_path).resolve()
    if not separator or not function or not resolved.is_relative_to("/workspace"):
        parser.error("entrypoint must be a file:function inside /workspace")
    if not resolved.is_file():
        parser.error(f"Flow file does not exist: {file_path}")
    loaded = flow.from_source(source="/workspace", entrypoint=args.entrypoint)
    loaded.deploy(
        name=args.name,
        work_pool_name="local-process",
        build=False,
        push=False,
        cron=args.cron,
        concurrency_limit=1,
        job_variables={
            "command": "/opt/venv/bin/python /opt/workbench/manager/engine.py",
            "env": {"WORKBENCH_DEPLOYMENT_KEY": f"{loaded.name}/{args.name}"},
        },
    )


if __name__ == "__main__":
    main()
