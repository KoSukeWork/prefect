# 多容器历史方案（仅供回退参考）

# Prefect 本地工作台

从当前仓库源码构建 Prefect（包含两版 UI），附加浏览器 VS Code（code-server，MIT）及其文件管理器。一次 Compose 启动整套服务；每个脚本作为 Process Worker 的子进程运行，无需单独创建容器。不修改 Prefect 核心代码或上游 Dockerfile。

## 启动和验收

在仓库根目录执行（需要 Docker Desktop / Docker Engine 和 Compose）：

```powershell
./deploy/workbench/setup.ps1
docker compose -f deploy/workbench/compose.multi.yaml up -d --build --wait --wait-timeout 240
```

首次构建需联网下载基础镜像、Python 和 npm 依赖。Linux 用户可复制 `.env.example` 为 `.env`，分别用 `openssl rand -hex 24` 生成三个密码，再执行同一条 Compose 命令。

| 入口 | 用途 | 登录 |
| --- | --- | --- |
| http://localhost:8090 | 中文操作页：新建脚本、编辑、注册、启停、更新重启、依赖安装 | 与 Prefect 共用 `PREFECT_PASSWORD`，只输入密码 |
| http://localhost:4200 | Prefect：参数、手动运行、定时、日志、取消 | `.env` 中的 `admin:PREFECT_PASSWORD`；如页面分开询问则用户名 admin、密码为该值 |
| http://localhost:8080 | 网页编辑器、文件管理、终端 | `.env` 中的 `EDITOR_PASSWORD` |

网页编辑器已内置简体中文语言包，默认以 `zh-cn` 启动，刷新页面即可使用。语言包随镜像提供，首次启动会离线安装到编辑器数据卷，重建容器后仍保留。Prefect 控制台本身仍为英文。

密码保存在本目录 `.env`，已忽略，不写进镜像。默认端口仅绑定本机。修改 `PREFECT_PORT` 时同步修改 `PREFECT_PUBLIC_API_URL` 和 `PREFECT_PUBLIC_UI_URL`。远程部署应通过 HTTPS 反向代理接入，修改公开 API 地址和端口绑定；这次交付仅进行本地验收。

1. 打开 8090 中文操作页，使用 Prefect 密码登录。选择脚本后点“打开”，或点“新建脚本”从 Flow 模板创建。
2. 编辑代码，填写 Flow 函数名和部署名称，点“保存并注册任务”。同名 Flow 与部署会更新已有条目；新任务应使用不同 Flow 名称。
3. 在任务列表点“启动”，需要时填写 JSON 参数；详细日志和定时计划通过右上角 Prefect 控制台查看。
4. 保存修改后的脚本，下次运行读取新版。常驻任务点击“更新并重启”，等待旧运行停止后才启动新版。
5. 复杂编辑或文件上传、下载、移动、删除，打开 8080 完整编辑器的资源管理器。编辑器默认中文。

自动验收（执行期间会重建本 Compose 项目的容器，因此应在没有实际业务运行时执行）：

```powershell
uv run --no-project deploy/workbench/acceptance.py
uv run --no-project deploy/workbench/manager_acceptance.py
```

测试使用唯一命名的测试流程，不连接飞书等业务服务；覆盖认证、非 root 执行、两个 UI、编辑器与 Worker 版本一致、代码保存生效、实际日志、并发、取消和重新运行、真实定时调度、容器重建后数据库和代码持久化。成功报告写入本目录 `acceptance-results.json`。失败时保留现场以便诊断，重复执行不会覆盖已有业务文件。

## 服务边界与数据

| 服务 | 职责 | 持久化数据 |
| --- | --- | --- |
| postgres | 工作流状态、部署、日志 | database 卷 |
| server | API、UI、调度与后台服务，单实例 | PostgreSQL |
| worker | 默认同时管理 4 个运行，使用同一 Python 环境 | workspace 卷 |
| editor | 独立 code-server 进程，与 worker 同一个 runtime 镜像层 | workspace、editor-data 卷 |
| manager | 独立中文操作页、Prefect API 客户端、共享依赖环境构建 | workspace 操作记录、business-env 卷 |
| init-workspace | 一次性填充缺失的示例文件，不覆盖现有文件 | workspace 卷 |

Server 不挂业务代码；Worker 和编辑器在同一路径 `/workspace` 共享代码。服务没有 Docker socket、特权模式或宿主目录挂载。数据库不发布端口。单机单 Server 使用内置消息机制，无需额外 Redis；扩成多个 Server 时应按上游分布式部署指引增加消息服务。

新增任务只需新增 Python Flow 文件并注册。网页终端支持：

```sh
python /usr/local/bin/workbench deploy flows/hello.py:hello --name local
python /usr/local/bin/workbench deploy flows/listener.py:listener --name local
prefect deployment run hello/local
```

定时计划可在 Prefect 网页添加并选择时区；命令行 `--cron` 使用 Prefect 默认时区 UTC，国内任务建议在网页中明确选择 `Asia/Shanghai`。辅助注册工具默认每个部署并发限制 1，整个 Worker 默认 4，可在 `.env` 调整 `WORKER_LIMIT` 后重建 Worker。

