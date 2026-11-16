"""多媒体证据与因果解释（规格书功能三，docs/07 §3.2 / §4.7 冻结契约）。

为什么证据要独立成模块：证据文件的 sha256 会进入记录的签名字段范围
（``evidence_hash`` = 各证据 sha256 的规范归约，是 ``entry_hash`` 的一部分），
所以"收了什么文件、哈希是什么、谁给的"必须有一处唯一事实来源——
``evidence/index.jsonl``（Store）+ ``evidence/files/<id>.<ext>``（本体）。
记录本体只放引用（``multimedia_evidence`` 字段，uri 指回本模块的端点），
不复制文件内容：记录会被签名，签名对象里塞二进制是灾难。

三条纪律（与其它扩展模块一致）：

* **纯标准库**，离线可跑，不 import 任何第三方渲染库；
* 错误码是契约（docs/07 §7）：``EVIDENCE_KIND_INVALID`` / ``EVIDENCE_PAYLOAD_INVALID``
  / ``EVIDENCE_TOO_LARGE`` / ``EVIDENCE_NOT_FOUND`` / ``EVIDENCE_VOTE_INVALID``
  / ``EVIDENCE_WRITE_FAILED``，HTTP 层只做翻译不重命名；
* 写失败如实报错（``EVIDENCE_WRITE_FAILED``），绝不"返回成功但文件没落地"。

魔数校验的为什么：``payload`` 里那张"截图"可能根本不是 PNG。把魔数不对的
字节落盘再让浏览器渲染，等于替攻击者绕过内容嗅探；这里直接拒收。
"""

from __future__ import annotations

import base64
import binascii
import difflib
import hashlib
import pathlib
import shutil
import sys
import threading

from .feature_store import FeaturePaths, FeatureStore, new_id, open_store, utc_stamp

__all__ = [
    "EVIDENCE_VERSION", "KINDS", "MAX_BYTES", "VOTES", "MEDIA_TYPES",
    "EvidenceError", "EvidenceService", "default_service",
]

EVIDENCE_VERSION = "1.0"
KINDS: tuple[str, ...] = ("screenshot", "diagram", "diff")
#: 单个证据文件的体积上限（docs/07 §4.7）：证据是给人看的，不是给仓库当网盘的。
MAX_BYTES = 4 * 1024 * 1024
VOTES: tuple[str, ...] = ("like", "dislike")
#: 每种 kind 的（媒体类型, 扩展名）——T15 回字节流时的 Content-Type 就从这里来。
MEDIA_TYPES: dict[str, tuple[str, str]] = {
    "screenshot": ("image/png", "png"),      # jpeg 入参会被转存为 .png 名义下的原字节，
    "diagram": ("image/svg+xml", "svg"),     # 媒体类型按真实魔数回（见 _store_payload）
    "diff": ("text/x-diff", "diff"),
}

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_JPEG_MAGIC = b"\xff\xd8\xff"
_SVG_MAGIC = b"<svg"
_SVG_MAGIC_LCASE = b"<?xml"


class EvidenceError(Exception):
    """证据域异常：``code`` 对齐 docs/07 §7 的错误码表，HTTP 层照表翻译。"""

    def __init__(self, code: str, reason: str, *, field: str | None = None,
                 hint: str | None = None, detail: dict | None = None) -> None:
        super().__init__(reason)
        self.code = code
        self.reason = reason
        self.field = field
        self.hint = hint
        self.detail = dict(detail or {})

    def as_dict(self) -> dict:
        out = {"code": self.code, "message": self.reason}
        if self.field:
            out["field"] = self.field
        if self.detail:
            out["detail"] = self.detail
        if self.hint:
            out["hint"] = self.hint
        return out

    def render(self) -> str:
        parts = [f"{self.code}: {self.reason}"]
        if self.field:
            parts.append(f"field={self.field}")
        if self.hint:
            parts.append(f"hint: {self.hint}")
        return "；".join(parts)


def _now() -> str:
    return utc_stamp()


