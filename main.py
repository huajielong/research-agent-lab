from sqlalchemy.orm.session import Session

import copy
import html
import ipaddress
import json
import os
import secrets
import threading
import time
import uuid
from collections import defaultdict, deque
from datetime import datetime

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from sqlalchemy import Column, DateTime, String, Text, create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from src.planning_agent import executor_agent_step, planner_agent

# === 加载环境变量 ===
load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL 未设置")

# 兼容 Heroku 的 postgres:// URL 格式
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)


def _env_int(name: str, default: int, *, minimum: int = 1) -> int:
    try:
        return max(minimum, int(os.getenv(name, str(default))))
    except ValueError:
        return default


# === 运行配置 ===
# 默认 fail closed：必须设置 APP_API_KEY；只有显式 ALLOW_ANONYMOUS=1 才允许无 key。
APP_API_KEY = os.getenv("APP_API_KEY", "").strip()
ALLOW_ANONYMOUS = os.getenv("ALLOW_ANONYMOUS", "").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)
if not APP_API_KEY and not ALLOW_ANONYMOUS:
    raise RuntimeError(
        "APP_API_KEY 未设置：请设置 APP_API_KEY，"
        "或仅在本地开发时显式设置 ALLOW_ANONYMOUS=1。"
    )
MAX_PROMPT_CHARS = _env_int("MAX_PROMPT_CHARS", 4000)
RATE_LIMIT_PER_MINUTE = _env_int("RATE_LIMIT_PER_MINUTE", 5)
MAX_RATE_BUCKETS = _env_int("MAX_RATE_BUCKETS", 10_000)
MAX_ACTIVE_TASKS = _env_int("MAX_ACTIVE_TASKS", 10)
MAX_CONCURRENT_TASKS = _env_int("MAX_CONCURRENT_TASKS", 2)
# 逗号分隔；留空表示只允许同源（同源请求本来就不需要 CORS）。
CORS_ALLOW_ORIGINS = [
    origin.strip()
    for origin in os.getenv("CORS_ALLOW_ORIGINS", "").split(",")
    if origin.strip()
]

# === 数据库初始化 ===
Base = declarative_base()
engine = create_engine(DATABASE_URL, echo=False, future=True)
SessionLocal = sessionmaker[Session](bind=engine)


class Task(Base):
    __tablename__ = "tasks"
    id = Column(String, primary_key=True, index=True)
    prompt = Column(Text)
    status = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    result = Column(Text)


if os.getenv("RESET_DB_ON_STARTUP") == "1":
    try:
        Base.metadata.drop_all(bind=engine)
    except Exception as e:
        print(f"❌ 数据库清理失败：{e}")

try:
    Base.metadata.create_all(bind=engine)
except Exception as e:
    print(f"❌ 数据库创建失败：{e}")

# === FastAPI ===
app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ALLOW_ORIGINS,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "X-API-Key"],
)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# 任务进度、并发与限流状态（单进程内；多 worker 部署时每个进程各自计数）
task_progress: dict[str, dict] = {}
task_progress_lock = threading.Lock()
active_tasks = 0
active_tasks_lock = threading.Lock()
task_semaphore = threading.BoundedSemaphore(MAX_CONCURRENT_TASKS)
rate_events: dict[str, deque] = defaultdict(deque)
rate_lock = threading.Lock()


class PromptRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=MAX_PROMPT_CHARS)


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _require_api_key(request: Request) -> None:
    """默认必须带正确的 X-API-Key；只有 ALLOW_ANONYMOUS=1 才跳过。"""
    if ALLOW_ANONYMOUS:
        return
    provided = request.headers.get("X-API-Key", "")
    if not secrets.compare_digest(provided.encode("utf-8"), APP_API_KEY.encode("utf-8")):
        raise HTTPException(status_code=401, detail="无效或缺失的 API Key")


