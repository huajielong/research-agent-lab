# 反思式研究智能体（FastAPI + Postgres，单容器）

一个基于 FastAPI 的 Web 应用：它会规划研究流程，调用具备工具调用能力的智能体（Tavily、arXiv、Wikipedia），并把任务状态与结果存放在 Postgres 中。
本仓库包含一套 Docker 配置，可在**同一个容器中同时运行 Postgres 与 API**（用于本地/开发环境）。

## 功能特性

* `/` 提供一个简单的界面（Jinja2 模板），用于发起研究任务。
* `/generate_report` 以线程方式启动多步骤智能体流程（规划器 → 研究/写作/编辑）。
* `/task_progress/{task_id}` 实时查看每个步骤/子步骤的状态。
* `/task_status/{task_id}` 获取最终状态与报告。

---

## 项目结构（关键路径）

```
.
├─ main.py                      # FastAPI 应用（即上文展示的文件）
├─ src/
│  ├─ planning_agent.py         # planner_agent()、executor_agent_step()
│  ├─ agents.py                 # research_agent、writer_agent、editor_agent（示例）
│  └─ research_tools.py         # tavily_search_tool、arxiv_search_tool、wikipedia_search_tool
├─ templates/
│  └─ index.html                # 由 "/" 渲染的界面页面
├─ static/                      # 可选的静态资源（css/js）
├─ docker/
│  └─ entrypoint.sh             # 启动 Postgres、初始化数据库，然后启动 Uvicorn
├─ requirements.txt
├─ Dockerfile
└─ README.md
```

> 请确保 `templates/index.html` 以及（可选的）`static/` 目录存在，并且会被复制进镜像。

---

## 前置条件

* **Docker**（Windows/macOS 上的 Docker Desktop，或 Linux 上的 Docker 引擎）。


* API 密钥保存在 `.env` 文件中。先复制模板再填入真实值：

  ```bash
  cp .env.example .env
  ```

  `.env` 已被 `.gitignore` 忽略；仓库里只保留不含密钥的 `.env.example`。
  `.env` 里必须设置 `APP_API_KEY`（默认 fail closed）；本地演示可改用
  `ALLOW_ANONYMOUS=1`。`OPENAI_MODEL` 用于指定模型名（不填时默认
  `gpt-4.1-mini`，代码会自动补 `openai:` 前缀）。

* Python 依赖由 Docker 根据 `requirements.txt` 自动安装：

  * `fastapi`、`uvicorn`、`sqlalchemy`、`python-dotenv`、`jinja2`、`bleach`、
    `requests`、`wikipedia` 等。
  * 以及你的 `aisuite` 客户端所需的全部依赖库。

---

## 环境变量

应用读取 `DATABASE_URL` 以及下面的运行配置。

* 容器入口脚本会用 `POSTGRES_*` 生成 DSN：

  ```
  postgresql://<POSTGRES_USER>:<POSTGRES_PASSWORD>@127.0.0.1:5432/<POSTGRES_DB>
  ```
* **`POSTGRES_PASSWORD` 必须显式设置**；入口脚本不再使用默认口令，留空会直接
  启动失败。先 `cp .env.example .env`，再用
  `openssl rand -hex 24` 生成一个强密码填进去（不要用示例/弱口令）。
* 如需使用 Tavily：提供 `TAVILY_API_KEY`（通过 `.env` 或 `-e` 传入）。

数据库相关：

* `POSTGRES_USER`（默认 `app`）
* `POSTGRES_PASSWORD`（**必填**，使用强随机值；不要用示例/弱口令）
* `POSTGRES_DB`（默认 `appdb`）
* `DATABASE_URL`（可选；不设置时入口脚本自动生成；本地非 Docker 可设为
  `sqlite:///...`）

运行安全配置：

* `APP_API_KEY`（**默认必填**）：`/generate_report`、`/task_progress`、
  `/task_status` 需要 `X-API-Key` 请求头。未设置且未显式允许匿名时，应用会拒绝启动。
* `ALLOW_ANONYMOUS=1`：仅本地开发/演示时使用；公网部署不要设置。
* `MAX_PROMPT_CHARS`（默认 `4000`）
* `RATE_LIMIT_PER_MINUTE`（默认 `5`，每个客户端 IP；IPv6 按 `/64` 聚合）
* `AUTH_FAILURE_LIMIT`（默认 `5`，每个客户端 IP 每分钟允许的认证失败次数）
* `POLL_RATE_LIMIT_PER_MINUTE`（默认 `120`，轮询接口每 IP 每分钟配额）
* `MAX_RATE_BUCKETS`（默认 `10000`，超出后按最近使用时间淘汰）
* `TRUST_PROXY_HEADERS=1`：仅当应用在可信反向代理之后、且代理会设置/覆盖
  `X-Forwarded-For` 时启用；直接暴露公网时保持关闭。
* `MAX_ACTIVE_TASKS`（默认 `10`，运行中 + 排队任务上限）
* `MAX_CONCURRENT_TASKS`（默认 `2`，同时调用 LLM 的任务数）
* `CORS_ALLOW_ORIGINS`（逗号分隔；留空 = 只允许同源）

模型相关：

* `OPENAI_MODEL`（默认 `gpt-4.1-mini`，代码会自动补 `openai:` 前缀）
* `OPENAI_BASE_URL`（OpenAI 兼容端点，例如 DeepSeek）
* `OPENAI_MAX_TOKENS`（writer 单次输出上限，默认 `32000`；被截断会自动续写）
* `RESET_DB_ON_STARTUP=1`（启动时清空 `tasks` 表；默认保留历史任务）