class EvidenceService:
    """证据索引 + 文件本体的唯一写入者（server 只做薄翻译）。

    线程安全：HTTP 服务是多线程的，索引读写持 ``RLock``；文件写入用
    ``.<id>.<ext>.tmp`` + ``os.replace`` 原子换名，半截文件不会出现在索引里。
    """

    def __init__(self, paths: FeaturePaths, *, now=None) -> None:
        if not isinstance(paths, FeaturePaths):
            raise TypeError("EvidenceService 需要 FeaturePaths（见 feature_store.py）")
        self.paths = paths
        self._now = now or _now
        self._lock = threading.RLock()
        self._index: FeatureStore = open_store(paths.evidence_index)

    # -- 内部 ------------------------------------------------------------- #
    def _public(self, entry: dict) -> dict:
        """对外视图：索引里的内部键（本模块不需要进 API 的）不发出去。"""
        view = {k: v for k, v in entry.items() if not k.startswith("_")}
        view["uri"] = f"/api/evidence/{entry['id']}/file"
        return view

    def _entry(self, evidence_id: str) -> dict:
        found = self._index.get(evidence_id)
        if not isinstance(found, dict):
            raise EvidenceError("EVIDENCE_NOT_FOUND", f"证据 {evidence_id} 不存在",
                                detail={"evidence_id": evidence_id})
        return dict(found)

    def _store_payload(self, evidence_id: str, kind: str, media_type: str,
                       data: bytes) -> tuple[int, str]:
        """落盘 + 返回 (bytes, sha256)；写失败如实抛 EVIDENCE_WRITE_FAILED。

        写盘前先补一次幂等 ``mkdir(parents=True, exist_ok=True)``（R3-2 前置微修，
        PM-VERDICT-R3-1 §3.3 遗留 3）：``FeaturePaths.ensure()`` 是"用到才建目录"的懒口径，
        而这里过去只做 ``tmp`` + 原子换名、自己不建目录 —— 全新数据根上第一次 T13
        主动上传就撞上 ``evidence/files`` 不存在，抛的是 ``EVIDENCE_WRITE_FAILED``
        （"写盘失败"这个码当时把"目录没建"这件事盖住了，排障时看不到根因）。
        **建目录的口径仍归** ``FeaturePaths``（这里只调它的 ``evidence_files_dir``），
        **写盘的口径仍归本函数**：不新增机制，只把缺口补上。
        """
        if len(data) > MAX_BYTES:
            raise EvidenceError("EVIDENCE_TOO_LARGE",
                                f"证据超过上限（{len(data)} > {MAX_BYTES} 字节）",
                                detail={"bytes": len(data), "max_bytes": MAX_BYTES})
        ext = MEDIA_TYPES[kind][1]
        target = self.paths.evidence_file(evidence_id, ext)
        self.paths.evidence_files_dir.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(target.suffix + ".tmp")
        try:
            tmp.write_bytes(data)
            tmp.replace(target)
        except OSError as exc:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            raise EvidenceError("EVIDENCE_WRITE_FAILED",
                                "证据文件写盘失败", detail={"error": str(exc)}) from exc
        return len(data), hashlib.sha256(data).hexdigest()

    @staticmethod
    def _decode_screenshot(payload: dict) -> tuple[bytes, str]:
        """screenshot 入参 → (原始字节, 真实 media_type)；魔数不对就拒收。"""
        raw: bytes | None = None
        if isinstance(payload.get("data_uri"), str):
            head, _, body = payload["data_uri"].partition(",")
            if not body or not head.startswith("data:"):
                raise EvidenceError("EVIDENCE_PAYLOAD_INVALID",
                                    "data_uri 必须是 data:<mime>;base64,<正文>",
                                    field="payload.data_uri")
            raw = _b64(body)
        elif isinstance(payload.get("base64"), str):
            raw = _b64(payload["base64"])
        elif isinstance(payload.get("png"), (bytes, bytearray)):
            raw = bytes(payload["png"])
        elif isinstance(payload.get("jpeg"), (bytes, bytearray)):
            raw = bytes(payload["jpeg"])
        else:
            raise EvidenceError("EVIDENCE_PAYLOAD_INVALID",
                                "screenshot 需要 data_uri / base64 / png / jpeg 之一",
                                field="payload",
                                hint="前置校验失败：没有可解码的图像字段")
        if raw.startswith(_PNG_MAGIC):
            return raw, "image/png"
        if raw.startswith(_JPEG_MAGIC):
            return raw, "image/jpeg"
        raise EvidenceError("EVIDENCE_PAYLOAD_INVALID",
                            "截图字节不是 PNG/JPEG（魔数校验失败）", field="payload",
                            hint="只收真 PNG（\\x89PNG）与 JPEG（\\xff\\xd8\\xff），不嗅探不猜测")

    @staticmethod
    def _diagram_svg(title: str, nodes: list, edges: list) -> bytes:
        """nodes/edges → 一份极简 SVG（纯手工拼，不引渲染库）。

        形状保守：矩形节点 + 直线边 + 序号文本。图的用途是"人看得懂当时发生了
        什么"，不是画布产品；更大的诉求应该走 screenshot 证据。
        """
        width, height = 800, max(240, 96 + 56 * max(1, len(nodes)))
        parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" '
                 f'height="{height}" viewBox="0 0 {width} {height}">',
                 f"<title>{_esc(title or 'diagram')}</title>",
                 f"<rect width=\"100%\" height=\"100%\" fill=\"white\"/>"]
        coords: dict[str, tuple[int, int]] = {}
        for idx, node in enumerate(nodes):
            label = _esc(str(node if isinstance(node, str)
                             else (node or {}).get("label") or (node or {}).get("id") or "?"))
            x, y = 60 + (idx % 2) * 420, 72 + (idx // 2) * 96
            coords[str((node or {}).get("id") if isinstance(node, dict) else node) or str(idx)] = (x, y)
            parts.append(f'<g><rect x="{x}" y="{y}" width="240" height="56" rx="8" '
                         f'fill="#f4f6f9" stroke="#5b6472"/>'
                         f'<text x="{x + 12}" y="{y + 34}" font-size="14" '
                         f'font-family="sans-serif">{label}</text></g>')
        for edge in edges:
            src = str((edge or {}).get("from") or "") if isinstance(edge, dict) else str(edge or "")
            dst = str((edge or {}).get("to") or "") if isinstance(edge, dict) else ""
            if src in coords and dst in coords:
                x1, y1 = coords[src][0] + 240, coords[src][1] + 28
                x2, y2 = coords[dst]
                parts.append(f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" '
                             f'stroke="#5b6472" marker-end="none"/>')
        parts.append("</svg>")
        return "\n".join(parts).encode("utf-8")

    # -- 附加证据（T13） --------------------------------------------------- #
    def add(self, record_id: str, *, kind: str, payload,
            caption: str = "", provided_by: str = "runtime") -> dict:
        if kind not in KINDS:
            raise EvidenceError("EVIDENCE_KIND_INVALID",
                                f"kind 必须是 {'/'.join(KINDS)} 之一",
                                field="kind", detail={"received": kind, "allowed": list(KINDS)})
        if not isinstance(record_id, str) or not record_id.strip():
            raise EvidenceError("EVIDENCE_PAYLOAD_INVALID", "缺少 record_id",
                                field="record_id")
        if provided_by not in ("runtime", "user", "server"):
            raise EvidenceError("EVIDENCE_PAYLOAD_INVALID",
                                "provided_by 只能是 runtime/user/server",
                                field="provided_by", detail={"received": provided_by})
        if kind == "screenshot":
            if not isinstance(payload, dict):
                raise EvidenceError("EVIDENCE_PAYLOAD_INVALID",
                                    "screenshot 的 payload 必须是对象", field="payload")
            data, media_type = self._decode_screenshot(payload)
        elif kind == "diagram":
            if not isinstance(payload, dict) \
                    or not isinstance(payload.get("nodes"), list) \
                    or not isinstance(payload.get("edges"), list):
                raise EvidenceError("EVIDENCE_PAYLOAD_INVALID",
                                    "diagram 的 payload 需要 {nodes:[…], edges:[…]}",
                                    field="payload")
            data = self._diagram_svg(str(payload.get("title") or ""),
                                     payload["nodes"], payload["edges"])
            media_type = MEDIA_TYPES["diagram"][0]
        else:  # diff
            if not isinstance(payload, dict) \
                    or not isinstance(payload.get("before"), str) \
                    or not isinstance(payload.get("after"), str):
                raise EvidenceError("EVIDENCE_PAYLOAD_INVALID",
                                    "diff 的 payload 需要 {before, after} 两个文本",
                                    field="payload")
            data = render_diff(payload["before"], payload["after"],
                               filename=str(payload.get("filename") or "before"))
            media_type = MEDIA_TYPES["diff"][0]
        with self._lock:
            entry = {
                # new_id 自带随机位：同一秒并发创建不撞号（撞号 = 后写覆盖先写，
                # 第一版用纯时间戳派生 id，自检当场抓到同秒第二条覆盖第一条）
                "id": new_id("evd"),
                "record_id": record_id.strip(),
                "kind": kind,
                "media_type": media_type,
                "caption": str(caption or "")[:300],
                "provided_by": provided_by,
                "created_at": self._now(),
                "feedback": {"like": 0, "dislike": 0},
            }
            size, sha = self._store_payload(entry["id"], kind, media_type, data)
            entry["bytes"] = size
            entry["sha256"] = sha
            try:
                self._index.put(entry)
            except Exception as exc:                                  # noqa: BLE001
                raise EvidenceError("EVIDENCE_WRITE_FAILED", "证据索引写盘失败",
                                    detail={"error": str(exc)}) from exc
            return self._public(entry)

    def add_diff(self, record_id: str, *, before: str, after: str,
                 filename: str = "", caption: str = "") -> dict:
        return self.add(record_id, kind="diff",
                        payload={"before": before, "after": after, "filename": filename},
                        caption=caption)

    def add_diagram(self, record_id: str, *, title: str = "", nodes: list,
                    edges: list, caption: str = "") -> dict:
        return self.add(record_id, kind="diagram",
                        payload={"title": title, "nodes": nodes, "edges": edges},
                        caption=caption)

    # -- 查询（T14 / T15） ------------------------------------------------- #
    def list(self, record_id: str) -> list[dict]:
        with self._lock:
            rows = [self._public(dict(item)) for item in self._index.items()
                    if item.get("record_id") == record_id]
        rows.sort(key=lambda item: str(item.get("created_at") or ""))
        return rows

    def get(self, evidence_id: str) -> dict | None:
        with self._lock:
            found = self._index.get(evidence_id)
        return self._public(dict(found)) if isinstance(found, dict) else None

    def file_path(self, evidence_id: str) -> pathlib.Path | None:
        with self._lock:
            found = self._index.get(evidence_id)
        if not isinstance(found, dict):
            return None
        ext = MEDIA_TYPES.get(str(found.get("kind")), ("", "bin"))[1]
        target = self.paths.evidence_file(evidence_id, ext)
        return target if target.exists() else None

    def relative_path(self, evidence_id: str) -> str:
        """证据文件**相对数据根**的路径（``evidence/files/<id>.<ext>``，POSIX 分隔）。

        为什么单独给一个口径（R3-1，docs/07 §11.18）：存储纪律是"记录里只放
        sha256 + 相对路径 + media_type"，而 `docs/schemas/action_record.schema.json`
        的 ``multimedia_evidence`` 条目是 ``additionalProperties: false`` 的封闭形状，
        里面**没有** ``relative_path`` 这个属性（schema 零改动是铁律）。
        于是"相对路径"由这里单点导出：记录里放 schema 认得的 ``uri``
        （schema 明文"uri 指回本模块的端点"），需要直接读盘的人调这个方法拿相对路径，
        两处指向同一个文件，不产生第二套定位符。索引里没有 → 空串（不猜路径）。
        """
        with self._lock:
            found = self._index.get(evidence_id)
        if not isinstance(found, dict):
            return ""
        ext = MEDIA_TYPES.get(str(found.get("kind")), ("", "bin"))[1]
        return f"evidence/files/{evidence_id}.{ext}"

    # -- 反馈（T16） ------------------------------------------------------- #
    def feedback(self, evidence_id: str, *, vote: str, actor: str = "local") -> dict:
        if vote not in VOTES:
            raise EvidenceError("EVIDENCE_VOTE_INVALID",
                                f"vote 必须是 {'/'.join(VOTES)}",
                                field="vote", detail={"received": vote, "allowed": list(VOTES)})
        with self._lock:
            entry = self._entry(evidence_id)
            feedback = dict(entry.get("feedback") or {})
            feedback[vote] = int(feedback.get(vote) or 0) + 1
            entry["feedback"] = feedback
            self._index.put(entry)
            return self._public(entry)

    # -- 因果解释（T17 的计算核心；写回记录由 server 层做） ------------------ #
    def counterfactual(self, record: dict, candidates: list, *,
                       lang: str = "zh", llm=None) -> dict:
        """模板版因果解释：反事实 + 备选项。``llm`` 可注入真模型（0.6 只接钩子）。

        为什么先做模板版：解释必须能离线复算、可审计；模板文案每个字都来自
        输入记录，不存在"编造事实"。LLM 版本接上后 ``model`` 字段如实标 ``llm``。

        调用方有两个，本函数的入参语义一字未改（R3-2）：
        T17（``server.evidence_causal``）= 用户/执行端主动补解释，``candidates`` 由请求给；
        自动接线（``evidence_wiring.causal_explanation``）= 决策完成时把**记录自己**的
        未选候选与被拒参数当 ``candidates`` 传进来，``llm`` 永远传 ``None``。
        """
        if llm is not None:
            result = llm(record, candidates, lang=lang)
            if isinstance(result, dict) and result.get("counterfactual"):
                result.setdefault("model", "llm")
                result.setdefault("generated_at", self._now())
                result.setdefault("language", lang)
                result.setdefault("alternatives", [])
                return result
        action_type = str((record or {}).get("action_type") or "?")
        tool = str(((record or {}).get("tool_used") or {}).get("name") or "?")
        decision = str(((record or {}).get("user_intervention") or {}).get("decision") or "未干预")
        if lang == "en":
            counterfactual = (f"If a different option had been chosen for this {tool} "
                              f"{action_type}, the recorded outcome would differ; the "
                              f"on-record decision was \"{decision}\" (template, offline).")
            alternatives = [{"option": str((item or {}).get("option") or "?"),
                             "expected": str((item or {}).get("expected") or ""),
                             "why": str((item or {}).get("why") or "")}
                            for item in (candidates or [])]
        else:
            counterfactual = (f"如果当时对这条 {tool} {action_type} 选择不同选项，"
                              f"结果会随所选选项改变；记录在案的决定是「{decision}」"
                              "（模板生成，离线可复算）。")
            alternatives = [{"option": str((item or {}).get("option") or "?"),
                             "expected": str((item or {}).get("expected") or ""),
                             "why": str((item or {}).get("why") or "")}
                            for item in (candidates or [])]
        return {"counterfactual": counterfactual, "alternatives": alternatives,
                "model": "template", "generated_at": self._now(), "language": lang}

    # -- 签名范围 ----------------------------------------------------------- #
    def sign_range(self, record: dict) -> str:
        """``evidence_hash``：该记录全部证据 sha256 的规范归约（进 entry_hash）。

        归约口径：按 sha256 字典序排序后逐行 ``sha256`` 再编码 —— 与事实指纹
        （tools/facts_hash.py）同思路：顺序无关、缺证据 = 空串的哈希，不空转。
        """
        view = record.get("multimedia_evidence") \
            if isinstance(record, dict) else None
        hashes = sorted(str(item.get("sha256") or "")
                        for item in (view or []) if isinstance(item, dict))
        if not hashes:
            return "sha256:" + hashlib.sha256(b"").hexdigest()
        payload = "\n".join(hashes).encode("utf-8")
        return "sha256:" + hashlib.sha256(payload).hexdigest()

    # -- 自检 ---------------------------------------------------------------- #
    def facts(self) -> dict:
        with self._lock:
            return {"version": EVIDENCE_VERSION, "count": self._index.count(),
                    "kinds": list(KINDS), "max_bytes": MAX_BYTES,
                    "store": str(self.paths.evidence_index)}


def _b64(text: str) -> bytes:
    try:
        return base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise EvidenceError("EVIDENCE_PAYLOAD_INVALID", "base64 解码失败",
                            field="payload", detail={"error": str(exc)}) from exc


def _esc(text: str) -> str:
    return (str(text).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def render_diff(before: str, after: str, *, filename: str = "before") -> bytes:
    """统一 diff 文本（text/x-diff）。file header 用给定文件名，不猜路径。"""
    lines = list(difflib.unified_diff(before.splitlines(), after.splitlines(),
                                      fromfile=filename, tofile=f"{filename} (after)",
                                      lineterm=""))
    return ("\n".join(lines) + "\n").encode("utf-8")


def default_service(paths: FeaturePaths, *, now=None) -> EvidenceService:
    return EvidenceService(paths, now=now)


def _self_check() -> int:  # pragma: no cover - 人工自检
    """``python -m agent_explainer.evidence``：临时目录里跑一遍附加→查询→反馈。"""
    import sys
    import tempfile

    with tempfile.TemporaryDirectory() as temp:
        paths = FeaturePaths(temp)
        paths.ensure()
        service = EvidenceService(paths)
        png = _PNG_MAGIC + b"\x00" * 32
        shot = service.add("act-1", kind="screenshot", payload={"png": png},
                           caption="自检截图", provided_by="runtime")
        diff = service.add_diff("act-1", before="a\nb\n", after="a\nc\n", caption="自检 diff")
        got = service.get(shot["id"])
        assert got and got["sha256"] == shot["sha256"]
        assert service.file_path(shot["id"]).exists()
        assert len(service.list("act-1")) == 2
        voted = service.feedback(diff["id"], vote="like")
        assert voted["feedback"]["like"] == 1
        record = {"id": "act-1", "multimedia_evidence": [shot, diff]}
        reduction = service.sign_range(record)
        assert reduction.startswith("sha256:")
        causal = service.counterfactual({"action_type": "write",
                                         "tool_used": {"name": "fs.write"}},
                                        [{"option": "A", "expected": "文件被写", "why": "…"}])
        assert causal["model"] == "template" and causal["alternatives"]
        print(f"evidence self-check ok: screenshot={shot['id']} diff={diff['id']} "
              f"sign_range={reduction[:18]}… bytes={shot['bytes']}")
        shutil.rmtree(temp, ignore_errors=True)
        return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(_self_check())
