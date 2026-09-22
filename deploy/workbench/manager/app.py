"""Single-user local management UI; uses only public Prefect orchestration APIs."""

import asyncio
import hashlib
import hmac
import json
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Annotated
from urllib.parse import urlsplit

import httpx
from environment import install, status
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel, Field

ROOT = Path("/workspace")
DATA = ROOT / ".workbench"
DATA.mkdir(exist_ok=True)
JOBS = DATA / "jobs"
JOBS.mkdir(exist_ok=True)
BUSY = asyncio.Lock()
TASKS = set()
AUTH = HTTPBasic(auto_error=False)
logger = logging.getLogger(__name__)
API = os.environ["PREFECT_API_URL"]
USER, PASSWORD = os.environ["PREFECT_API_AUTH_STRING"].split(":", 1)


def authenticated(
    request: Request, credentials: Annotated[HTTPBasicCredentials | None, Depends(AUTH)]
):
    if request.method != "GET" and request.headers.get("x-workbench") != "1":
        raise HTTPException(403, "缺少请求校验")
    if request.headers.get("sec-fetch-site") == "cross-site":
        raise HTTPException(403, "不允许跨站请求")
    origin = request.headers.get("origin")
    host = request.headers.get("x-forwarded-host", request.headers.get("host", ""))
    if origin and urlsplit(origin).netloc.lower() != host.lower():
        raise HTTPException(403, "请求来源与工作台地址不一致")
    if request.url.path in {"/", "/api/login"}:
        return
    valid = (
        credentials
        and hmac.compare_digest(credentials.username.encode(), USER.encode())
        and hmac.compare_digest(credentials.password.encode(), PASSWORD.encode())
    )
    cookie = request.cookies.get("workbench-session", "")
    stamp, _, signature = cookie.partition(".")
    if stamp.isdigit() and 0 <= time.time() - int(stamp) < 43200:
        valid = valid or hmac.compare_digest(signature, session_signature(stamp))
    if not valid:
        raise HTTPException(401, "请登录工作台")


def session_signature(stamp):
    return hmac.new(
        PASSWORD.encode(), ("workbench-session:" + stamp).encode(), hashlib.sha256
    ).hexdigest()


app = FastAPI(dependencies=[Depends(authenticated)])


class Login(BaseModel):
    password: str


@app.post("/api/login")
def login(body: Login):
    if not hmac.compare_digest(body.password.encode(), PASSWORD.encode()):
        raise HTTPException(401, "密码不正确")
    stamp = str(int(time.time()))
    response = JSONResponse({"ok": True})
    response.set_cookie(
        "workbench-session",
        stamp + "." + session_signature(stamp),
        httponly=True,
        samesite="strict",
        max_age=43200,
    )
    return response


async def api(path, body=None, method=None):
    async with httpx.AsyncClient(auth=(USER, PASSWORD), timeout=30) as client:
        response = await client.request(
            method or ("POST" if body is not None else "GET"), API + path, json=body
        )
        response.raise_for_status()
        return response.json() if response.content else None


def file_path(path):
    result = (ROOT / path).resolve()
    if (
        not result.is_relative_to(ROOT)
        or ".workbench" in result.relative_to(ROOT).parts
        or result.suffix != ".py"
    ):
        raise HTTPException(400, "请选择工作区内的 Python 文件")
    return result


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def save_job(job):
    target = JOBS / (job["id"] + ".json")
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(job, ensure_ascii=False))
    temporary.replace(target)


@app.on_event("startup")
async def recover():
    for path in JOBS.glob("*.json"):
        job = json.loads(path.read_text())
        if job["state"] == "running":
            job.update(
                state="failed", error="管理服务已重启，操作中断。请检查任务状态后重试。"
            )
            save_job(job)