---

## 安全说明

* `.env` 同时被 `.gitignore` 和 `.dockerignore` 忽略，`docker build` 不会把本地密钥打进镜像。
* 默认必须设置 `APP_API_KEY`；只有本地开发/演示可以显式设置 `ALLOW_ANONYMOUS=1`。
* 默认不发布 Postgres 的 5432 端口；数据库只在容器内可达。需要宿主机直连时再显式
  加 `-p 5432:5432`，并使用强 `POSTGRES_PASSWORD`。
* 接口有每 IP 每分钟限流、提示词长度上限、活动任务上限和并发 LLM 上限；这些按单进程计数，
  多 worker 部署需在反向代理层再加限流。
* 前端用 DOMPurify 净化 `marked` 输出；后端对用户 prompt 和 agent 输出做 HTML 转义。
  CDN 资源同时使用固定版本和 SRI，CDN 内容被篡改时浏览器会拒绝加载。

---

## 构建与运行（本地/开发）

### 1) 构建

```bash
docker build -t fastapi-postgres-service .
```

### 2) 运行（前台）

```bash
docker run --rm -it  -p 8000:8000  --name fpsvc  --env-file .env  fastapi-postgres-service
```

你应该能看到类似下面的日志：

```
🚀 Starting Postgres cluster 17/main...
✅ Postgres is ready
CREATE ROLE
CREATE DATABASE
🔗 DATABASE_URL set (host=127.0.0.1, port=5432, db=appdb); password redacted
INFO:     Uvicorn running on http://0.0.0.0:8000
```

### 3) 打开应用

* 界面：[http://localhost:8000/](http://localhost:8000/)
* 接口文档：[http://localhost:8000/docs](http://localhost:8000/docs)

---

## API 快速上手

如果设置了 `APP_API_KEY`，所有任务接口都需要加 `-H "X-API-Key: <你的 key>"`。

### 发起一次任务

```bash
curl -X POST http://localhost:8000/generate_report \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $APP_API_KEY" \
  -d '{"prompt": "Large Language Models for scientific discovery"}'
# -> {"task_id": "UUID..."}
```

### 轮询进度

```bash
curl -H "X-API-Key: $APP_API_KEY" http://localhost:8000/task_progress/<TASK_ID>
```

### 获取最终状态与报告

```bash
curl -H "X-API-Key: $APP_API_KEY" http://localhost:8000/task_status/<TASK_ID>
```

---

## 常见问题排查

**打开 [http://localhost:8000](http://localhost:8000) 后一片空白 / 报错**

* 确认容器内存在 `templates/index.html`：

  ```bash
  docker exec -it fpsvc bash -lc "ls -l /app/templates && ls -l /app/static || true"
  ```
* 在加载页面的同时观察日志：

  ```bash
  docker logs -f fpsvc
  ```

**容器启动时要求输入 Postgres 密码**

* 入口脚本以 postgres 用户执行管理类操作（`runuser -u postgres -- psql ...`，
  或 `su -s /bin/bash postgres -c 'exec "$@"' _ psql ...`），走
  **UNIX socket + peer 认证**，不需要 postgres 超级用户密码。
* 应用角色的密码来自 `POSTGRES_PASSWORD`；未设置时容器会直接报错退出。

**报 `DATABASE_URL not set` 错误**

* 入口脚本会用 `POSTGRES_*` 生成 DSN；如果你覆盖了它，请确保格式合法：

  ```
  postgresql://<user>:<password>@<host>:<port>/<database>
  ```

**重启后数据表消失**

* 现在默认**不会**清空：`main.py` 只在 `RESET_DB_ON_STARTUP=1` 时执行
  `Base.metadata.drop_all(...)`。需要每次启动重建 `tasks` 表时显式设置该变量。
* 旧版本的行为是每次启动都清空；升级后历史任务会保留。

**最终报告被截断**

* 推理模型（如 DeepSeek）的隐藏推理 token 也计入 `max_tokens`，正文写到上限就会被切断。
  `writer_agent` 现在默认 `max_tokens=32000`，并在 `finish_reason == "length"` 时
  自动从截断处续写；若正文为空（额度全被推理占用）会自动加倍重试（上限 64000）。
* 仍觉得不够可调大 `OPENAI_MAX_TOKENS`。

**Tavily / arXiv / Wikipedia 报错**

* 提供 `TAVILY_API_KEY` 并确保网络可访问；在根目录下的 `.env` 文件中按如下方式配置：
```
# OpenAI API Key
OPENAI_API_KEY=your-open-api-key
TAVILY_API_KEY=your-tavily-api-key
```

* Wikipedia 有时会触发限流；可以稍后重试，或优雅地处理异常。

---

## 开发技巧

* **热重载**（可选）：在开发时，如果你挂载了代码目录，可以用 `--reload` 运行 Uvicorn：

  ```bash
  docker run --rm -it -p 8000:8000 \
    -v "$PWD":/app \
    --name fpsvc fastapi-postgres-service \
    bash -lc "pg_ctlcluster \$(psql -V | awk '{print \$3}' | cut -d. -f1) main start && uvicorn main:app --host 0.0.0.0 --port 8000 --reload"
  ```

* **从宿主机连接数据库：**

  默认的 `docker run` **不发布 5432**，数据库只在容器内可达。确实需要宿主机直连时，
  给 `docker run` 额外加 `-p 5432:5432`，并使用强密码：

  ```bash
  psql "postgresql://app:${POSTGRES_PASSWORD}@localhost:5432/appdb"
  # POSTGRES_PASSWORD 是你在 .env 里设置的强密码
  ```

---
