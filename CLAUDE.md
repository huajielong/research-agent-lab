# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 这是什么

DeepLearning.AI《Agentic AI 实战》M1 的演示 lab「反思式研究智能体」：FastAPI + Postgres + Jinja2 前端。
用户提交研究主题后，规划器拆出最多 7 步，执行器按步骤标题把任务分派给
research / writer / editor 三个 agent，研究 agent 通过 Tavily、arXiv、Wikipedia 三个工具检索；
进度实时显示在页面上，最终报告可在页面查看并下载为 .md / .html。

- 上游代码：<https://github.com/https-deeplearning-ai/agentic-ai-public>
- 课程里这是**非评分演示**；模块 2/3/5 的评分 lab 会从零重建研究智能体后端。
- 本目录是课程库 `_labs` 下的独立项目：根目录 `E:\wuenda\DeepLearning.AI\CLAUDE.md`
  讲的抓取/维护流水线（`_scripts/`、`_index/`、`_state.db`、档位）**不适用于这里**，
  也不要在这个 lab 里改动上层那些文件。

## 运行

### Docker（推荐：单容器同时跑 Postgres 与 API）

```bash
docker build -t fastapi-postgres-service .
docker run --rm -it -p 8000:8000 -p 5432:5432 --name fpsvc \
  --env-file .env fastapi-postgres-service
```

入口脚本 `docker/entrypoint.sh` 会启动 Debian 自带的 Postgres、创建 `app/appdb` 角色与库，
再导出默认 `DATABASE_URL` 并启动 `uvicorn main:app`。打开：

- 界面 <http://localhost:8000/>
- 接口文档 <http://localhost:8000/docs>

### 本地直接跑

需要可用的 Postgres（或 SQLite 的 DSN）和 Python 3.10+（Dockerfile 用 3.11）：

```bash
cp .env.example .env          # 填入真实密钥；.env 不进 Git
pip install -r requirements.txt
export DATABASE_URL="postgresql://app:local@127.0.0.1:5432/appdb"
# 没有 Postgres 时可直接用 SQLite 调试（数据库放在课程目录外）：
# export DATABASE_URL="sqlite:///E:/wuenda/DeepLearning.AI/_logs/research_agent_debug.db"
python -X utf8 -m uvicorn main:app --host 127.0.0.1 --port 8000 --reload
```

**必须在项目根目录启动**：`main.py` 用相对路径挂载 `static/`、读取 `templates/`。
Windows 下必须加 `-X utf8`：agent 会 `print` emoji，默认 GBK stdout 会抛
`'gbk' codec can't encode character`，导致该步骤直接失败。

### 验证（没有 build / lint / 单元测试）

启动后做 API 冒烟即可：

```bash
curl http://localhost:8000/api                       # {"status":"ok"}
curl -X POST http://localhost:8000/generate_report \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Large Language Models for scientific discovery"}'
curl http://localhost:8000/task_progress/<task_id>
curl http://localhost:8000/task_status/<task_id>
```

真正的端到端验证需要 `OPENAI_API_KEY` 与 `TAVILY_API_KEY`；没有 Tavily key 时工具返回
`[{"error": ...}]`，工作流不会崩，但报告质量无从谈起。

## 架构

```
main.py                 FastAPI 应用 + SQLAlchemy Task 表 + 内存 task_progress
src/planning_agent.py   planner_agent() 与 executor_agent_step()
src/agents.py           research_agent / writer_agent / editor_agent
src/research_tools.py   tavily_search_tool / arxiv_search_tool / wikipedia_search_tool
templates/index.html    单页前端（无构建步骤，marked.js + Bootstrap CDN）
docker/entrypoint.sh    容器内 Postgres 初始化 + uvicorn 启动
```

### 请求流程

1. `POST /generate_report` 同步调用 `planner_agent(prompt)` 生成步骤列表（**规划阶段会阻塞响应**），
   在 Postgres 建 `Task(status="running")`，把步骤写进内存 `task_progress[task_id]`，然后起线程执行。
