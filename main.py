import os
import uuid
import json
import threading
from datetime import datetime
from pathlib import Path
from src.console import configure_console
from src.errors import WORKFLOW_ERROR, RESTART_ERROR, public_error

configure_console()

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import create_engine, Column, Text, DateTime, String
from sqlalchemy.orm import sessionmaker, declarative_base

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / '.env')
from src.security import (authorize, ALLOW_ANONYMOUS, MAX_PROMPT_CHARS,
                          MAX_ACTIVE_TASKS, task_semaphore, sanitize_html, safe_result)
# Load model configuration before importing the clients and their default arguments.
from src.planning_agent import planner_agent, executor_agent_step

DATABASE_URL = os.getenv('DATABASE_URL') or 'sqlite:///' + (ROOT / 'tasks.db').as_posix()
if DATABASE_URL.startswith('postgres://'):
    DATABASE_URL = DATABASE_URL.replace('postgres://', 'postgresql://', 1)
Base = declarative_base()
engine = create_engine(DATABASE_URL, connect_args={'check_same_thread': False} if DATABASE_URL.startswith('sqlite:') else {})
SessionLocal = sessionmaker(bind=engine)


class Task(Base):
    __tablename__ = 'tasks'
    id = Column(String, primary_key=True, index=True)
    prompt = Column(Text)
    status = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    result = Column(Text)


if os.getenv('RESET_DB_ON_STARTUP') == '1':
    Base.metadata.drop_all(bind=engine)
Base.metadata.create_all(bind=engine)
# A process restart cannot resume a provider call; keep its saved work visible.
with SessionLocal() as db:
    for task in db.query(Task).filter(Task.status == 'running').all():
        result = json.loads(task.result) if task.result else {}
        result['error'] = RESTART_ERROR
        task.result = json.dumps(result, ensure_ascii=False)
        task.status = 'error'
    db.commit()

app = FastAPI(title='Research 工作台')
app.mount('/static', StaticFiles(directory=ROOT / 'static'), name='static')
templates = Jinja2Templates(directory=ROOT / 'templates')
task_progress = {}
task_lock = threading.RLock()


@app.middleware('http')
async def protect_workspace(request, call_next):
    if request.url.path not in ('/', '/api') and not request.url.path.startswith('/static/'):
        try:
            authorize(request)
        except HTTPException as exc:
            return JSONResponse({'detail': exc.detail}, status_code=exc.status_code,
                                headers={'Cache-Control': 'no-store'})
    response = await call_next(request)
    if not request.url.path.startswith('/static/'):
        response.headers['Cache-Control'] = 'no-store'
    return response


class PromptRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=MAX_PROMPT_CHARS)

    @field_validator('prompt')
    @classmethod
    def validate_prompt(cls, value):
        if not value.strip():
            raise ValueError('请输入研究主题')
        return value.strip()


@app.get('/', response_class=HTMLResponse)
def read_index(request: Request):
    return templates.TemplateResponse(request=request, name='index.html')


@app.get('/api')
def health_check():
    return {'status': 'ok', 'auth_required': not ALLOW_ANONYMOUS}


@app.get('/config')
def config():
    return {'model': os.getenv('OPENAI_MODEL') or 'OpenAI · o4-mini / gpt-4.1-mini',
            'max_prompt_chars': MAX_PROMPT_CHARS}


@app.get('/tasks')
def list_tasks():
    with SessionLocal() as db:
        tasks = db.query(Task).order_by(Task.created_at.desc()).limit(50).all()
        return {'tasks': [{'id': t.id, 'prompt': t.prompt, 'status': t.status,
                           'created_at': t.created_at.isoformat() + 'Z'} for t in tasks]}


@app.delete('/tasks/{task_id}')
def delete_task(task_id: str):
    delete_finished_tasks([task_id])
    return {'deleted': task_id}


class DeleteTasksRequest(BaseModel):
    task_ids: list[str] = Field(min_length=1, max_length=50)


@app.post('/tasks/delete')
def batch_delete_tasks(req: DeleteTasksRequest):
    return {'deleted': delete_finished_tasks(req.task_ids)}