def _rate_key(request: Request) -> str:
    host = _client_ip(request)
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return host
    if address.version == 6:
        # 同一个 /64 内共享一个桶，避免轮换 IPv6 地址绕过限流。
        return str(ipaddress.ip_network(f"{address}/64", strict=False))
    return str(address)


def _check_rate_limit(request: Request) -> None:
    now = time.monotonic()
    key = _rate_key(request)
    with rate_lock:
        bucket = rate_events[key]
        while bucket and now - bucket[0] >= 60:
            bucket.popleft()
        if len(bucket) >= RATE_LIMIT_PER_MINUTE:
            raise HTTPException(status_code=429, detail="请求过于频繁，请稍后再试")
        bucket.append(now)

        if len(rate_events) > MAX_RATE_BUCKETS:
            # 先清掉过期桶；仍超限则按最近一次请求时间淘汰最旧的桶。
            # 不要整体 clear()：否则攻击者可以用大量源地址重置所有桶。
            expired = [
                k
                for k, existing in rate_events.items()
                if not existing or now - existing[-1] >= 60
            ]
            for k in expired:
                rate_events.pop(k, None)
            if len(rate_events) > MAX_RATE_BUCKETS:
                overflow = len(rate_events) - MAX_RATE_BUCKETS
                oldest = sorted(
                    rate_events.items(),
                    key=lambda item: item[1][-1] if item[1] else 0.0,
                )[:overflow]
                for k, _ in oldest:
                    rate_events.pop(k, None)


@app.get("/", response_class=HTMLResponse)
def read_index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/api", response_class=JSONResponse)
def health_check(request: Request):
    return {"status": "ok", "auth_required": not ALLOW_ANONYMOUS}


@app.post("/generate_report")
def generate_report(req: PromptRequest, request: Request):
    _require_api_key(request)
    _check_rate_limit(request)

    global active_tasks
    with active_tasks_lock:
        if active_tasks >= MAX_ACTIVE_TASKS:
            raise HTTPException(status_code=429, detail="当前任务过多，请稍后再试")
        active_tasks += 1

    task_id = str(uuid.uuid4())
    try:
        db = SessionLocal()
        try:
            db.add(Task(id=task_id, prompt=req.prompt, status="running"))
            db.commit()
        finally:
            db.close()

        with task_progress_lock:
            task_progress[task_id] = {"steps": []}

        initial_plan_steps = planner_agent(req.prompt)

        with task_progress_lock:
            for step_title in initial_plan_steps:
                task_progress[task_id]["steps"].append(
                    {
                        "title": step_title,
                        "status": "pending",
                        "description": "等待执行",
                        "substeps": [],
                    }
                )

        thread = threading.Thread(
            target=run_agent_workflow, args=(task_id, req.prompt, initial_plan_steps)
        )
        thread.start()
        return {"task_id": task_id}
    except HTTPException:
        with active_tasks_lock:
            active_tasks = max(0, active_tasks - 1)
        raise
    except Exception as exc:
        with active_tasks_lock:
            active_tasks = max(0, active_tasks - 1)
        _mark_task_failed(task_id)
        print(f"任务 {task_id} 启动失败：{exc}")
        raise HTTPException(status_code=502, detail="任务启动失败，请稍后重试") from exc


def _mark_task_failed(task_id: str) -> None:
    db = SessionLocal()
    try:
        task = db.query(Task).filter(Task.id == task_id).first()
        if task:
            task.status = "error"
            task.updated_at = datetime.utcnow()
            db.commit()
    except Exception as exc:
        print(f"任务 {task_id} 状态更新失败：{exc}")
    finally:
        db.close()


@app.get("/task_progress/{task_id}")
def get_task_progress(task_id: str, request: Request):
    _require_api_key(request)
    with task_progress_lock:
        data = task_progress.get(task_id)
        return copy.deepcopy(data) if data else {"steps": []}


