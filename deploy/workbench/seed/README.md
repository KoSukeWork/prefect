# 工作区

左侧资源管理器支持新增、编辑、移动、删除文件，拖入文件上传，右键下载。

1. 编辑 `flows/hello.py` 并保存。
2. 菜单 Terminal → Run Task → **Prefect: register flow**，确认文件入口和部署名称。
3. 打开 http://localhost:8090/prefect/，在 Deployments 中运行 `hello/local`，修改参数、设置计划、查看日志。

也可在本网页终端执行：

```sh
python /usr/local/bin/workbench deploy flows/hello.py:hello --name local
python /usr/local/bin/workbench deploy flows/listener.py:listener --name local
prefect deployment run hello/local
```

保存现有代码后，新运行读取新版；参数签名改变后需重新注册。正在运行的代码不会热更新。
打开 http://localhost:8090 使用中文操作页，新建脚本、注册、启动、停止，或保存代码后点击“更新并重启”。一次登录即可访问同一端口的编辑器 `/editor/` 和 Prefect `/prefect/`。每个部署默认并发上限为 1，整个 Worker 默认 4。

代码保存在持久化卷中。额外 Python 依赖在中文操作页“安装并统一启用”，成功后编辑器 python 与新运行共用新版环境，已有运行保留旧环境。不要仅在编辑器临时安装依赖。