2. 线程 `run_agent_workflow` 逐步调用 `executor_agent_step(step_title, execution_history, prompt)`，
   每步把状态从 `pending` → `running` → `done`（或 `error`）写回内存，并把历史累积成上下文。
3. 全部完成后把最终 Markdown 与步骤历史序列化进 `Task.result`，状态改 `done`。
4. 前端每 2 秒轮询 `/task_progress/{id}`；状态为 `done` 时拉 `/task_status/{id}`，用 `marked.js`
   渲染报告，并提供下载按钮。

### 规划契约（容易踩）

`planner_agent` 的提示词要求模型只输出 Python/JSON 字符串列表（最多 7 步），解析顺序是
严格 JSON → `ast.literal_eval` → 代码围栏兜底。随后 `_ensure_contract` 会强制：

- 第 1 步必须是 Tavily 广搜；
- 第 2 步必须是基于第 1 步候选做 arXiv 匹配；
- 最后一步必须是 writer 生成带引用和 References 的 Markdown 报告；
- 缺失就注入，超出 7 步截断。

改规划行为要同时改提示词和 `_ensure_contract`，只改一处会被契约覆盖。

### 执行器路由

`executor_agent_step` **只看步骤标题的子串**决定调用哪个 agent：

| 标题含 | 调用 |
|---|---|
| `research` | `research_agent` |
| `draft` / `write` | `writer_agent` |
| `revise` / `edit` / `feedback` | `editor_agent` |
| 其他 | 抛 `ValueError("Unknown step type")` |

因此规划器生成的步骤必须带上这些英文关键词，否则工作流会在该步报错。

### Agent 与工具

- 三个 agent 都是 `aisuite.Client().chat.completions.create` 的薄包装，默认模型读
  `.env` 的 `OPENAI_MODEL`（没有 `provider:` 前缀会自动补 `openai:`），缺省
  `gpt-4.1-mini`；规划器共用同一默认。要换模型改 `.env` 或函数参数。
- OpenAI 兼容端点不一定支持 `o4-mini` / `gpt-4.1-mini`：本机端点只支持
  `deepseek-flash` / `deepseek-v4-pro`，必须靠 `OPENAI_MODEL` 覆盖。
- `research_agent` 传 `tools=[arxiv, tavily, wikipedia]`、`tool_choice="auto"`、`max_turns=5`；
  从 `intermediate_responses` / `intermediate_messages` 提取工具调用，附加成 HTML 列表。
- `tavily_search_tool` 需要 `TAVILY_API_KEY`，支持 `DLAI_TAVILY_BASE_URL` 覆盖端点。
- `arxiv_search_tool` 直接查 arXiv Atom API，并**默认下载 PDF、抽取前 6 页 / 5000 字符**
  覆盖 `summary`；带 5 次重试与退避。
- `wikipedia_search_tool` 取搜索首条结果的摘要。
- 工具失败统一返回 `[{"error": ...}]`，不抛异常；`research_agent` 直接把三个工具函数传给 aisuite，
  文件里的 `*_tool_def` 与 `tool_mapping` 当前未被调用。

## 文件依赖与运行链路

### 文件依赖关系树图

路径均相对本工程根目录；`──►` 表示依赖/调用，`┈┈►` 表示运行期回调，`◄──` 表示生成关系。

