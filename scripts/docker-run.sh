#!/bin/bash
# 用纯 docker run 起一个 TraceLens 团队模式容器（子任务 5.4 / t17）。
# 与 docker compose 起的是同一个镜像；区别只是这里一条命令带全参数，适合
# "不想装 compose"或临时排障的场景。
#
#   ./scripts/docker-run.sh                       # 起在 8765，数据落 ./data ./logs
#   TRACELENS_PORT=9000 ./scripts/docker-run.sh   # 换宿主端口
#   ./scripts/docker-run.sh --build               # 先构建再起
#   ./scripts/docker-run.sh --rm --name t17-try   # 临时容器，退出即删
#
# 管理员口令来源（按优先级）：
#   1) 环境变量 TRACELENS_ADMIN_PASSWORD
#   2) 仓库根 .env 里的 TRACELENS_ADMIN_PASSWORD
#   3) 都没有 → 不设置该变量，产品会**生成一次性口令并打印到容器日志**（会提醒你）
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

case "$(uname -s 2>/dev/null || echo unknown)" in
    MINGW*|MSYS*|CYGWIN*) export MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL='*' ;;
esac

PYTHON="${PYTHON:-python}"
command -v python >/dev/null 2>&1 || PYTHON="${PYTHON:-python3}"

BUILD=0
RM=0
NAME="tracelens"
mkdir -p data logs
while [ "$#" -gt 0 ]; do
    case "$1" in
        --build) BUILD=1; shift ;;
        --rm) RM=1; shift ;;
        --name) NAME="$2"; shift 2 ;;
        -h|--help) sed -n '2,16p' "$0"; exit 0 ;;
        *) echo "未知参数：$1" >&2; exit 2 ;;
    esac
done

if [ -f .env ]; then
    # 只取这一项，不把整份 .env 塞进 shell（避免误传别的变量）
    password="$(grep -E '^[[:space:]]*TRACELENS_ADMIN_PASSWORD=' .env | tail -1 | cut -d= -f2- || true)"
    if [ -n "${password:-}" ] && [ -z "${TRACELENS_ADMIN_PASSWORD:-}" ]; then
        TRACELENS_ADMIN_PASSWORD="$password"
        echo "== 管理员口令取自 .env =="
    fi
fi
if [ -z "${TRACELENS_ADMIN_PASSWORD:-}" ]; then
    echo "!! 没给 TRACELENS_ADMIN_PASSWORD：容器首启会把一次性口令打印在日志里"
    echo "   （docker logs ${NAME}），首次登录后必须改密。要固定的口令请写进 .env。"
fi

VERSION="${TRACELENS_VERSION:-$("$PYTHON" -c "import sys; sys.path.insert(0, '.'); from agent_explainer import server; print(server.SERVER_VERSION)")}"
IMAGE="tracelens:${VERSION}"
PORT="${TRACELENS_PORT:-8765}"
DEMO="${TRACELENS_DEMO:-zh}"

if [ "$BUILD" = "1" ] || ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
    "$(dirname "${BASH_SOURCE[0]}")/docker-build.sh"
fi

docker rm -f "$NAME" >/dev/null 2>&1 || true

env_args=()
[ -n "${TRACELENS_ADMIN_PASSWORD:-}" ] && env_args+=("-e" "TRACELENS_ADMIN_PASSWORD=${TRACELENS_ADMIN_PASSWORD}")
[ -n "${TRACELENS_LLM_MODE:-}" ] && env_args+=("-e" "TRACELENS_LLM_MODE=${TRACELENS_LLM_MODE}")
[ -n "${TRACELENS_LLM_PROVIDER:-}" ] && env_args+=("-e" "TRACELENS_LLM_PROVIDER=${TRACELENS_LLM_PROVIDER}")
[ -n "${TRACELENS_LLM_BASE_URL:-}" ] && env_args+=("-e" "TRACELENS_LLM_BASE_URL=${TRACELENS_LLM_BASE_URL}")
[ -n "${TRACELENS_LLM_MODEL:-}" ] && env_args+=("-e" "TRACELENS_LLM_MODEL=${TRACELENS_LLM_MODEL}")
[ -n "${TRACELENS_LLM_API_KEY:-}" ] && env_args+=("-e" "TRACELENS_LLM_API_KEY=${TRACELENS_LLM_API_KEY}")
[ -n "${TRACELENS_LLM_ALLOW_OUTBOUND:-}" ] && env_args+=("-e" "TRACELENS_LLM_ALLOW_OUTBOUND=${TRACELENS_LLM_ALLOW_OUTBOUND}")

run_flags=(-d --name "$NAME" -p "${PORT}:8765"
           -v "$(pwd)/data:/app/var" -v "$(pwd)/logs:/app/logs"
           --entrypoint /usr/local/bin/tracelens-entrypoint
           --restart unless-stopped
           --security-opt no-new-privileges:true
           --tmpfs /tmp)
[ "$RM" = "1" ] && run_flags+=(--rm)

echo "== docker run ${IMAGE} =="
# --features：5.0 总裁办账本（identities.jsonl / keyring.json / delegations / letters）
# 的根。默认值是 <应用根>/var/features 且**不跟随 --data-dir**（桌面侧就栽在这条默认值上，
# 账本被写进只读的包内目录，见 docs/audit/v050-sidecar.md）。容器里应用根=/app、
# 挂载卷=/app/var，默认落点本来就在卷内（实测见 docs/audit/v050-docker.md §4）；
# 显式钉死是为了让这条不变量与 WORKDIR / --data-dir 的取值无关——升级换镜像不冲账本。
docker run "${run_flags[@]}" "${env_args[@]}" "$IMAGE" \
    python -m agent_explainer --mode team --host 0.0.0.0 --port 8765 \
    --demo "$DEMO" --data-dir /app/var --features /app/var/features

echo -n "== 等 /health（最多 90s）"
for _ in $(seq 1 180); do
    if docker exec "$NAME" python /usr/local/bin/tracelens-healthcheck.py >/dev/null 2>&1; then
        echo " → 就绪"
        docker exec "$NAME" python /usr/local/bin/tracelens-healthcheck.py
        break
    fi
    echo -n "."
    sleep 0.5
done
if ! docker exec "$NAME" python /usr/local/bin/tracelens-healthcheck.py >/dev/null 2>&1; then
    echo " → 超时；docker logs ${NAME} 看原因" >&2
    docker logs "$NAME" 2>&1 | tail -20 >&2
    exit 1
fi

cat <<EOF

== 起来了 ==
  界面/接口 : http://127.0.0.1:${PORT}/          （团队模式 → 先登录）
  健康检查  : http://127.0.0.1:${PORT}/health    （200 且 status="ok"）
  数据      : $(pwd)/data   → 容器 /app/var
  总裁办    : $(pwd)/data/features → 容器 /app/var/features
              （身份账本 identities.jsonl + 密钥库 keyring.json，换镜像不丢）
              注意：team 模式下 /api/secretariat/* 需登录——未登录 GET
              /api/secretariat/status 是 401 AUTH_REQUIRED（实测，非缺陷；
              验收要先 POST /api/auth/login 拿会话，再带 Cookie + X-CSRF-Token）
  日志      : $(pwd)/logs   （容器入口把 stdout 同时落成文件）
  跟随日志  : docker logs -f ${NAME}
  停止      : docker stop ${NAME}（数据留在 ./data，再 run/compose up 就回来）
EOF
