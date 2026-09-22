"""Run flow imports in the selected business environment, outside the web server."""

import json
import sys

from prefect import flow
from prefect.client.orchestration import get_client
from prefect.client.schemas.actions import DeploymentUpdate
from prefect.exceptions import ObjectNotFound

entrypoint, name = sys.argv[1:3]
loaded = flow.from_source(source="/workspace", entrypoint=entrypoint)
with get_client(sync_client=True) as client:
    try:
        existing = client.read_deployment_by_name(f"{loaded.name}/{name}")
    except ObjectNotFound:
        existing = None
if (
    len(sys.argv) > 3
    and sys.argv[3]
    and (existing is None or str(existing.id) != sys.argv[3])
):
    raise ValueError("Flow 名称已改变，请另行注册；旧部署保持暂停")
variables = {
    "command": "/opt/venv/bin/python /opt/workbench/manager/engine.py",
    "env": {"WORKBENCH_DEPLOYMENT_KEY": f"{loaded.name}/{name}"},
}
if existing:
    if existing.work_pool_name != "local-process":
        raise ValueError("同名部署属于其他工作池，请使用不同部署名称")
    variables["env"] = {**existing.job_variables.get("env", {}), **variables["env"]}
    with get_client(sync_client=True) as client:
        client.update_deployment(
            existing.id,
            DeploymentUpdate(
                entrypoint=entrypoint,
                path="/workspace",
                parameter_openapi_schema=loaded.parameters.model_dump(),
                concurrency_limit=1,
                job_variables={**existing.job_variables, **variables},
            ),
        )
    identifier = existing.id
else:
    identifier = loaded.deploy(
        name=name,
        work_pool_name="local-process",
        build=False,
        push=False,
        concurrency_limit=1,
        job_variables=variables,
        print_next_steps=False,
    )
print(
    "WORKBENCH_RESULT="
    + json.dumps({"id": str(identifier), "flow": loaded.name, "name": name})
)
