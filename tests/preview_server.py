"""Isolated UI integration fixture; never calls model/search providers.

Run: python tests/preview_server.py (serves on 127.0.0.1:8766).
"""
import os
import sys
import tempfile
import time
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
temp = tempfile.TemporaryDirectory()
os.environ['DATABASE_URL'] = 'sqlite:///' + temp.name.replace('\\', '/') + '/tasks.db'
os.environ.pop('RESET_DB_ON_STARTUP', None)
agents = types.ModuleType('src.planning_agent')


def planner(prompt):
    time.sleep(1)
    if prompt == 'TEST_ERROR':
        raise RuntimeError('测试：研究服务暂不可用')
    return ['Research agent: Tavily web search', 'Writer agent: final report']


def executor(title, history, prompt):
    time.sleep(1)
    return title, 'test_agent', '# 集成测试报告\n\n这是隔离测试结果，不是实际研究。\n\n## 参考来源\n\n[测试链接](https://example.com)\n\n<script>alert(1)</script>'


agents.planner_agent = planner
agents.executor_agent_step = executor
sys.modules['src.planning_agent'] = agents
import main
import uvicorn

try:
    uvicorn.run(main.app, host='127.0.0.1', port=8766)
finally:
    main.engine.dispose()
    temp.cleanup()