async def submit(operation):
    if BUSY.locked():
        raise HTTPException(409, "已有操作正在执行，请等待完成")
    await BUSY.acquire()
    job = {"id": uuid.uuid4().hex, "state": "running", "started": time.time()}
    save_job(job)

    async def execute():
        try:
            job["result"] = await operation(job)
            job["state"] = "completed"
        except Exception as exc:
            logger.exception("Workbench operation failed")
            job.update(state="failed", error=str(exc))
        finally:
            save_job(job)
            BUSY.release()

    task = asyncio.create_task(execute())
    TASKS.add(task)
    task.add_done_callback(TASKS.discard)
    return job


@app.get("/")
def index():
    return FileResponse("/opt/workbench/manager/index.html")


@app.get("/api/session")
def session():
    """Authentication subrequest for the single-port gateway."""
    return {"authenticated": True}


@app.get("/api/status")
async def overview():
    deployments = await api(
        "/deployments/filter",
        {"work_pools": {"name": {"any_": ["local-process"]}}, "limit": 200},
    )
    runs = await api(
        "/flow_runs/filter",
        {
            "work_pools": {"name": {"any_": ["local-process"]}},
            "limit": 100,
            "sort": "START_TIME_DESC",
        },
    )
    jobs = sorted(
        (json.loads(p.read_text()) for p in JOBS.glob("*.json")),
        key=lambda j: j["started"],
        reverse=True,
    )[:10]
    return {
        "links": {
            "editor": os.environ.get("WORKBENCH_EDITOR_URL", "http://localhost:8080"),
            "prefect": os.environ.get("WORKBENCH_PREFECT_URL", "http://localhost:4200"),
        },
        "files": sorted(
            str(p.relative_to(ROOT))
            for p in ROOT.rglob("*.py")
            if ".workbench" not in p.parts
            and "__pycache__" not in p.parts
            and ".venv" not in p.parts
        ),
        "deployments": deployments,
        "runs": runs,
        "environment": status(),
        "jobs": jobs,
        "busy": BUSY.locked(),
    }


@app.get("/api/file")
def read_file(path: str):
    target = file_path(path)
    if not target.is_file():
        raise HTTPException(404, "文件不存在")
    return {"content": target.read_text(), "revision": digest(target)}


class FileEdit(BaseModel):
    path: str
    content: str = Field(max_length=500000)
    revision: str | None = None


@app.post("/api/file")
async def write_file(body: FileEdit):
    if BUSY.locked():
        raise HTTPException(409, "操作正在执行，请稍后保存")
    target = file_path(body.path)
    if digest(target) != body.revision:
        raise HTTPException(409, "文件已被修改，请重新打开后再保存，避免覆盖")
    try:
        compile(body.content, str(target), "exec")
    except SyntaxError as exc:
        raise HTTPException(400, f"第 {exc.lineno} 行语法错误：{exc.msg}") from exc
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + "." + uuid.uuid4().hex + ".tmp")
    temporary.write_text(body.content)
    temporary.replace(target)
    return {"revision": digest(target)}


class Registration(BaseModel):
    entrypoint: str
    name: str = Field(default="local", pattern=r"^[\w-]{1,80}$")


