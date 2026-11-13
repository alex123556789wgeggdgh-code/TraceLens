#!/bin/bash
# TraceLens Docker 交付的真实验收（子任务 5.4 / t17）——一条命令跑完整个证据链。
#
#   ./scripts/docker-verify.sh              # 全新数据起跑（旧 ./data ./logs 会改名备份）
#   ./scripts/docker-verify.sh --keep-data  # 保留现有 ./data ./logs
#   ./scripts/docker-verify.sh --no-offline # 跳过"断网可运行"那一项
#
# 它依次做（每一步都真实执行，失败就记 red，不早退）：
#   1. 前置：docker version / docker info / docker compose version（原始输出 + 退出码）
#   2. docker build（镜像 ID / 体积 / 耗时）
#   3. docker compose config -q（编排文件的变量插值与语法）
#   4. docker compose up -d → 等 /health 200 且 status="ok"
#   5. scripts/verify_e2e.py --phase fresh（登录三角色 / webhook 写入 / 审计导出）
#   6. docker compose restart → 等 /health → --phase after-restart（卷持久化）
#   7. ./logs 里真有入口包装写下的日志文件
#   8. docker run --network none（断网镜像仍可运行）
#   9. docker save 出离线 tar + SHA256SUMS + IMAGE-INFO.json
# 产物全部落在 release/<版本>/，控制台与 release/<版本>/docker-verify.log 内容一致。
#
# 退出码：0 = 全绿；1 = 有红项（红项清单在最末尾）。
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

case "$(uname -s 2>/dev/null || echo unknown)" in
    MINGW*|MSYS*|CYGWIN*) export MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL='*' ;;
esac

PYTHON="${PYTHON:-python}"
command -v python >/dev/null 2>&1 || PYTHON="python3"

KEEP_DATA=0
DO_OFFLINE=1
while [ "$#" -gt 0 ]; do
    case "$1" in
        --keep-data) KEEP_DATA=1; shift ;;
        --no-offline) DO_OFFLINE=0; shift ;;
        -h|--help) sed -n '2,24p' "$0"; exit 0 ;;
        *) echo "未知参数：$1" >&2; exit 2 ;;
    esac
done

VERSION="${TRACELENS_VERSION:-$("$PYTHON" -c "import sys; sys.path.insert(0, '.'); from agent_explainer import server; print(server.SERVER_VERSION)")}"
IMAGE="tracelens:${VERSION}"
DIST="release/${VERSION}"
PORT="${TRACELENS_PORT:-8765}"
NAME="tracelens"
# 验收用口令：只活在这台机器的这次运行里，不写进任何交付文件
export TRACELENS_ADMIN_PASSWORD="${TRACELENS_ADMIN_PASSWORD:-Docker-Verify-Pass-2026}"
export TRACELENS_PORT="$PORT"
export TRACELENS_VERSION="$VERSION"

mkdir -p "$DIST"
LOG="${DIST}/docker-verify.log"
: > "$LOG"
exec > >(tee -a "$LOG") 2>&1

RESULTS=()
failures=0
pass() { RESULTS+=("PASS|$1|$2"); echo "[PASS] $1 —— $2"; }
fail() { RESULTS+=("FAIL|$1|$2"); failures=$((failures + 1)); echo "[FAIL] $1 —— $2"; }

wait_health() {
    local tries="${1:-180}" i
    for i in $(seq 1 "$tries"); do
        if "$PYTHON" - "$PORT" <<'PY' >/dev/null 2>&1
import json, sys, urllib.request
url = "http://127.0.0.1:%s/health" % sys.argv[1]
with urllib.request.urlopen(url, timeout=3) as r:
    sys.exit(0 if json.load(r).get("status") == "ok" else 1)
PY
        then return 0; fi
        sleep 0.5
    done
    return 1
}

echo "########################################################################"
echo "# TraceLens Docker 交付验收（t17）"
echo "# 时间     : $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "# 版本     : ${VERSION}   镜像：${IMAGE}   宿主端口：${PORT}"
echo "# Python   : $($PYTHON -V 2>&1)"
echo "# 平台     : $(uname -srm 2>/dev/null || echo unknown)"
echo "########################################################################"

