# syntax=docker/dockerfile:1
#
# TraceLens 团队模式镜像（子任务 5.4 / t17）
# ---------------------------------------------------------------------------
# 交付形态 D-3（docs/03-architecture.md 第 1 节）：一条命令起团队服务器，
# 同事用浏览器访问。口径与代码现状逐条对齐：
#   · 基础镜像 python:3.12-slim（架构文档第 9 节：Python 兼容 3.12+）；
#   · 多阶段构建：builder 只做「源码自检 + 字节码编译 + 可选 PDF 依赖」，
#     runtime 只装运行时要的东西，构建工具链一个字节都不进交付镜像；
#   · 非 root 运行（uid/gid 10001，无登录 shell、无 home 写权限）；
#   · HEALTHCHECK 打的是真实的 GET /health（产品**没有** /api/health，
#     也没有 ready 字段，见 docs/03 §6.6），要求 200 且 status=="ok"；
#   · EXPOSE 8765 + ENTRYPOINT 逐字等于任务规格：
#       python -m agent_explainer --mode team --host 0.0.0.0 --port 8765
#   · 数据全部落在 /app/var（记录日志 / 审计链 / 设置 / 用户表 / 令牌表同目录，
#     见 agent_explainer/__main__.py 的 --data-dir），日志目录 /app/logs
#     由 scripts/docker-entrypoint.sh 落成文件（容器默认入口不做 tee，保持规格原样）。
#
# 依赖现状（docs/03 第 9 节）：产品核心**纯标准库**，pip 装的东西只有可选的
# reportlab（审计导出 PDF 用；缺了会自动退回可打印 HTML，4.8 的降级路径）。
# 想得到"零第三方库"的最小镜像：--build-arg TRACELENS_WITH_PDF=0
# ---------------------------------------------------------------------------

# ===========================================================================
# 阶段 1：builder —— 只做校验与预处理，产物是源码目录树 + 字节码 + 可选依赖
# ===========================================================================
FROM python:3.12-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1

# 1 = 装上 reportlab（审计导出真出 PDF）；0 = 不装（导出自动回退可打印 HTML）
ARG TRACELENS_WITH_PDF=1

WORKDIR /build

# 只拷运行时真正需要的两棵树：包本体（含 web/ 静态站与 prompts/）与演示夹具
COPY agent_explainer/ ./agent_explainer/
COPY samples/ ./samples/

# 构建期闸门：语法错/导入错会让 docker build 当场失败，而不是等启动才炸。
# compileall 同时产出 __pycache__，runtime 阶段直接用（启动少解析一遍源码）。
RUN python -m compileall -q agent_explainer \
 && python -c "import agent_explainer, agent_explainer.server as s; \
print('package', agent_explainer.__version__, 'server', s.SERVER_VERSION)"

# 可选 PDF 依赖装进独立前缀，runtime 只 COPY 这一层
RUN if [ "$TRACELENS_WITH_PDF" = "1" ]; then \
        pip install --target=/opt/deps "reportlab>=4,<5" \
     && python -c "import sys; sys.path.insert(0, '/opt/deps'); \
import reportlab; print('reportlab', reportlab.Version)"; \
    else \
        mkdir -p /opt/deps && echo "skip reportlab (TRACELENS_WITH_PDF=0)"; \
    fi

# ===========================================================================
# 阶段 2：runtime —— 交付镜像
# ===========================================================================
FROM python:3.12-slim AS runtime

ARG TRACELENS_VERSION=0.5.0
ARG TRACELENS_WITH_PDF=1

LABEL org.opencontainers.image.title="TraceLens" \
      org.opencontainers.image.description="Agent 行动透镜 · 团队模式（多用户 + 审计链 + 实时接入）" \
      org.opencontainers.image.version="${TRACELENS_VERSION}" \
      org.opencontainers.image.licenses="Apache-2.0" \
      tracelens.mode="team" \
      tracelens.pdf-export="${TRACELENS_WITH_PDF}"

# PYTHONDONTWRITEBYTECODE：/app 对运行用户只读也能跑（字节码在构建期已编好）
# PYTHONUNBUFFERED：容器日志按行刷出，docker logs 能看到首启 banner 与管理员口令
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/opt/deps \
    TRACELENS_VERSION="${TRACELENS_VERSION}" \
    TRACELENS_DATA_DIR=/app/var \
    TRACELENS_LOG_DIR=/app/logs \
    TRACELENS_HEALTH_PORT=8765

# 非 root：uid/gid 固定 10001（挂载卷时宿主要给这个 uid 写权限），无登录 shell
RUN groupadd --gid 10001 tracelens \
 && useradd --uid 10001 --gid tracelens --no-create-home \
            --home-dir /app --shell /usr/sbin/nologin tracelens

WORKDIR /app

COPY --from=builder /build/agent_explainer ./agent_explainer
COPY --from=builder /build/samples ./samples
COPY --from=builder /opt/deps /opt/deps

# 容器入口包装（可写性自检 + 把 stdout/stderr 同时落到 /app/logs）与健康检查脚本。
# 镜像默认 ENTRYPOINT 仍是规格里那条 python 命令；compose / 脚本用 --entrypoint
# 换成这个包装，属于运维增强，不改变"应用怎么被启动"。
COPY scripts/docker-entrypoint.sh /usr/local/bin/tracelens-entrypoint
COPY scripts/healthcheck.py /usr/local/bin/tracelens-healthcheck.py
RUN chmod 0755 /usr/local/bin/tracelens-entrypoint /usr/local/bin/tracelens-healthcheck.py \
 && mkdir -p /app/var /app/logs \
 && chown -R tracelens:tracelens /app \
 && chmod 0750 /app/var /app/logs

EXPOSE 8765

USER 10001:10001

# GET /health 200 且 status=="ok" 才算健康；连不上/字段不符都判 unhealthy。
# 打的是回环地址，容器内自检，不经宿主机端口映射。
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD ["python", "/usr/local/bin/tracelens-healthcheck.py"]

ENTRYPOINT ["python", "-m", "agent_explainer", "--mode", "team", "--host", "0.0.0.0", "--port", "8765"]
