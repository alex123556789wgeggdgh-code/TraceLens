#!/bin/bash
# TraceLens 镜像构建（子任务 5.4 / t17）—— 真的调 docker build，不做任何静态检查。
#
#   ./scripts/docker-build.sh                  # 构建 tracelens:<version> + tracelens:latest
#   ./scripts/docker-build.sh --save           # 构建后再 docker save 出离线 tar + SHA256SUMS
#   TRACELENS_WITH_PDF=0 ./scripts/docker-build.sh   # 不装 reportlab（导出自动回退 HTML）
#   TRACELENS_VERSION=0.4.5 ./scripts/docker-build.sh # 覆盖版本号（默认取自代码）
#
# 退出码：0 = 构建成功（--save 时还包括 tar + 校验和）；非 0 = 构建/导出失败。
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

# Git Bash / MSYS2 会把 /app/var 这种容器内路径改写成 C:/...，必须关掉路径转换
case "$(uname -s 2>/dev/null || echo unknown)" in
    MINGW*|MSYS*|CYGWIN*) export MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL='*' ;;
esac

PYTHON="${PYTHON:-}"
if [ -z "$PYTHON" ]; then
    if command -v python >/dev/null 2>&1; then PYTHON=python
    elif command -v python3 >/dev/null 2>&1; then PYTHON=python3
    else echo "找不到 python（用于读版本号）；可用 PYTHON=/path/to/python 指定" >&2; exit 2; fi
fi

SAVE=0
for arg in "$@"; do
    case "$arg" in
        --save) SAVE=1 ;;
        -h|--help) sed -n '2,14p' "$0"; exit 0 ;;
        *) echo "未知参数：$arg（只支持 --save）" >&2; exit 2 ;;
    esac
done

# 版本号唯一来源 = 代码里 /health 上报的那个：agent_explainer.server.SERVER_VERSION
VERSION="${TRACELENS_VERSION:-$("$PYTHON" -c "import sys; sys.path.insert(0, '.'); from agent_explainer import server; print(server.SERVER_VERSION)")}"
WITH_PDF="${TRACELENS_WITH_PDF:-1}"
IMAGE="tracelens:${VERSION}"
RELEASE_DIR="release/${VERSION}"

echo "== TraceLens 镜像构建 =="
echo "  版本            = ${VERSION}"
echo "  标签            = ${IMAGE}, tracelens:latest"
echo "  reportlab (PDF) = ${WITH_PDF}（1=装，0=不装）"
echo "  上下文          = $(pwd)"
echo

started="$(date +%s)"
docker build \
    --build-arg "TRACELENS_VERSION=${VERSION}" \
    --build-arg "TRACELENS_WITH_PDF=${WITH_PDF}" \
    --label "tracelens.build.by=docker-build.sh" \
    -t "$IMAGE" -t tracelens:latest .
elapsed=$(( $(date +%s) - started ))

image_id="$(docker image inspect "$IMAGE" --format '{{.Id}}')"
image_size="$(docker image inspect "$IMAGE" --format '{{.Size}}')"
echo
echo "== 构建成功 =="
echo "  镜像 ID   = ${image_id}"
echo "  镜像体积  = ${image_size} 字节（$(awk -v b="$image_size" 'BEGIN{printf "%.1f MiB", b/1048576}')）"
echo "  耗时      = ${elapsed}s"
docker image inspect "$IMAGE" --format '  Created   = {{.Created}}{{"\n"}}  Entrypoint= {{json .Config.Entrypoint}}{{"\n"}}  User      = {{.Config.User}}'

if [ "$SAVE" = "1" ]; then
    mkdir -p "$RELEASE_DIR"
    tar_path="${RELEASE_DIR}/tracelens-${VERSION}-image.tar"
    echo
    echo "== docker save 离线镜像 =="
    echo "  落点 = ${tar_path}"
    docker save -o "$tar_path" "$IMAGE"
    bytes="$(wc -c < "$tar_path" | tr -d ' ')"
    if command -v sha256sum >/dev/null 2>&1; then
        digest="$(sha256sum "$tar_path" | awk '{print $1}')"
    else
        digest="$("$PYTHON" -c "import hashlib,sys;print(hashlib.sha256(open(sys.argv[1],'rb').read()).hexdigest())" "$tar_path")"
    fi
    printf '%s  %s\n' "$digest" "$(basename "$tar_path")" > "${RELEASE_DIR}/SHA256SUMS.txt"
    echo "  体积   = ${bytes} 字节（$(awk -v b="$bytes" 'BEGIN{printf "%.1f MiB", b/1048576}')）"
    echo "  sha256 = ${digest}"
    echo "  校验文件 = ${RELEASE_DIR}/SHA256SUMS.txt"
    echo "  离线机器上：docker load -i $(basename "$tar_path")   # 再 docker run 即可"
fi