# ---------------------------------------------------------------------------
echo
echo "== 1. 前置：Docker 真的可用吗（docker info 是第一道闸门）=="
for cmd in "docker version" "docker compose version"; do
    out="$($cmd 2>&1)"; rc=$?
    echo "  \$ $cmd   → exit=$rc"
    echo "$out" | sed -n '1,6p' | sed 's/^/      /'
    if [ $rc -eq 0 ]; then pass "前置 ${cmd}" "exit=0"; else fail "前置 ${cmd}" "exit=$rc"; fi
done
info_out="$(docker info 2>&1)"; info_rc=$?
printf '%s\n' "$info_out" > "${DIST}/docker-info.txt"
summary="$(printf '%s\n' "$info_out" | grep -E 'Server Version|Operating System|OSType|Architecture|Storage Driver|Cgroup|Docker Root Dir' | sed 's/^ */    /' | head -12)"
echo "  \$ docker info        → exit=$info_rc"
printf '%s\n' "$summary"
if [ $info_rc -eq 0 ]; then
    pass "前置 docker info" "Server $(printf '%s\n' "$info_out" | grep -m1 'Server Version' | sed 's/.*: //')"
    echo "  完整输出：${DIST}/docker-info.txt"
else
    fail "前置 docker info" "exit=$info_rc（Docker Desktop 没起来？先解决这个，别往下走）"
    echo "!!! docker info 失败 → 后续步骤不再执行"
    for row in "${RESULTS[@]}"; do echo "  $row"; done
    exit 1
fi

# ---------------------------------------------------------------------------
echo
echo "== 2. docker build + docker save（一次构建、一次导出，镜像 ID 与离线包对得上）=="
if ./scripts/docker-build.sh --save; then
    image_id="$(docker image inspect "$IMAGE" --format '{{.Id}}')"
    image_size="$(docker image inspect "$IMAGE" --format '{{.Size}}')"
    pass "docker build" "id=${image_id:0:19}… size=${image_size}B"
else
    fail "docker build" "见上方日志"
    echo "!!! 构建失败 → 后续步骤不再执行；复现：./scripts/docker-build.sh"
    for row in "${RESULTS[@]}"; do echo "  $row"; done
    exit 1
fi

# ---------------------------------------------------------------------------
echo
echo "== 3. docker compose config -q（变量插值 + 语法）=="
if docker compose config -q 2>/dev/null; then
    pass "compose config" "插值与语法 OK（-q 不打印明文口令）"
else
    fail "compose config" "$(docker compose config -q 2>&1 | tail -3)"
fi
if env -u TRACELENS_ADMIN_PASSWORD docker compose config -q >/dev/null 2>&1; then
    fail "compose 缺口令守卫" "没给 TRACELENS_ADMIN_PASSWORD 竟然也能通过校验（不该发生）"
else
    pass "compose 缺口令守卫" "缺口令时 compose 直接报错停下（不让管理员口令只出现在滚动日志里）"
fi

# ---------------------------------------------------------------------------
echo
echo "== 4. docker compose up -d（bind mount ./data ./logs）=="
docker compose down --remove-orphans >/dev/null 2>&1 || true
if [ "$KEEP_DATA" = "0" ]; then
    stamp="$(date +%Y%m%d-%H%M%S)"
    mkdir -p "${DIST}/backup"
    [ -d data ] && mv data "${DIST}/backup/data-${stamp}" && echo "  旧 ./data → ${DIST}/backup/data-${stamp}"
    [ -d logs ] && mv logs "${DIST}/backup/logs-${stamp}" && echo "  旧 ./logs → ${DIST}/backup/logs-${stamp}"
fi
mkdir -p data logs
up_out="$(docker compose up -d 2>&1)"; up_rc=$?
echo "$up_out" | sed 's/^/      /'
if [ $up_rc -ne 0 ]; then
    fail "compose up" "exit=$up_rc"
    docker compose logs --tail 30 2>&1 | sed 's/^/      /'
else
    pass "compose up" "容器已起"
fi
if wait_health 240; then
    pass "GET /health（容器就绪）" "200 + status=ok"
    docker inspect "$NAME" --format '  容器状态：{{.State.Status}} health={{if .State.Health}}{{.State.Health.Status}}{{else}}n/a{{end}}  用户：{{.Config.User}}' 2>/dev/null
