# Research · 研究工作台

DeepLearning.AI Agentic AI 课程研究代理的本地 Web 应用。前端根据 ImageGen 设计稿重建，采用 Codex 风格的灰白工作台，使用 Sites 技能的本地开发流程，仍由 FastAPI 提供页面和接口。

## 启动

Python 3.10+，建议使用虚拟环境：

```powershell
python -m pip install -r requirements.txt
python -m uvicorn main:app --host 127.0.0.1 --port 8765
```

打开 http://127.0.0.1:8765 。接口文档为 `/docs`。

在 `.env` 配置：

```dotenv
APP_API_KEY=replace-with-a-long-random-workspace-secret
OPENAI_API_KEY=your-key
# 使用兼容服务时按需配置
OPENAI_BASE_URL=https://your-provider.example/v1
OPENAI_MODEL=your-model-name
TAVILY_API_KEY=your-tavily-key
# 可选；不填时使用当前项目目录的 tasks.db
# DATABASE_URL=postgresql://user:password@localhost:5432/appdb
```

默认必须设置 `APP_API_KEY`，否则应用拒绝启动。打开页面后输入访问密钥；浏览器只在当前标签页会话保存它，并在私有 API 请求中通过 `X-API-Key` 请求头发送。查询、生成和单条/批量删除记录都要求鉴权。仅限本机临时演示，可显式设置 `ALLOW_ANONYMOUS=1`；已有访问密钥时该开关不能绕过鉴权。部署到远程时使用 HTTPS。

这是单个共享工作台：持有同一密钥的使用者可以查看和管理全部研究记录，不提供多用户账户或租户隔离。`/`、静态资源和 `/api` 健康检查公开，其余路由需要密钥。生成接口有限流、排队总量和模型并发上限，参数见 `.env.example`。

模型需要支持工具调用。配置不会在页面或接口中公开密钥。页面顶部显示配置的模型名称；未设置模型名称时，规划使用 o4-mini，研究、写作、编辑使用 gpt-4.1-mini。

## 使用

- 输入主题和研究要求，或选择一个建议方向；点击“开始研究”，也可按 Ctrl/Cmd+Enter。
- 后端先规划，再逐步调用研究、写作、编辑智能体；点击时间线查看实际步骤输出。
- 最终报告支持阅读、复制、下载 Markdown 和 HTML。
- 侧栏和“研究记录”展示最近 50 条任务，可以搜索、重新打开或根据原问题再次研究。
- “研究记录”右键单条记录可打开删除菜单；勾选复选框后可批量删除，支持全选和取消选择。确认后永久删除对应报告及执行记录，并同步更新侧栏；运行中的研究不可选择或删除。更改搜索词会清空选择，避免误删隐藏记录。
- URL 中的 `?task=...` 可在刷新后恢复任务。进度和报告写入数据库；服务重启会将无法继续的运行中任务标记为中断，保留已有输出。
- 手机布局提供可开合的侧栏及横向步骤列表。

## 代码结构

| 文件 | 职责 |
| --- | --- |
| `main.py` | FastAPI、数据库、后台工作线程、任务持久化 |
| `src/planning_agent.py` | 研究规划和智能体分派 |
| `src/agents.py` | 研究、写作、编辑智能体 |
| `src/research_tools.py` | Tavily、arXiv、Wikipedia、论文文本提取 |
| `templates/index.html` | 工作台页面结构 |
| `static/workspace.css` | 响应式 Codex 风格界面 |
| `static/workspace.js` | 同源 API、轮询、任务切换、报告导出 |
| `static/vendor/` | 本地 marked 与 DOMPurify，保留库内许可证标记 |
| `docs/design/` | ImageGen 设计稿和桌面、手机验证截图 |

前端不依赖在线 CDN。模型输出和历史 HTML 经 DOMPurify 清理后再渲染，纯文本字段使用文本节点或 HTML 转义。

服务端先渲染 Markdown，再用 Bleach 清理新输出及旧记录中的报告/步骤 HTML，保留引用、表格和代码示例；浏览器渲染和 HTML 导出统一经过 DOMPurify。PDF 下载只允许明确列出的 arXiv 主机，每次跳转检查域名和公网解析地址；直连时检查对端地址，响应流限制为 50 MiB。通过代理时由可信代理负责目标网络访问控制。

## 接口

- `GET /config`：模型展示名称。
- `GET /tasks`：最近 50 条任务的摘要。
- `DELETE /tasks/{task_id}`：删除已结束的任务；运行中返回 409，不存在返回 404。
- `POST /tasks/delete`：接收 `{"task_ids":["任务编号"]}`，一次最多 50 条，全部通过检查后在同一事务内删除。
- `POST /generate_report`：接收 `{"prompt":"研究问题"}`，立即返回 `task_id`；规划和研究在后台执行。
- `GET /task_progress/{task_id}`：内存中的当前步骤，或数据库中保存的步骤。
- `GET /task_status/{task_id}`：状态、原问题、报告、执行记录和异常信息。
- `GET /api`：健康检查。

## 验证

安装测试依赖 `python -m pip install httpx`，然后：

```powershell
python -m unittest discover -s tests -v
node --check static/workspace.js
node --test tests/test_frontend_security.cjs
python -m py_compile main.py
```

后端测试使用临时 SQLite 数据库及模拟模型，覆盖鉴权、限流、空输入、历史记录、进度恢复、规划失败、删除和旧/新报告清理。PDF 测试模拟网络请求；Docker 入口测试使用 Bash 和命令桩，不启动真实数据库。`tests/preview_server.py` 可启动独立 UI 测试服务（8766），访问密钥为 `preview-only-key`，不调用真实模型。输入 `TEST_ERROR` 触发规划失败，`TEST_XSS` 验证步骤标题转义。

本次浏览器验证：真实旧任务读取和报告阅读、模拟提交到报告完成、刷新恢复、Markdown/HTML 下载、脚本标签过滤、1440px 桌面及 390px 手机布局、手机侧栏开合。未新建调用付费模型的真实研究。

## Docker

```powershell
docker build -t research-workspace .
docker run --rm -p 8000:8000 --env-file .env research-workspace
```

镜像沿用课程方案，在同一容器运行 PostgreSQL 和 API。启动前必须在 `.env` 设置非空 `POSTGRES_PASSWORD`，没有默认密码；角色及数据库标识符和密码交给 psql 安全引用，DSN 做 URL 编码且不会写入日志。数据库需要持久化时应配置卷或外部 PostgreSQL。`RESET_DB_ON_STARTUP=1` 会清空任务表，日常使用无需设置。

当前工作流适合单进程本地使用；不支持跨进程任务调度、任务取消或服务重启后继续模型调用。