async def register(body, expected_id=""):
    filename, sep, function = body.entrypoint.partition(":")
    if not sep or not function.isidentifier() or not file_path(filename).is_file():
        raise ValueError("入口格式应为 flows/example.py:function")
    process = await asyncio.create_subprocess_exec(
        "/opt/workbench/bin/python",
        "/opt/workbench/manager/register.py",
        body.entrypoint,
        body.name,
        expected_id,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        output, _ = await asyncio.wait_for(process.communicate(), 90)
    except TimeoutError:
        process.kill()
        await process.wait()
        raise ValueError("注册超时，请检查脚本导入时是否有阻塞操作")
    text = output.decode(errors="replace")
    if process.returncode:
        raise ValueError(text[-6000:])
    return json.loads(
        next(
            line.removeprefix("WORKBENCH_RESULT=")
            for line in text.splitlines()
            if line.startswith("WORKBENCH_RESULT=")
        )
    )


@app.post("/api/register")
async def register_route(body: Registration):
    async def operation(job):
        return await register(body)

    return await submit(operation)


async def deployment(identifier):
    uuid.UUID(identifier)
    item = await api("/deployments/" + identifier)
    if item["work_pool_name"] != "local-process":
        raise ValueError("只支持 local-process 工作池")
    return item


async def active_runs(identifier):
    return await api(
        "/flow_runs/filter",
        {
            "deployments": {"id": {"any_": [identifier]}},
            "flow_runs": {
                "state": {
                    "type": {
                        "any_": [
                            "RUNNING",
                            "PENDING",
                            "CANCELLING",
                            "PAUSED",
                            "SCHEDULED",
                        ]
                    }
                }
            },
            "limit": 200,
        },
    )


async def stop(identifier):
    await api("/deployments/" + identifier, {"paused": True}, "PATCH")
    deadline = time.monotonic() + 120
    while True:
        runs = await active_runs(identifier)
        if not runs:
            return
        for run in runs:
            if run["state"]["type"] != "CANCELLING":
                await api(
                    "/flow_runs/" + run["id"] + "/set_state",
                    {"state": {"type": "CANCELLING"}},
                )
        if time.monotonic() > deadline:
            raise ValueError(
                "旧运行未确认停止，已暂停调度；没有启动新实例。请检查 Worker 和运行状态。"
            )
        await asyncio.sleep(1)


class Action(BaseModel):
    action: str = Field(pattern="^(start|stop|restart)$")
    parameters: dict = Field(default_factory=dict)


@app.post("/api/deployments/{identifier}/action")
async def action(identifier: str, body: Action):
    async def operation(job):
        item = await deployment(identifier)
        if body.action in {"stop", "restart"}:
            await stop(identifier)
        if body.action == "stop":
            return {"message": "运行已停止，定时调度已暂停"}
        if body.action == "restart":
            # Re-import code only after the old execution has stopped.
            registered = await register(
                Registration(entrypoint=item["entrypoint"], name=item["name"]),
                identifier,
            )
            if registered["id"] != identifier:
                raise ValueError("Flow 名称已改变，请使用新部署启动；旧部署保持暂停")
        elif await active_runs(identifier):
            return {"message": "已有运行或排队实例，未重复启动"}
        else:
            # Existing deployments also adopt the shared interpreter and process lock.
            flow = await api("/flows/" + item["flow_id"])
            variables = item.get("job_variables") or {}
            variables.update(
                command="/opt/venv/bin/python /opt/workbench/manager/engine.py",
                env={
                    **variables.get("env", {}),
                    "WORKBENCH_DEPLOYMENT_KEY": f"{flow['name']}/{item['name']}",
                },
            )
            await api(
                "/deployments/" + identifier,
                {"job_variables": variables, "concurrency_limit": 1},
                "PATCH",
            )
        run = await api(
            "/deployments/" + identifier + "/create_flow_run",
            {
                "parameters": body.parameters,
                "idempotency_key": "workbench-" + job["id"],
            },
        )
        if body.action == "restart" and not item["paused"]:
            await api("/deployments/" + identifier, {"paused": False}, "PATCH")
        return {"run_id": run["id"], "message": "已提交运行"}

    return await submit(operation)


class Dependencies(BaseModel):
    requirements: str = Field(max_length=20000)


@app.post("/api/environment")
async def dependencies(body: Dependencies):
    async def operation(job):
        return await asyncio.to_thread(
            install, body.requirements, JOBS / (job["id"] + ".log")
        )

    return await submit(operation)


@app.get("/api/jobs/{identifier}")
def job_status(identifier: str):
    if not re_identifier(identifier):
        raise HTTPException(404)
    path = JOBS / (identifier + ".json")
    if not path.exists():
        raise HTTPException(404)
    job = json.loads(path.read_text())
    log = path.with_suffix(".log")
    job["log"] = log.read_text(errors="replace")[-12000:] if log.exists() else ""
    return job


def re_identifier(value):
    return len(value) == 32 and all(c in "0123456789abcdef" for c in value)