else
    fail "GET /health（容器就绪）" "240s 内没等到 200+status=ok"
    docker compose logs --tail 40 2>&1 | sed 's/^/      /'
fi
health_state="unknown"
for _ in $(seq 1 120); do
    health_state="$(docker inspect "$NAME" --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' 2>/dev/null || echo unknown)"
    [ "$health_state" = "healthy" ] && break
    sleep 1
done
if [ "$health_state" = "healthy" ]; then
    pass "Docker HEALTHCHECK" "docker inspect 报 healthy（/health 200 + status=ok 才算）"
    docker inspect "$NAME" --format '{{range .State.Health.Log}}{{.Output}}
{{end}}' 2>/dev/null | grep -v '^$' | tail -1 | sed 's/^/      /'
else
    fail "Docker HEALTHCHECK" "120s 内没变成 healthy（当前：${health_state}）"
fi

# ---------------------------------------------------------------------------
echo
echo "== 5. 首启验收：三角色登录 / webhook 写入 / 审计导出（verify_e2e phase=fresh）=="
if "$PYTHON" scripts/verify_e2e.py --base-url "http://127.0.0.1:${PORT}" \
        --password "$TRACELENS_ADMIN_PASSWORD" --phase fresh \
        --state "${DIST}/verify-state.json" --json-out "${DIST}/verify-fresh.json"; then
    pass "verify_e2e fresh" "详见 ${DIST}/verify-fresh.json"
else
    fail "verify_e2e fresh" "见上方 [FAIL] 行"
fi

# ---------------------------------------------------------------------------
echo
echo "== 6. 重启容器：数据还在吗（卷持久化）=="
before="$(ls -l data/records.jsonl 2>/dev/null | awk '{print $5}')"
docker compose restart >/dev/null 2>&1; restart_rc=$?
if [ $restart_rc -eq 0 ] && wait_health 240; then
    after="$(ls -l data/records.jsonl 2>/dev/null | awk '{print $5}')"
    pass "compose restart" "重启后 /health 仍 200（records.jsonl ${before}B → ${after}B）"
else
    fail "compose restart" "exit=$restart_rc 或 /health 未就绪"
fi
if "$PYTHON" scripts/verify_e2e.py --base-url "http://127.0.0.1:${PORT}" \
        --password "$TRACELENS_ADMIN_PASSWORD" --phase after-restart \
        --state "${DIST}/verify-state.json" --json-out "${DIST}/verify-restart.json"; then
    pass "verify_e2e after-restart" "记录数/用户表/审计链都活过重启"
else
    fail "verify_e2e after-restart" "见上方 [FAIL] 行"
fi

# ---------------------------------------------------------------------------
echo
echo "== 7. ./logs 里真有日志文件吗（容器入口包装的 tee）=="
log_file="$(ls -1 logs/tracelens-*.log 2>/dev/null | head -1)"
if [ -n "$log_file" ] && [ -s "$log_file" ]; then
    pass "./logs 落盘" "${log_file}（$(wc -c < "$log_file" | tr -d ' ') 字节，$(wc -l < "$log_file" | tr -d ' ') 行）"
    head -3 "$log_file" | sed 's/^/      /'
else
    fail "./logs 落盘" "没找到 logs/tracelens-*.log（入口包装没生效？）"
fi

# ---------------------------------------------------------------------------
if [ "$DO_OFFLINE" = "1" ]; then
    echo
    echo "== 8. 断网可运行：docker run --network none =="
    docker rm -f t17-offline >/dev/null 2>&1 || true
    docker run -d --name t17-offline --network none \
        -e TRACELENS_ADMIN_PASSWORD=Offline-Probe-Pass-2026 \
        -e TRACELENS_DEMO=zh \
        --entrypoint /usr/local/bin/tracelens-entrypoint \
        "$IMAGE" python -m agent_explainer --mode team --host 0.0.0.0 --port 8765 \
        --demo zh --data-dir /app/var >/dev/null 2>&1
    offline_ok=1
    for _ in $(seq 1 120); do
        if docker exec t17-offline python /usr/local/bin/tracelens-healthcheck.py >/tmp/t17-offline.out 2>&1; then
            offline_ok=0; break
        fi
        sleep 0.5
    done
    if [ "$offline_ok" = "0" ]; then
        pass "断网运行" "$(cat /tmp/t17-offline.out)"
        docker exec t17-offline python -c "
