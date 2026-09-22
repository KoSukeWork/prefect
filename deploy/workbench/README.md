# Prefect 单容器工作台

一个 Docker 容器、一个对外端口、一个网页登录密码。镜像由本仓库源码构建，包含 Prefect 两版 UI、Process Worker、中文 code-server、中文管理页、PostgreSQL 17 和 Nginx。不修改 Prefect 核心代码或上游 Dockerfile。

## 启动

```powershell
./deploy/workbench/setup.ps1
docker compose -f deploy/workbench/compose.yaml up -d --build --wait --wait-timeout 300
```

打开 **http://localhost:8090/**，输入 `.env` 的 `PREFECT_PASSWORD`。同一次登录即可访问：

| 路径 | 用途 |
| --- | --- |
| `/` | 中文操作页：新建模板、编辑脚本、注册、启动、停止、更新重启、安装依赖 |
| `/editor/` | 中文 VS Code：文件管理、上传下载、编辑、终端 |
| `/prefect/` | Prefect 控制台：运行日志、参数、定时计划、监控 |
| `/prefect/api/` | 对外 SDK / REST API，支持 `admin:PREFECT_PASSWORD` Basic 认证 |

所有链接均使用同一端口。旧的 4200 和 8080 端口不再对外开放。默认只绑定 `127.0.0.1`。更改端口时同时设置 `.env` 的 `WORKBENCH_PORT` 与 `WORKBENCH_PUBLIC_URL`；浏览器路径仍相对当前地址，公开 URL 用于任务日志中的链接。

首次构建需要联网。Linux 可复制 `.env.example` 为 `.env`，用 `openssl rand -hex 24` 填写两个秘密：`PREFECT_PASSWORD` 是唯一需要输入的网页密码；`POSTGRES_PASSWORD` 仅供容器内部数据库使用，无需在网页输入。旧 `.env` 的 `EDITOR_PASSWORD` 在单容器方案中不再使用。

## 日常使用

1. 操作页点击“新建脚本”，从 Flow 模板开始，编辑并填写函数名、部署名称。
2. 点击“保存并注册任务”；新业务应使用不同的 Flow 名称。同名 Flow 和部署会更新已有条目。
3. 点击“启动”，可填写 JSON 参数。日志、定时计划通过右上角 Prefect 链接查看。
4. 保存代码后，新启动的运行读取新版。修改参数签名后重新注册。常驻脚本点“更新并重启”，先取消旧实例，再注册并启动新版，保留已有定时计划和默认参数。
5. “停止”取消当前及排队运行，同时暂停调度；恢复定时调度请到 Prefect 控制台操作。
6. 复杂编辑和文件操作打开“完整编辑器”。首次进入如提示受限模式，信任本工作区后可使用终端任务。

工作台注册或启动的部署并发上限为 1，并使用跨进程文件锁覆盖脚本执行期。重复启动会跳过；停止超时则不会启动新版。不同部署可以并行，Worker 默认总并发 4，可通过 `WORKER_LIMIT` 调整。

编辑器终端也支持：

```sh
python /usr/local/bin/workbench deploy flows/hello.py:hello --name local
prefect deployment run hello/local
```

任务不会自动热更新。已经加载的代码与解释器保持原样；业务自行动态导入或读取共享文件时仍可读到新文件。主机断电或容器整体重启后的常驻业务自动恢复，需要业务自己的幂等和检查点策略。

## 依赖与数据持久化

在操作页填写完整额外依赖清单，如 `humanfriendly==10.0`，点击“安装并统一启用”。uv 先生成独立项目和锁文件，安装验证成功后才原子切换环境。冲突或安装失败不改变当前环境；Prefect 和基础依赖版本保持固定。

编辑器终端 `python`、注册工具和新业务运行均通过 `/opt/workbench/bin/python` 选择同一个环境。已有进程保留原环境。自行配置绝对解释器路径时也应使用这个入口。旧环境保留供已有进程使用，不要在运行中清理。

| 命名卷 | 保存内容 |
| --- | --- |
| `prefect-workbench_database-single` | PostgreSQL 数据、任务、计划、运行历史与日志 |
| `prefect-workbench_workspace` | 脚本、工作区文件、操作记录 |
| `prefect-workbench_business-env` | Python 依赖环境、清单、uv 锁文件 |
| `prefect-workbench_editor-data` | 中文语言包、编辑器扩展和偏好 |

