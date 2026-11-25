#!/usr/bin/env bash
set -euo pipefail

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

# --- 应用/DSN 变量 ---
# 不再提供默认密码：部署时忘记设置就会失败，而不是静默使用已知口令。
: "${POSTGRES_USER:=app}"
: "${POSTGRES_PASSWORD:?POSTGRES_PASSWORD must be set (copy .env.example to .env and change it)}"
: "${POSTGRES_DB:=appdb}"

# 以 postgres 用户执行命令；参数按 argv 传递，不把变量拼进 shell 字符串。
as_postgres() {
  if command -v runuser >/dev/null 2>&1; then
    runuser -u postgres -- "$@"
  else
    su -s /bin/bash postgres -c 'exec "$@"' _ "$@"
  fi
}

# 角色存在性检查：用 psql -v 传值，由 psql 负责标识符/字面量转义。
if ! as_postgres psql -tAc "SELECT 1 FROM pg_roles WHERE rolname = :'role'" \
      -v role="$POSTGRES_USER" | grep -q 1; then
  as_postgres psql \
    -v role="$POSTGRES_USER" \
    -v password="$POSTGRES_PASSWORD" \
    -c "CREATE USER :\"role\" WITH PASSWORD :'password';"
fi

# 数据库存在性检查与创建
if ! as_postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname = :'db'" \
      -v db="$POSTGRES_DB" | grep -q 1; then
  as_postgres psql \
    -v db="$POSTGRES_DB" \
    -v role="$POSTGRES_USER" \
    -c "CREATE DATABASE :\"db\" OWNER :\"role\";"
fi

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
echo "🔗 DATABASE_URL set (host=127.0.0.1, port=5432, db=${POSTGRES_DB}); password redacted"

# 启动 FastAPI
exec uvicorn main:app --host 0.0.0.0 --port 8000
