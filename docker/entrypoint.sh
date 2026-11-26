#!/usr/bin/env bash
set -euo pipefail

# --- 应用/DSN 变量 ---
# 不再提供默认密码：部署时忘记设置就会失败，而不是静默使用已知口令。
: "${POSTGRES_USER:=app}"
: "${POSTGRES_PASSWORD:?POSTGRES_PASSWORD must be set (copy .env.example to .env and change it)}"
: "${POSTGRES_DB:=appdb}"

export POSTGRES_USER POSTGRES_PASSWORD POSTGRES_DB


# --- 启动 Debian 默认的 Postgres 集群 ---
PG_MAJOR="$(psql -V | awk '{print $3}' | cut -d. -f1)"
echo "🚀 正在启动 Postgres 集群 ${PG_MAJOR}/main..."
pg_ctlcluster "${PG_MAJOR}" main start

# 等待 Postgres 就绪
for i in $(seq 1 60); do
  if pg_isready -h 127.0.0.1 -p 5432 -U postgres >/dev/null 2>&1; then
    echo "✅ Postgres 已就绪"
    break
  fi
  sleep 1
done

# 以 postgres 用户执行命令；参数按 argv 传递，不把变量拼进 shell 字符串。
# --preserve-environment 让 psql 能通过 \getenv 读取 POSTGRES_PASSWORD。
as_postgres() {
  if command -v runuser >/dev/null 2>&1; then
    runuser --preserve-environment -u postgres -- "$@"
  else
    su --preserve-environment -s /bin/bash postgres -c 'exec "$@"' _ "$@"
  fi
}

# psql variable substitution works in stdin SQL, not in the -c option.
# Quote identifiers and values using psql; never interpolate into a shell command.
as_postgres psql -X -v ON_ERROR_STOP=1 -v role="$POSTGRES_USER" -v db="$POSTGRES_DB" <<'SQL'
\getenv password POSTGRES_PASSWORD
SELECT format('CREATE USER %I WITH PASSWORD %L', :'role', :'password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'role')
\gexec
SELECT format('CREATE DATABASE %I OWNER %I', :'db', :'role')
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = :'db')
\gexec
SQL

# 为应用定义 DSN；用户名/密码/库名做 URL 编码，避免特殊字符拼坏 URL。
if [ -z "${DATABASE_URL:-}" ]; then
  DATABASE_URL="$(python -I - <<'PY'
import os
import urllib.parse

user = urllib.parse.quote(os.environ["POSTGRES_USER"], safe="")
password = urllib.parse.quote(os.environ["POSTGRES_PASSWORD"], safe="")
db = urllib.parse.quote(os.environ["POSTGRES_DB"], safe="")
print(f"postgresql://{user}:{password}@127.0.0.1:5432/{db}")
PY
)"
  export DATABASE_URL
fi

# 只打印非敏感信息；不要把完整 DSN（含密码）写进日志。
echo "Database configured; credentials redacted"

# 启动 FastAPI
exec uvicorn main:app --host 0.0.0.0 --port 8000