import json, urllib.request
d = json.load(urllib.request.urlopen('http://127.0.0.1:8765/health', timeout=5))
print('      records=%s mode=%s ingest=%s' % (d['stats']['count'], d['mode'], d['ingest']['enabled']))
"
    else
        fail "断网运行" "健康检查没通过；docker logs t17-offline 看原因"
        docker logs t17-offline 2>&1 | tail -20 | sed 's/^/      /'
    fi
    docker rm -f t17-offline >/dev/null 2>&1 || true
    rm -f /tmp/t17-offline.out
fi

# ---------------------------------------------------------------------------
echo
echo "== 9. 离线包校验：体积 / SHA256 复算 / docker load =="
tar_path="${DIST}/tracelens-${VERSION}-image.tar"
if [ -f "$tar_path" ]; then
    bytes="$(wc -c < "$tar_path" | tr -d ' ')"
    recorded="$(awk '{print $1}' "${DIST}/SHA256SUMS.txt" 2>/dev/null)"
    actual="$("$PYTHON" -c "import hashlib,sys;print(hashlib.sha256(open(sys.argv[1],'rb').read()).hexdigest())" "$tar_path")"
    if [ -n "$recorded" ] && [ "$recorded" = "$actual" ]; then
        pass "离线包 sha256 复算一致" "${actual:0:12}…（${bytes}B）"
    else
        fail "离线包 sha256 复算一致" "SHA256SUMS=$recorded 复算=$actual"
    fi
    "$PYTHON" - "$DIST" "$VERSION" "$IMAGE" "$(docker image inspect "$IMAGE" --format '{{.Id}}')" \
        "$(docker image inspect "$IMAGE" --format '{{.Size}}')" "$bytes" "$actual" <<'PY'
import json, sys, pathlib
dist, version, image, image_id, image_size, tar_bytes, digest = sys.argv[1:8]
doc = {
    "version": version, "image": image, "image_id": image_id,
    "image_size_bytes": int(image_size), "tar": f"tracelens-{version}-image.tar",
    "tar_bytes": int(tar_bytes), "tar_sha256": digest,
    "base_image": "python:3.12-slim", "user": "10001:10001", "expose": 8765,
    "entrypoint": ["python", "-m", "agent_explainer", "--mode", "team",
                    "--host", "0.0.0.0", "--port", "8765"],
    "healthcheck": "GET /health 200 + status=ok (scripts/healthcheck.py)",
    "note": "离线机器：docker load -i <tar>；compose 的 volumes 挂 ./data ./logs 即可续用数据",
}
path = pathlib.Path(dist) / "IMAGE-INFO.json"
path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print("      " + str(path))
PY
    load_out="$(docker load -i "$tar_path" 2>&1)"; load_rc=$?
    if [ $load_rc -eq 0 ]; then
        pass "docker load（离线包可用）" "$(echo "$load_out" | tail -1)"
    else
        fail "docker load（离线包可用）" "$(echo "$load_out" | tail -2)"
    fi
else
    fail "离线包存在" "没找到 ${tar_path}"
fi

# ---------------------------------------------------------------------------
echo
echo "== 10. 收尾：停掉验收容器（数据留在 ./data，便于人工复看）=="
docker compose down --remove-orphans >/dev/null 2>&1 && echo "  已 docker compose down（./data ./logs 保留）"

echo
echo "########################################################################"
echo "# 验收小结（$VERSION）"
printf '# %-52s %s\n' "检查项" "结果"
for row in "${RESULTS[@]}"; do
    IFS='|' read -r status name detail <<<"$row"
    printf '# [%s] %-48s %s\n' "$status" "$name" "$detail"
done
echo "# 日志：${LOG}"
echo "# 镜像信息：${DIST}/IMAGE-INFO.json"
echo "########################################################################"
if [ "$failures" -ne 0 ]; then
    echo "结论：失败 ${failures} 项 —— 以上面 [FAIL] 行为准，不宣称交付"
    exit 1
fi
echo "结论：全部通过"
exit 0