```text
Dockerfile
│  作用：镜像构建入口——装系统包、pip 安装依赖、COPY 整个目录、指定启动脚本
│  隐式边：COPY . /app 会带入本目录所有文件（含 .env、static/、templates/、src/、文档、__pycache__）
│
├─► requirements.txt
│     作用：Python 依赖清单（fastapi/uvicorn/sqlalchemy/aisuite/tavily-python/pdfminer.six 等）
│
└─► docker/entrypoint.sh
      作用：容器启动——启动 Postgres、幂等创建角色/库、导出 DATABASE_URL、exec uvicorn main:app
      │
      └─► main.py
            作用：FastAPI 入口——路由、Task 表、内存进度 task_progress、后台线程工作流
            │
            ├─► .env
            │     作用：本地配置/密钥（main.py 与 src/research_tools.py 都会 load_dotenv；不要打印内容）
            │
            ├─► templates/index.html
            │     作用：单页前端——提交研究主题、每 2 秒轮询进度、marked.js 渲染报告、下载 .md/.html
            │     ├─ 外部：Bootstrap CDN、marked.js CDN
            │     └┈► main.py 的 /generate_report、/task_progress/{id}、/task_status/{id}（HTTP 回调，形成环）
            │
            ├─► static/（由 main.py 以 StaticFiles 挂载为 /static）
            │     作用：图片资源目录；当前模板没有引用，实际是未被页面使用的资源
            │     ├─ static/arxiv_logo.png                       arXiv logo
            │     ├─ static/dl_logo.png                          DeepLearning.AI logo
            │     ├─ static/tavily_logo.svg                      Tavily logo
            │     ├─ static/wikipedia_logo.png                   Wikipedia logo
            │     └─ static/how_interactions_are_performed.png   交互流程示意图
            │
            └─► src/planning_agent.py
                  作用：planner_agent() 让模型拆最多 7 步计划，并用 _ensure_contract 强制前后步骤；
                        executor_agent_step() 按步骤标题关键词把任务分派给对应 agent
                  ├─ 外部：aisuite（模型取 .env 的 OPENAI_MODEL）
                  │
                  └─► src/agents.py
                        作用：research_agent / writer_agent / editor_agent 三个 agent；
                              research_agent 接三个检索工具，writer 写报告，editor 修订
                        └─► src/research_tools.py
                              作用：tavily_search_tool / arxiv_search_tool / wikipedia_search_tool；
                                    arXiv 结果会下载 PDF 并抽取前 6 页/5000 字符；工具报错返回 error 字典
                              ├─► .env（TAVILY_API_KEY、DLAI_TAVILY_BASE_URL；与 main.py 共用同一文件）
                              └─► 外部服务/库：Tavily API、arXiv Atom API + PDF、Wikipedia、
                                  requests/urllib3、PyMuPDF（可选）、pdfminer.six

旁支（无运行期依赖）：
├─ .env.example               作用：配置模板（占位值，复制为 .env 后填真实密钥）
├─ .gitignore                 作用：Git 忽略规则（.env、__pycache__/、*.db、.screenshots/ 等）
├─ README.md                  作用：使用说明——Docker 构建/运行、API 示例、常见排错
├─ CLAUDE.md                  作用：本 lab 的 Claude 工作指引（结构、命令、坑位）
└─ .screenshots/.tmp/         作用：空临时目录，无任何引用

生成物（Python 解释器生成，不属于源码依赖，可安全删除）：
├─ __pycache__/main.cpython-310.pyc                  ◄── main.py
├─ src/__pycache__/agents.cpython-310.pyc            ◄── src/agents.py
├─ src/__pycache__/planning_agent.cpython-310.pyc    ◄── src/planning_agent.py
└─ src/__pycache__/research_tools.cpython-310.pyc    ◄── src/research_tools.py
```

### 构建链

```text
docker build
 └─ Dockerfile
     ├─ apt-get install：gcc / libpq-dev / postgresql / postgresql-client / postgresql-contrib
     ├─ pip install -r requirements.txt
     ├─ COPY . /app          # 隐式带入 .env、static/、templates/、src/、文档、__pycache__
     └─ COPY docker/entrypoint.sh → /entrypoint.sh
          └─ CMD ["/entrypoint.sh"]

docker run --env-file .env -p 8000:8000 -p 5432:5432 --name fpsvc fastapi-postgres-service
 └─ docker/entrypoint.sh
     ├─ pg_ctlcluster <PG_MAJOR> main start
     ├─ pg_isready 等待 127.0.0.1:5432
     ├─ 幂等创建 POSTGRES_USER / POSTGRES_DB（默认 app / appdb）
     ├─ export DATABASE_URL=postgresql://app:local@127.0.0.1:5432/appdb
     └─ exec uvicorn main:app --host 0.0.0.0 --port 8000
```