@app.get("/task_status/{task_id}")
def get_task_status(task_id: str, request: Request):
    _require_api_key(request)
    db = SessionLocal()
    try:
        task = db.query(Task).filter(Task.id == task_id).first()
        if not task:
            raise HTTPException(status_code=404, detail="任务不存在")
        status = task.status
        result_raw = task.result
    finally:
        db.close()

    try:
        result = json.loads(result_raw) if result_raw else None
    except (TypeError, ValueError):
        result = None
    return {"status": status, "result": result}


def esc(value) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def format_history(history):
    return "\n\n".join(
        f"🔹 {title}\n{desc}\n\n📝 输出：\n{output}" for title, desc, output in history
    )


def run_agent_workflow(task_id: str, prompt: str, initial_plan_steps: list):
    global active_tasks
    try:
        with task_semaphore:
            _run_agent_workflow(task_id, prompt, initial_plan_steps)
    finally:
        with active_tasks_lock:
            active_tasks = max(0, active_tasks - 1)


def _run_agent_workflow(task_id: str, prompt: str, initial_plan_steps: list):
    with task_progress_lock:
        task_data = task_progress.get(task_id)
        steps_data = task_data["steps"] if task_data else []

    execution_history = []

    def update_step_status(index, status, description="", substep=None):
        with task_progress_lock:
            if index < len(steps_data):
                steps_data[index]["status"] = status
                if description:
                    steps_data[index]["description"] = description
                if substep:
                    steps_data[index]["substeps"].append(substep)
                steps_data[index]["updated_at"] = datetime.utcnow().isoformat()

    try:
        for i, plan_step_title in enumerate(initial_plan_steps):
            update_step_status(i, "running", f"正在执行：{plan_step_title}")

            actual_step_description, agent_name, output = executor_agent_step(
                plan_step_title, execution_history, prompt
            )

            execution_history.append([plan_step_title, actual_step_description, output])

            update_step_status(
                i,
                "done",
                f"已完成：{plan_step_title}",
                {
                    "title": f"调用 {agent_name}",
                    # 所有来自用户/模型的内容都必须转义；这个字符串会被前端当作 HTML 渲染。
                    "content": f"""
<div style='border:1px solid #ccc; border-radius:8px; padding:10px; margin:8px 0; background:#fff;'>
  <div style='font-weight:bold; color:#2563eb;'>📘 用户提示词</div>
  <div style='white-space:pre-wrap;'>{esc(prompt)}</div>

  <div style='font-weight:bold; color:#16a34a; margin-top:8px;'>📜 上一步</div>
  <pre style='white-space:pre-wrap; background:#f9fafb; padding:6px; border-radius:6px; margin:0;'>
{esc(format_history(execution_history[-2:-1]))}
  </pre>

  <div style='font-weight:bold; color:#f59e0b; margin-top:8px;'>🧹 你的下一个任务</div>
  <div style='white-space:pre-wrap;'>{esc(actual_step_description)}</div>

  <div style='font-weight:bold; color:#10b981; margin-top:8px;'>✅ 输出</div>
  <div style='white-space:pre-wrap;'>
{esc(output)}
  </div>
</div>
""".strip(),
                },
            )

        final_report_markdown = (
            execution_history[-1][-1] if execution_history else "未生成报告。"
        )

        with task_progress_lock:
            history_snapshot = copy.deepcopy(steps_data)
        result = {"html_report": final_report_markdown, "history": history_snapshot}

        db = SessionLocal()
        try:
            task = db.query(Task).filter(Task.id == task_id).first()
            if task:
                task.status = "done"
                task.result = json.dumps(result)
                task.updated_at = datetime.utcnow()
                db.commit()
        finally:
            db.close()

    except Exception as e:
        print(f"任务 {task_id} 的工作流出错：{e}")
        if steps_data:
            error_step_index = next(
                (i for i, s in enumerate(steps_data) if s["status"] == "running"),
                len(steps_data) - 1,
            )
            if error_step_index >= 0:
                update_step_status(
                    error_step_index,
                    "error",
                    "执行过程中出错，请稍后重试",
                    {"title": "错误", "content": "内部错误，请查看服务端日志"},
                )

        _mark_task_failed(task_id)
