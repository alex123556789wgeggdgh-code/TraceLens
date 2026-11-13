#!/bin/bash
# TraceLens 容器入口包装（子任务 5.4 / t17）
# ---------------------------------------------------------------------------
# 镜像的默认 ENTRYPOINT 是任务规格里那条命令，逐字不动：
#     python -m agent_explainer --mode team --host 0.0.0.0 --port 8765
# 这个包装只在**编排侧**（docker-compose.yml / scripts/docker-run.sh）用
# --entrypoint 顶上，做三件规格没要求、但运维真需要的事：
#
#   1. 目录可写性自检：非 root（uid 10001）跑容器时，宿主绑定挂载的 ./data
#      可能是 root 拥有、0755。这时产品会在写 records.jsonl 时报 500
#      RECORD_STORE_WRITE_FAILED —— 与其让人对着日志猜，不如启动就拒并给出
#      该敲的那一条 chown 命令。
#   2. 日志落文件：产品按 12-factor 只写 stdout，docker logs 里滚掉就没了。
#      这里用 tee 把 stdout/stderr **同时**留给 docker 和 ./logs/tracelens-*.log
#      （`exec` 保留 PID 1 语义，SIGTERM 直接送到 python，正常优雅退出）。
#   3. 首启事实抬头：把 argv、uid、数据目录写进日志第一行，事后排障不用问。
#
# 注意：首启时控制台会打印**一次性管理员口令**（team 模式零用户 + 没注入
# TRACELENS_ADMIN_PASSWORD 时）。这份日志因此含敏感信息，文件权限按 0600 建。
# 想避免它出现，就在 .env 里显式设 TRACELENS_ADMIN_PASSWORD（推荐）。
# ---------------------------------------------------------------------------
set -euo pipefail

DATA_DIR="${TRACELENS_DATA_DIR:-/app/var}"
LOG_DIR="${TRACELENS_LOG_DIR:-/app/logs}"
RUN_UID="$(id -u 2>/dev/null || echo '?')"
RUN_GID="$(id -g 2>/dev/null || echo '?')"

if [ "$#" -eq 0 ]; then
    echo "[entrypoint] 没有收到要执行的命令；用 --entrypoint 覆盖时请同时给 command" >&2
    exit 64
fi

# ---- 1. 数据目录必须可写（不可写就不启动，绝不"先跑起来再说"）------------
mkdir -p "$DATA_DIR" 2>/dev/null || true
if [ ! -d "$DATA_DIR" ]; then
    cat >&2 <<EOF
[entrypoint] 数据目录建不出来：$DATA_DIR
  容器用户 uid=$RUN_UID gid=$RUN_GID
  绑定挂载的宿主目录必须存在且可写，例如：
      mkdir -p ./data ./logs && sudo chown -R 10001:10001 ./data ./logs
EOF
    exit 2
fi
if [ ! -w "$DATA_DIR" ]; then
    cat >&2 <<EOF
[entrypoint] 数据目录不可写：$DATA_DIR（uid=$RUN_UID gid=$RUN_GID）
  这会让首启就直接失败（产品写 records.jsonl 失败会返回 500
  RECORD_STORE_WRITE_FAILED，绝不假装"导入成功、重启就没了"）。
  在宿主机上修一次即可（把 10001 换成镜像里 --user 的 uid）：
      sudo chown -R 10001:10001 ./data ./logs
  或者临时用 root 跑（不推荐，镜像本身是非 root 交付）：
      docker compose run --rm --user 0 tracelens chown -R 10001:10001 /app/var
EOF
    exit 2
fi

# ---- 2. 日志同时落文件（写不了就只留 docker logs，并明说）----------------
if mkdir -p "$LOG_DIR" 2>/dev/null && [ -w "$LOG_DIR" ]; then
    LOG_FILE="$LOG_DIR/tracelens-$(date -u +%Y%m%d).log"
    ( umask 077; : >> "$LOG_FILE" ) 2>/dev/null || true
    {
        echo "==== $(date -u +%Y-%m-%dT%H:%M:%SZ) container start ===="
        echo "uid=$RUN_UID gid=$RUN_GID data_dir=$DATA_DIR"
        echo "argv: $*"
    } >>"$LOG_FILE" 2>/dev/null || true
    # tee 的进程替换保持 exec：PID 1 仍是 python，docker stop 的 SIGTERM 直达应用
    exec "$@" > >(tee -a "$LOG_FILE") 2> >(tee -a "$LOG_FILE" >&2)
else
    echo "[entrypoint] 日志目录不可写：$LOG_DIR —— 只保留 docker logs" >&2
    exec "$@"
fi