def delete_finished_tasks(task_ids):
    ids = list(dict.fromkeys(task_ids))
    with SessionLocal() as db:
        tasks = db.query(Task).filter(Task.id.in_(ids)).all()
        if len(tasks) != len(ids):
            raise HTTPException(status_code=404, detail='任务不存在或已被删除')
        if any(task.status == 'running' for task in tasks):
            raise HTTPException(status_code=409, detail='研究正在运行，请等待结束后再删除')
        for task in tasks:
            db.delete(task)
        db.commit()
    for task_id in ids:
        task_progress.pop(task_id, None)
    return ids


def save_progress(task_id, status='running', report=None, error=None):
    result = {'history': safe_result(task_progress[task_id]['steps'])}
    if report is not None:
        result['html_report'] = sanitize_html(report)
    if error:
        result['error'] = public_error(error)
    with SessionLocal() as db:
        task = db.get(Task, task_id)
        task.status = status
        task.result = json.dumps(result, ensure_ascii=False)
        task.updated_at = datetime.utcnow()
        db.commit()


@app.post('/generate_report')
def generate_report(req: PromptRequest):
    # Serialize admission and insertion; queued tasks count towards the cap.
    with task_lock:
        with SessionLocal() as db:
            if db.query(Task).filter(Task.status == 'running').count() >= MAX_ACTIVE_TASKS:
                raise HTTPException(429, '当前任务过多，请稍后重试')
        return start_report(req)


def start_report(req):
    task_id = str(uuid.uuid4())
    with SessionLocal() as db:
        db.add(Task(id=task_id, prompt=req.prompt, status='running'))
        db.commit()
    task_progress[task_id] = {'steps': []}
    threading.Thread(target=plan_and_run, args=(task_id, req.prompt), daemon=True).start()
    return {'task_id': task_id}


def plan_and_run(task_id, prompt):
    with task_semaphore:
        _plan_and_run(task_id, prompt)


def _plan_and_run(task_id, prompt):
    try:
        plan = planner_agent(prompt)
        if not plan:
            raise ValueError('未能生成有效研究计划，请重试。')
        task_progress[task_id]['steps'] = [
            {'title': title, 'status': 'pending', 'description': '等待执行', 'substeps': []}
            for title in plan
        ]
        save_progress(task_id)
        run_agent_workflow(task_id, prompt, plan)
    except Exception as exc:
        print('研究规划失败：', task_id, type(exc).__name__)
        save_progress(task_id, status='error', error=WORKFLOW_ERROR)


@app.get('/task_progress/{task_id}')
def get_task_progress(task_id: str):
    if task_id in task_progress:
        return safe_result(task_progress[task_id])
    with SessionLocal() as db:
        task = db.get(Task, task_id)
        if not task:
            raise HTTPException(status_code=404, detail='任务不存在')
        result = json.loads(task.result) if task.result else {}
        return safe_result({'steps': result.get('history', [])})


@app.get('/task_status/{task_id}')
def get_task_status(task_id: str):
    with SessionLocal() as db:
        task = db.get(Task, task_id)
        if not task:
            raise HTTPException(status_code=404, detail='任务不存在')
        return {'status': task.status, 'prompt': task.prompt,
                'result': safe_result(json.loads(task.result)) if task.result else None}


def run_agent_workflow(task_id: str, prompt: str, initial_plan_steps: list):
    steps = task_progress[task_id]['steps']
    history = []
    try:
        for index, title in enumerate(initial_plan_steps):
            step = steps[index]
            step.update(status='running', description='正在执行：' + title)
            save_progress(task_id)
            description, agent, output = executor_agent_step(title, history, prompt)
            if output.startswith('[模型错误：'):
                raise RuntimeError(output)
            history.append([title, description, output])
            step.update(status='done', description='已完成：' + title)
            step['substeps'].append({'title': '调用 ' + agent, 'content': sanitize_html(output)})
            save_progress(task_id)
        save_progress(task_id, status='done', report=history[-1][-1] if history else '未生成报告。')
    except Exception as exc:
        print('研究执行失败：', task_id, type(exc).__name__)
        for step in steps:
            if step['status'] == 'running':
                step.update(status='error', description=WORKFLOW_ERROR)
        save_progress(task_id, status='error', error=WORKFLOW_ERROR)