### 运行时调用链

```text
浏览器 templates/index.html
 ├─ GET / ──► main.read_index ──► Jinja2 渲染 templates/index.html
 ├─ POST /generate_report
 │    └─ planner_agent() ──► aisuite ──► OpenAI（模型取 OPENAI_MODEL）
 │         ├─ 写 Postgres tasks（status=running）
 │         ├─ 写内存 task_progress
 │         └─ 起线程 run_agent_workflow
 │              └─ 每步 executor_agent_step()
 │                   ├─ research  → research_agent → Tavily / arXiv(+PDF) / Wikipedia
 │                   ├─ draft/write → writer_agent → aisuite
 │                   └─ revise/edit/feedback → editor_agent → aisuite
 │              └─ 完成后写 tasks.result（status=done/error）
 └─ 每 2 秒 GET /task_progress/{id}；状态 done 后 GET /task_status/{id}
      └─ marked.js 渲染报告，可下载 .md / .html
```

## 关键坑

- **`main.py` 启动时执行 `Base.metadata.drop_all`**：每次启动都会清空 `tasks` 表。
  需要保留任务时先去掉这段（README 建议用 `RESET_DB_ON_STARTUP` 环境变量保护）。
- **`DATABASE_URL` 必填且检查顺序有 bug**：代码先 `DATABASE_URL.startswith(...)` 再判空，
  未设置时会报 `AttributeError` 而不是预期的 `RuntimeError`。
- **`task_progress` 只在进程内存**：重启即丢；uvicorn 保持单 worker，多 worker 轮询会打空。
- **README 的 curl 示例带 `"model"` 字段，但 `PromptRequest` 只接收 `prompt`**，`model` 被忽略；
  模型由 `.env` 的 `OPENAI_MODEL`（或 `src/` 里的函数参数）决定。
- `.env` 是本地密钥文件（已被 `.gitignore` 忽略）：**不要读取、打印或提交其内容**。
  仓库只提交 `.env.example`（占位值）；本地复制为 `.env` 后填真实值。
  应用启动只强依赖 `DATABASE_URL`；联网搜索另需 `TAVILY_API_KEY`。
  注意 Dockerfile 用 `COPY . /app` 且没有 `.dockerignore`，`.env` 会被打进镜像。
- Wikipedia 工具已修复：`wikipedia` 包默认走 `http://` + 通用 UA，Wikimedia 返回 429
  纯文本，包内 `r.json()` 报 `Expecting value: line 1 column 1`。`src/research_tools.py`
  已强制 HTTPS + 描述性 UA；必须改 `wikipedia.wikipedia` 子模块的全局变量
  （`wikipedia.API_URL` 只是 `from .wikipedia import *` 的副本，改了不生效）。
- 本机实测（2026-11-18）：Tavily key 已验证可用；Tavily 缺 key 时工具返回 error 字典，
  工作流退化到 arXiv。
- `requirements.txt` 未锁版本，且重复列了 `python-dotenv` / `SQLAlchemy`。
- 前端模板与根 README 都指向的 `static/` 只是图片等资源；页面本身不依赖构建工具。

## 修改指引

- 改研究流程/步数：`src/planning_agent.py` 的提示词 + `_ensure_contract`
- 改步骤到 agent 的分配：`executor_agent_step` 的关键词分支（或同步改规划器措辞）
- 改检索能力：`src/research_tools.py` 的工具函数 + `*_tool_def` + `tool_mapping`
- 改页面/交互：`templates/index.html`；改接口或数据模型：`main.py`
- 改容器与数据库初始化：`Dockerfile`、`docker/entrypoint.sh`
