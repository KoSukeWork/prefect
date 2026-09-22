# 本地验收记录

## 2026-09-22：单容器、单端口交付

默认 Compose 已改为唯一的 `workbench` 服务，镜像 `prefect-workbench:local`，唯一端口映射为 `127.0.0.1:8090->8090/tcp`。

- 中文操作页 `/`、中文编辑器 `/editor/`、Prefect `/prefect/` 共用一个入口和登录会话。浏览器已确认编辑器能打开迁移的 `web_demo.py`，Prefect 仪表盘能读取历史运行，均无需再次登录。
- 旧 PostgreSQL 16 数据通过逻辑备份迁移到容器内 PostgreSQL 17。原部署 ID、任务和运行历史保留；旧数据库卷仍在。迁移备份在 `backups/single-migration-20260922T032346Z/`。
- 所有主要进程非 root 运行，内部服务仅绑定回环地址，无 Docker socket 和特权模式。
- `acceptance.py` 已在单容器上重新通过：真实执行、保存新代码生效、并发、取消重启、真实 interval 调度，以及整个容器重建后的数据库和代码持久化。
- `manager_acceptance.py` 已重新通过：注册编辑、修订冲突、启停与更新、共享依赖、安装冲突回退、容器重建后环境保留和实际运行。
- `single_acceptance.py` 检查统一认证、UI 静态资源子路径、未经认证及跨来源请求拒绝、迁移数据保留，并模拟管理进程异常退出后自动恢复。
- 最终镜像的全新离线临时容器成功初始化数据库、示例文件和中文编辑器；临时容器已删除，正式工作台保持健康运行。

单容器重启会重启全部服务。已打开的编辑器如果提示无法重新连接，刷新页面即可；文件与设置仍在持久化卷中。详细运行结果保存在本目录忽略的 `*-acceptance-results.json` / `acceptance-results.json` 中。

以下保留此前多容器阶段的验收记录，当前入口以本节和 README 为准。

2026-09-22 中文操作页验收通过，入口 http://localhost:8090，复用 Prefect 密码。独立 manager 服务已部署，5 个常驻服务健康。

- 真实 HTTP 验收覆盖登录认证、跨站请求校验、路径越界拒绝、Python 语法校验与并发编辑版本冲突。
- 新建脚本、注册部署、实际运行、修改后再次运行成功。
- 常驻任务重复启动被拦截；更新重启取消旧实例后读取新代码；测试脚本另持非阻塞进程锁，未发生业务执行重叠。
- 重启保留原有定时计划和默认参数；停止会取消运行并暂停调度。
- 安装 `humanfriendly==10.0` 后 editor 与 worker 的 Python 路径、包版本完全一致，新任务成功导入；原长任务保持运行。
- 安装冲突版本 `pydantic==1.10.0` 失败，当前环境未被切换。
- 强制重建 manager、worker、editor 后，代码与依赖环境保留，实际任务再次运行成功。
- 浏览器完成登录、从模板新建 `flows/web_demo.py`、编辑中文日志、保存注册和启动。运行 `a705a393-a1d6-4265-a664-7e3aedbd5e56` 成功，日志为“你好，世界！这是网页创建的任务。”。保留 `web-demo/local` 供验收。

新增测试命令：`uv run --no-project deploy/workbench/manager_acceptance.py`；机器可读报告：本目录忽略的 `manager-acceptance-results.json`。临时测试部署与源码已清理，运行审计记录保留。依赖测试所用 `humanfriendly==10.0` 保留，可在页面修改完整依赖清单。

2026-09-22 补充：网页编辑器默认简体中文已验收。已重建 editor，浏览器确认登录页、资源管理器、搜索、运行和调试、菜单及终端显示中文。使用 `--network none` 的全新临时容器验证镜像内置语言包可离线安装，并生成有效的中文语言索引。Server、Worker、数据库未重启。

2026-09-21，Windows Docker Desktop，Linux amd64 容器。
源码基线：`33de4078a0`（Prefect 3.8.6）；分支 `codex/local-workbench`。

构建和自动验收均已通过。最终机器可读报告为本目录忽略的 `acceptance-results.json`，完成时间 `2026-09-21T13:11:39Z`。

- 从源码构建 Prefect 两版 UI 与 Python 包，code-server 4.138.0。
- 4 个常驻服务健康；工作区初始化服务正常退出。
- Prefect API 和编辑器认证正常，未认证请求被拒绝。
- Server、Worker、editor 均非 root，无 Docker socket、无特权模式。
- 编辑器、网页登录 Shell、Worker 使用一致的 Prefect Python 环境。
- 编辑器写入脚本，注册部署，由 Worker 成功执行，日志进入数据库。
- 保存新代码后，下一次运行直接读取新版，无需重建或重新注册。
- 长任务运行期间短任务正常完成；同一部署并发限制 1 生效。
- 取消长任务并重新启动成功。
- Server 创建的真实 interval 计划成功执行。
- 整套容器强制重建后，代码、部署、历史运行仍存在，新任务继续成功。
- Ruff lint、格式检查、codespell（相关文件）、Compose 校验通过。

浏览器独立验收也已完成：登录两套页面，通过 **Tasks: Run Task → Prefect: register flow** 注册 `hello/local`，直接编辑并保存 `hello.py`，在 Prefect 网页点击 **Run → Quick run**。运行 `b3794742-a834-4561-9f5a-37574df687ed` 状态为 Completed，网页日志显示：

```text
Hello, Prefect! Saved and verified in the browser.
Finished in state Completed()
```

这个 `hello/local` 部署和示例运行保留供验收，自动测试部署已清理，测试运行历史保留。服务保持启动，仅绑定本机。未部署到远程服务器，也没有调用真实飞书接口。主机断电后常驻 Flow 的自动恢复不在本次已验证能力内，处理边界见 README。