## 代码与常驻任务更新

保存文件后，后续运行从共享目录加载新代码，不需要重新构建镜像。修改参数后重新注册部署。**正在运行的进程不会自动热更新**；业务自行动态导入或读取文件时仍会读取共享目录。常驻流程使用中文页面“更新并重启”：暂停调度、取消当前和排队运行、重新注册、启动新版，并保留原有计划与参数。Flow 改名时会中止重启，需要另行注册。停止超时则保持暂停，不启动新版。

通过工作台启动/注册的部署设置并发上限 1，并用跨进程文件锁覆盖脚本执行期，避免取消状态先于进程退出导致重叠。“启动”遇到已有运行或排队实例会跳过；“停止”同时暂停调度。恢复定时调度在 Prefect 控制台操作。不同部署可以并行；把同一业务注册为不同名称仍属于不同部署。

Python 异常重试由 Flow 的 `retries` 控制，示例配置了 3 次重试。Docker 的 `unless-stopped` 会恢复服务容器，但不能恢复被机器断电打断的 Python 调用栈，也不保证自动重新运行被中断的常驻 Flow。恢复后应核实旧运行及实际进程已停止，再取消遗留运行并重新启动。需要全天候无人值守恢复时，应针对业务增加幂等、检查点和明确的恢复策略。示例不发送真实通知，不迁移现有飞书业务。

编辑器和所有业务进程属于同一可信使用者，彼此共享工作区和 API 凭据，不是多租户沙箱。Prefect basic auth 和编辑器登录使用独立密码。

## 更新维护

部署扩展全部集中于 `deploy/workbench/`。更新 Prefect 时拉取/合并上游源码后重建；Python 依赖使用上游 `uv.lock` 的冻结安装，两套 UI 使用各自的 npm 锁文件。code-server 版本独立固定在 Dockerfile 构建参数中，可单独更新。

```powershell
docker compose -f deploy/workbench/compose.multi.yaml build
docker compose -f deploy/workbench/compose.multi.yaml up -d --wait --wait-timeout 240
docker compose -f deploy/workbench/compose.multi.yaml ps
```

先通过网页取消或等待运行完成，再更新 Worker。升级前备份数据库及代码；Prefect 启动时会自动迁移数据库，旧镜像回滚不能替代数据库备份恢复。`.env` 中 `WORKBENCH_TAG` 可为每次构建设置独立标签，保留之前的本地镜像。

业务依赖在 8090 页面填写完整额外依赖清单（例如 `humanfriendly==10.0`），点击“安装并统一启用”。manager 使用 uv 生成独立项目与锁文件，安装并验证成功后原子切换共享环境。失败时原环境保持有效。Prefect 和镜像基础依赖被固定，冲突会明确失败。不要为业务依赖修改上游 `pyproject.toml`。

编辑器终端 `python`、工作台注册与新运行统一经过 `/opt/workbench/bin/python` 选择环境；已有 Python 进程继续使用原环境。用户自行配置的绝对解释器路径也应选择此入口。旧版持久化 VS Code 注册任务如仍指向 `/opt/venv/bin/python`，需改为上述路径。依赖卷只允许 manager 写入，Worker 和 editor 只读；各代环境、`pyproject.toml`、`uv.lock` 与构建日志保留以供诊断。不要在旧运行存活时清理旧环境。

升级 Prefect 或 Python 基础镜像前先结束业务运行，备份 business-env 卷，并移除卷中的 `current` 链接，让服务回到新镜像基础环境。启动新镜像后在页面重新安装已保存的业务依赖清单，再恢复任务；不能让旧业务环境覆盖新版本 Prefect。当前默认 Python 为 3.12。

停止与重新启动（保留卷）：

```powershell
docker compose -f deploy/workbench/compose.multi.yaml down
docker compose -f deploy/workbench/compose.multi.yaml up -d --wait --wait-timeout 240
```

不要使用 `down -v`，它会删除数据库与业务代码。常规 `down`、镜像重建、容器更新均保留命名卷。

备份示例，在没有正在修改文件/运行业务时执行；备份目录中会包含业务代码和数据库：

```powershell
New-Item -ItemType Directory -Force deploy/workbench/backups
docker compose -f deploy/workbench/compose.multi.yaml exec -T postgres pg_dump -U prefect -Fc -f /tmp/prefect.dump prefect
docker compose -f deploy/workbench/compose.multi.yaml cp postgres:/tmp/prefect.dump deploy/workbench/backups/prefect.dump
docker compose -f deploy/workbench/compose.multi.yaml exec -T editor tar -czf /tmp/workspace.tar.gz -C /workspace .
docker compose -f deploy/workbench/compose.multi.yaml cp editor:/tmp/workspace.tar.gz deploy/workbench/backups/workspace.tar.gz
```

另行妥善保存 `.env` 和 business-env 卷（包含业务环境与锁文件）；editor-data 卷保存编辑器扩展和偏好。恢复应先停 manager、Worker、editor 和 Server，将数据库备份用 `pg_restore` 导入空数据库，并将代码归档还原到 workspace 卷，确认所有权为 UID 1000 后再启动。不要在正在运行的业务上直接覆盖恢复。
