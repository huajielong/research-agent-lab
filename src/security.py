"""Shared-workspace authentication and output boundaries (one key = one workspace)."""
import copy
import ipaddress
import os
import secrets
import threading
import time
from collections import defaultdict, deque

import bleach
from fastapi import HTTPException
from markdown_it import MarkdownIt


def env_int(name, default):
    try:
        return max(1, int(os.getenv(name, str(default))))
    except ValueError:
        return default


APP_API_KEY = os.getenv('APP_API_KEY', '').strip()
ALLOW_ANONYMOUS = os.getenv('ALLOW_ANONYMOUS') == '1' and not APP_API_KEY
if not APP_API_KEY and not ALLOW_ANONYMOUS:
    raise RuntimeError('请设置 APP_API_KEY；仅限本地演示可显式设置 ALLOW_ANONYMOUS=1。')
MAX_PROMPT_CHARS = env_int('MAX_PROMPT_CHARS', 4000)
MAX_ACTIVE_TASKS = env_int('MAX_ACTIVE_TASKS', 10)
task_semaphore = threading.BoundedSemaphore(env_int('MAX_CONCURRENT_TASKS', 2))
rate_events = defaultdict(deque)
rate_lock = threading.Lock()


def check_rate(request, prefix, limit):
    host = request.client.host if request.client else 'unknown'
    if os.getenv('TRUST_PROXY_HEADERS') == '1':
        host = request.headers.get('X-Forwarded-For', host).split(',')[-1].strip()
    try:
        address = ipaddress.ip_address(host)
        host = str(ipaddress.ip_network(f'{address}/64', strict=False)) if address.version == 6 else str(address)
    except ValueError:
        pass
    now = time.monotonic()
    with rate_lock:
        key = prefix + host
        bucket = rate_events[key]
        while bucket and now - bucket[0] >= 60:
            bucket.popleft()
        if len(bucket) >= limit:
            raise HTTPException(429, '请求过于频繁，请稍后重试')
        bucket.append(now)
        maximum = env_int('MAX_RATE_BUCKETS', 10000)
        if len(rate_events) > maximum:
            oldest = sorted(rate_events, key=lambda k: rate_events[k][-1])[:len(rate_events) - maximum]
            for old in oldest:
                rate_events.pop(old, None)


def authorize(request):
    if not ALLOW_ANONYMOUS:
        supplied = request.headers.get('X-API-Key', '')
        if not secrets.compare_digest(supplied.encode(), APP_API_KEY.encode()):
            check_rate(request, 'auth:', env_int('AUTH_FAILURE_LIMIT', 5))
            raise HTTPException(401, '请输入正确的工作台访问密钥')
    if request.url.path == '/generate_report':
        check_rate(request, 'generate:', env_int('RATE_LIMIT_PER_MINUTE', 5))
    else:
        check_rate(request, 'api:', env_int('POLL_RATE_LIMIT_PER_MINUTE', 120))


def sanitize_html(value):
    # Render Markdown first so autolinks and code fences survive HTML cleaning.
    rendered = MarkdownIt('commonmark', {'html': True}).enable('table').render(value or '')
    # Per-call Cleaner: html5lib parsers must not be shared between worker threads.
    return bleach.clean(rendered, tags=[
        'a', 'abbr', 'b', 'blockquote', 'br', 'code', 'div', 'em', 'h1', 'h2',
        'h3', 'h4', 'h5', 'h6', 'hr', 'i', 'img', 'li', 'ol', 'p', 'pre',
        'span', 'strong', 'table', 'tbody', 'td', 'th', 'thead', 'tr', 'ul',
    ], attributes={'a': ['href', 'title', 'rel'], 'img': ['src', 'alt', 'title'],
                   'code': ['class'], 'pre': ['class']},
       protocols=['http', 'https', 'mailto'], strip=True)


def safe_result(value):
    """Also protect reports persisted by older versions; text fields remain text."""
    value = copy.deepcopy(value)
    if isinstance(value, dict):
        for key, item in value.items():
            value[key] = sanitize_html(item) if key in ('html_report', 'content') and isinstance(item, str) else safe_result(item)
    elif isinstance(value, list):
        value = [safe_result(item) for item in value]
    return value