重启、重建、替换容器均保留这些卷。**不要使用 `docker compose down -v`**，它会删除数据。

## 内部解耦与更新

`compose.yaml` 只创建 `workbench` 一个服务。`standalone/supervisor.py` 按数据库、Server、Worker、编辑器、管理页、网关的顺序启动独立进程；Nginx 只对外监听 8090，其余服务均只监听容器内回环地址。管理页会话统一保护编辑器、Prefect UI 和 API。所有进程以非 root 用户运行，没有 Docker socket 或特权模式。

任何主要进程退出会触发整套进程有序退出，由 Docker 重启策略恢复。健康检查覆盖全部主要进程与 HTTP 端点。一个容器共用资源，更新或重启会影响整套工作台，操作前应结束业务运行。重启后编辑器若提示无法重新连接，刷新页面即可。

组件代码仍分别放在 `manager/`、`standalone/`、编辑器启动脚本和 Docker 构建阶段中。Prefect Python 依赖来自上游 `uv.lock`，UI 使用 npm 锁文件，code-server 版本单独固定；日常业务代码和依赖更新不需要修改 Prefect 源码。

```powershell
docker compose -f deploy/workbench/compose.yaml build
docker compose -f deploy/workbench/compose.yaml up -d --wait --wait-timeout 300
docker compose -f deploy/workbench/compose.yaml ps
```

升级 Prefect 或 Python 版本前备份数据并结束所有业务运行，保存额外依赖清单。移除 business-env 卷中的 `current` 链接后，启动新镜像的基础环境，再通过页面重建业务依赖环境。PostgreSQL 当前固定主版本 17，跨主版本升级须使用数据库备份迁移，不能直接复用旧版数据目录。

## 从旧多容器方案迁移

本机已经完成迁移，无需再次执行。迁移前数据库备份与快照保存在 `backups/single-migration-*/`，原 `prefect-workbench_database` 卷保留，可用于回退。

其他仍运行旧版的安装可先构建新镜像，再执行：

```powershell
docker compose -f deploy/workbench/compose.yaml build
uv run --no-project deploy/workbench/migrate_to_single.py
```

脚本拒绝迁移有活动运行的实例，停止旧应用写入后导出数据库，导入独立的新卷，沿用代码、依赖和编辑器卷。旧 PostgreSQL 16 数据通过逻辑备份导入 PostgreSQL 17，不直接改写旧数据目录。首次导入中断会停止启动，不会默默覆盖残留数据库。

`compose.multi.yaml` 和 `MULTI-CONTAINER.md` 仅保留作历史方案与回退参考。回退前先停止新容器，确认如何处理迁移后新增的数据；旧数据库不包含这些新记录，不要同时运行两套 Worker。

## 备份与验收

```powershell
New-Item -ItemType Directory -Force deploy/workbench/backups
docker compose -f deploy/workbench/compose.yaml exec -T workbench pg_dump -h /tmp/workbench-postgres -U prefect -Fc -f /tmp/prefect.dump prefect
docker compose -f deploy/workbench/compose.yaml cp workbench:/tmp/prefect.dump deploy/workbench/backups/prefect.dump
docker compose -f deploy/workbench/compose.yaml exec -T workbench tar -czf /tmp/workspace.tar.gz -C /workspace .
docker compose -f deploy/workbench/compose.yaml cp workbench:/tmp/workspace.tar.gz deploy/workbench/backups/workspace.tar.gz
```

另行保存 `.env`、business-env 和 editor-data 卷。恢复时停止业务并使用匹配的数据库主版本、Python 和 Prefect 镜像，目录所有者为 UID 1000。工作区中的 `.workbench/legacy-database.dump` 只供首次迁移导入；新数据库卷首次启动会读取它，创建全新安装时不要误带旧迁移文件。

以下测试会运行临时任务并重建容器，应在无业务运行时执行：

```powershell
uv run --no-project deploy/workbench/acceptance.py
uv run --no-project deploy/workbench/manager_acceptance.py
uv run --no-project deploy/workbench/single_acceptance.py
```

验收覆盖注册、执行、代码更新、依赖切换、冲突回退、取消重启、真实调度、统一认证与子路径、单容器单端口、容器恢复和持久化。详细记录见 `ACCEPTANCE.md`。
