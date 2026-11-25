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
: "${POSTGRES_USER:=app}"
: "${POSTGRES_PASSWORD:=local}"
: "${POSTGRES_DB:=appdb}"


# 若角色不存在则创建
if ! su -s /bin/bash postgres -c "psql -tAc \"SELECT 1 FROM pg_roles WHERE rolname='${POSTGRES_USER}'\"" | grep -q 1; then
  su -s /bin/bash postgres -c "psql -c \"CREATE USER ${POSTGRES_USER} WITH PASSWORD '${POSTGRES_PASSWORD}';\""
fi

# 若数据库不存在则创建
if ! su -s /bin/bash postgres -c "psql -tAc \"SELECT 1 FROM pg_database WHERE datname='${POSTGRES_DB}'\"" | grep -q 1; then
  su -s /bin/bash postgres -c "psql -c \"CREATE DATABASE ${POSTGRES_DB} OWNER ${POSTGRES_USER};\""
fi

# 为应用定义 DSN
export DATABASE_URL="${DATABASE_URL:-postgresql://${POSTGRES_USER}:${POSTGRES_PASSWORD}@127.0.0.1:5432/${POSTGRES_DB}}"
echo "🔗 DATABASE_URL=${DATABASE_URL}"

# 启动 FastAPI
exec uvicorn main:app --host 0.0.0.0 --port 8000
