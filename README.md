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
  `OPENAI_MODEL` 用于指定模型名（不填时默认 `gpt-4.1-mini`，代码会自动补 `openai:` 前缀）。

* Python 依赖由 Docker 根据 `requirements.txt` 自动安装：

  * `fastapi`、`uvicorn`、`sqlalchemy`、`python-dotenv`、`jinja2`、`requests`、`wikipedia` 等。
  * 以及你的 `aisuite` 客户端所需的全部依赖库。

---

## 环境变量

应用启动时**只读取 `DATABASE_URL`**。

* 容器入口脚本会为本地开发设置一个合理的默认值：

  ```
  postgresql://app:local@127.0.0.1:5432/appdb
  ```
* 如需使用 Tavily：

  * 提供 `TAVILY_API_KEY`（通过 `.env` 或 `-e` 传入）。

可选（如果你想覆盖入口脚本设置的默认值）：

* `POSTGRES_USER`（默认 `app`）
* `POSTGRES_PASSWORD`（默认 `local`）
* `POSTGRES_DB`（默认 `appdb`）

---

## 构建与运行（本地/开发）

### 1) 构建

```bash
docker build -t fastapi-postgres-service .
```

### 2) 运行（前台）

```bash
docker run --rm -it  -p 8000:8000  -p 5432:5432  --name fpsvc  --env-file .env  fastapi-postgres-service
```

你应该能看到类似下面的日志：

```
🚀 Starting Postgres cluster 17/main...
✅ Postgres is ready
CREATE ROLE
CREATE DATABASE
🔗 DATABASE_URL=postgresql://app:local@127.0.0.1:5432/appdb
INFO:     Uvicorn running on http://0.0.0.0:8000
```

### 3) 打开应用

* 界面：[http://localhost:8000/](http://localhost:8000/)
* 接口文档：[http://localhost:8000/docs](http://localhost:8000/docs)

---

## API 快速上手

### 发起一次任务

```bash
curl -X POST http://localhost:8000/generate_report \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Large Language Models for scientific discovery", "model":"openai:gpt-4o"}'
# -> {"task_id": "UUID..."}
```

### 轮询进度

```bash
curl http://localhost:8000/task_progress/<TASK_ID>
```

### 获取最终状态与报告

```bash
curl http://localhost:8000/task_status/<TASK_ID>
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

* 入口脚本对管理类操作使用的是 **UNIX socket + peer 认证**（无需密码）。
  请确认脚本中没有调用 `psql -h 127.0.0.1 -U postgres`，而应使用：

  ```bash
  su -s /bin/bash postgres -c "psql -c '...'"
  ```

**报 `DATABASE_URL not set` 错误**

* 入口脚本会导出一个默认的 DSN。如果你覆盖了它，请确保格式合法：

  ```
  postgresql://<user>:<password>@<host>:<port>/<database>
  ```

**重启后数据表消失**

* 你的 `main.py` 在启动时调用了 `Base.metadata.drop_all(...)`。
  请将其注释掉，或用环境变量开关加以保护：

  ```python
  if os.getenv("RESET_DB_ON_STARTUP") == "1":
      Base.metadata.drop_all(bind=engine)
  ```

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
  docker run --rm -it -p 8000:8000 -p 5432:5432 \
    -v "$PWD":/app \
    --name fpsvc fastapi-postgres-service \
    bash -lc "pg_ctlcluster \$(psql -V | awk '{print \$3}' | cut -d. -f1) main start && uvicorn main:app --host 0.0.0.0 --port 8000 --reload"
  ```

* **从宿主机连接数据库：**

  ```bash
  psql "postgresql://app:local@localhost:5432/appdb"
  ```

---
