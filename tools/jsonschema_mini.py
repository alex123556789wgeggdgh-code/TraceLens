r"""jsonschema_mini —— 纯标准库的 JSON Schema Draft 2020-12 子集校验器。

为什么要有这个文件（不是重复造轮子）：
  TraceLens 的核心运行时的依赖政策是『纯标准库』，且必须能在离线机器上跑
  （t1 的 K-12：无网络 / 无 API key 也要全功能可用）。
  因此 schema 的约束必须**只靠标准库就能生效**，不能依赖 pip 包。
  本文件实现了本项目 schema 用到的全部关键字，并在 validate_schema.py 中
  与第三方 jsonschema 库交叉比对，证明两者判定一致。

支持的关键字（Draft 2020-12 子集）：
  $ref(仅本地指针) / type / enum / const / required / properties /
  additionalProperties / propertyNames / patternProperties /
  minProperties / maxProperties / dependentRequired /
  items / prefixItems / minItems / maxItems / uniqueItems /
  contains / minContains / maxContains /
  allOf / anyOf / oneOf / not / if-then-else /
  pattern / minLength / maxLength / format(date-time, 可选) /
  minimum / maximum / exclusiveMinimum / exclusiveMaximum / multipleOf
不支持：正则的 \p{...} Unicode 属性、$dynamicRef、外部 $ref、contentEncoding。

用法：
    from jsonschema_mini import MiniValidator
    v = MiniValidator(schema)
    errors = v.collect(instance)          # -> list[ValidationError]
    ok = v.is_valid(instance)
"""

from __future__ import annotations

import datetime as _dt
import json
import re
from dataclasses import dataclass, field
from typing import Any, Iterator

__all__ = ["MiniValidator", "ValidationError", "SchemaError"]

_TYPES = {
    "object": dict,
    "array": list,
    "string": str,
    "number": (int, float),
    "integer": int,
    "boolean": bool,
    "null": type(None),
}

_IGNORED = {
    "$schema", "$id", "$anchor", "$comment", "title", "description",
    "default", "examples", "deprecated", "readOnly", "writeOnly", "$defs",
    "definitions", "typelets", "x-ext",
}

_FORMAT_RE = {
    "date-time": re.compile(
        r"^[0-9]{4}-[0-9]{2}-[0-9]{2}[Tt ][0-9]{2}:[0-9]{2}:[0-9]{2}"
        r"(\.[0-9]{1,6})?([Zz]|[+-][0-9]{2}:[0-9]{2})$"
    )
}


class SchemaError(Exception):
    """schema 本身不合法（例如 $ref 指向不存在的位置）。"""


@dataclass
class ValidationError(Exception):
    """一次具体的校验失败。keyword 用于负向测试断言『约束确实生效』。"""

    data_path: str
    keyword: str
    message: str
    schema_path: str = ""

    def __str__(self) -> str:  # pragma: no cover - 仅用于人类可读输出
        return f"{self.keyword} @ {self.data_path}: {self.message}"


@dataclass
class _Ctx:
    root: Any
    check_format: bool = True
    max_depth: int = 60


class MiniValidator:
    def __init__(self, schema: Any, *, check_format: bool = True) -> None:
        if not isinstance(schema, (dict, bool)):
            raise SchemaError("schema root must be object or boolean")
        self.schema = schema
        self.ctx = _Ctx(root=schema, check_format=check_format)

    # --------------------------------------------------------------- public
    def iter_errors(self, instance: Any, schema: Any = None,
                    path: str = "$", spath: str = "$") -> Iterator[ValidationError]:
        return self._validate(instance, schema if schema is not None else self.schema,
                              path, spath, 0)

    def collect(self, instance: Any) -> list[ValidationError]:
        return list(self.iter_errors(instance))

    def is_valid(self, instance: Any) -> bool:
        for _ in self.iter_errors(instance):
            return False
        return True

    def check_schema(self) -> None:
        """轻量元校验：能编译的 regex、能解析的 $ref、关键字类型正确。"""
        self._meta(self.schema, "$", set())

    # --------------------------------------------------------------- meta
    def _meta(self, node: Any, spath: str, seen: set[int]) -> None:
        if isinstance(node, bool):
            return
        if not isinstance(node, dict):
            raise SchemaError(f"schema node at {spath} must be object/boolean, got {type(node).__name__}")
        if id(node) in seen:
            return
        seen.add(id(node))
        for key, val in node.items():
            if key == "$ref":
                if not isinstance(val, str) or not val.startswith("#"):
                    raise SchemaError(f"{spath}/$ref must be a local pointer, got {val!r}")
                self._resolve(val, spath)
            elif key in ("pattern", "contentPattern"):
                if not isinstance(val, str):
                    raise SchemaError(f"{spath}/{key} must be string")
                try:
                    re.compile(val)
                except re.error as exc:
                    raise SchemaError(f"{spath}/{key} is not a valid regex: {exc}") from exc
            elif key in ("properties", "patternProperties", "dependentSchemas"):
                if not isinstance(val, dict):
                    raise SchemaError(f"{spath}/{key} must be object")
                for sub in val.values():
                    self._meta(sub, f"{spath}/{key}", seen)
            elif key in ("items", "contains", "if", "then", "else", "not",
                         "additionalProperties", "propertyNames", "additionalItems"):
                self._meta(val, f"{spath}/{key}", seen)
            elif key in ("allOf", "anyOf", "oneOf", "prefixItems"):
                if not isinstance(val, list):
                    raise SchemaError(f"{spath}/{key} must be array")
                for sub in val:
                    self._meta(sub, f"{spath}/{key}[]", seen)
            elif key in ("required", "enum", "type", "uniqueItems",
                         "minItems", "maxItems", "minLength", "maxLength",
                         "minimum", "maximum", "format", "const"):
                if key == "required" and (not isinstance(val, list)
                                          or any(not isinstance(i, str) for i in val)):
                    raise SchemaError(f"{spath}/required must be array of strings")
                if key in ("minLength", "maxLength", "minItems", "maxItems") and (
                        isinstance(val, bool) or not isinstance(val, int)):
                    raise SchemaError(f"{spath}/{key} must be integer")
                if key == "type" and not isinstance(val, (str, list)):
                    raise SchemaError(f"{spath}/type must be string or array")

    # --------------------------------------------------------------- helpers
    def _resolve(self, ref: str, spath: str) -> Any:
        if ref == "#":
            return self.ctx.root
        if not ref.startswith("#/"):
            raise SchemaError(f"only local '#/...' refs supported: {ref!r} (at {spath})")
        target: Any = self.ctx.root
        for part in ref[2:].split("/"):
            part = part.replace("~1", "/").replace("~0", "~")
            if isinstance(target, dict):
                if part not in target:
                    raise SchemaError(f"$ref {ref} not found (at {spath}); missing {part!r}")
                target = target[part]
            elif isinstance(target, list) and part.isdigit():
                target = target[int(part)]
            else:
                raise SchemaError(f"$ref {ref} cannot traverse into {type(target).__name__}")
        return target

    def _merge_ref(self, schema: dict, spath: str) -> tuple[dict, str]:
        """Draft 2020-12：$ref 与同级关键字共同生效（简单合并，本项目无循环引用）。"""
        merged = {k: v for k, v in schema.items() if k not in _IGNORED and k != "$ref"}
        target = self._resolve(schema["$ref"], spath)
        if isinstance(target, bool):
            if target is False:
                merged["not"] = {}
            return merged, spath
        base = {k: v for k, v in target.items() if k not in _IGNORED}
        base.update(merged)
        return base, f"{spath}{'/' if not spath.endswith('$') else ''}$ref"

    # --------------------------------------------------------------- core
    def _validate(self, inst: Any, schema: Any, path: str, spath: str,
                  depth: int) -> Iterator[ValidationError]:
        if depth > self.ctx.max_depth:
            raise SchemaError(f"schema recursion exceeded {self.ctx.max_depth} at {spath}")
        if schema is True:
            return
        if schema is False:
            yield ValidationError(path, "false", "schema forbids any value here", spath)
            return
        if not isinstance(schema, dict):
            raise SchemaError(f"schema at {spath} must be object/boolean")

        if "$ref" in schema:
            schema, spath = self._merge_ref(schema, spath)

        # type ---------------------------------------------------------
        if "type" in schema:
            t = schema["type"]
            types = [t] if isinstance(t, str) else list(t)
            if not any(self._type_ok(inst, x) for x in types):
                yield ValidationError(
                    path, "type",
                    f"expected {'|'.join(types)}, got {self._json_type(inst)}", spath + "/type")

        # enum / const --------------------------------------------------
        if "const" in schema and not self._eq(inst, schema["const"]):
            yield ValidationError(path, "const",
                                  f"must equal {schema['const']!r}, got {inst!r}", spath + "/const")
        if "enum" in schema and not any(self._eq(inst, v) for v in schema["enum"]):
            yield ValidationError(path, "enum",
                                  f"must be one of {schema['enum']!r}, got {inst!r}", spath + "/enum")

        # string --------------------------------------------------------
        if isinstance(inst, str):
            if "minLength" in schema and len(inst) < schema["minLength"]:
                yield ValidationError(path, "minLength",
                                      f"length {len(inst)} < {schema['minLength']}", spath + "/minLength")
            if "maxLength" in schema and len(inst) > schema["maxLength"]:
                yield ValidationError(path, "maxLength",
                                      f"length {len(inst)} > {schema['maxLength']}", spath + "/maxLength")
            if "pattern" in schema and not re.search(schema["pattern"], inst):
                yield ValidationError(path, "pattern",
                                      f"does not match pattern {schema['pattern']!r}", spath + "/pattern")
            if self.ctx.check_format and schema.get("format") in _FORMAT_RE:
                rx = _FORMAT_RE[schema["format"]]
                if not rx.match(inst) or not self._valid_datetime(inst):
                    yield ValidationError(path, "format",
                                          f"not a valid {schema['format']}: {inst!r}", spath + "/format")

        # number --------------------------------------------------------
        if self._is_number(inst):
            for kw, op in (("minimum", lambda a, b: a < b), ("maximum", lambda a, b: a > b),
                           ("exclusiveMinimum", lambda a, b: a <= b),
                           ("exclusiveMaximum", lambda a, b: a >= b)):
                if kw in schema and not isinstance(schema[kw], bool) and op(inst, schema[kw]):
                    yield ValidationError(path, kw, f"{inst} violates {kw}={schema[kw]}", spath + "/" + kw)
            if "multipleOf" in schema:
                q = inst / schema["multipleOf"]
                if abs(q - round(q)) > 1e-9:
                    yield ValidationError(path, "multipleOf",
                                          f"{inst} is not a multiple of {schema['multipleOf']}",
                                          spath + "/multipleOf")

        # object --------------------------------------------------------
        if isinstance(inst, dict):
            if "required" in schema:
                for key in schema["required"]:
                    if key not in inst:
                        yield ValidationError(path, "required", f"missing required property {key!r}",
                                              spath + "/required")
            props = schema.get("properties", {})
            for key, subschema in props.items():
                if key in inst:
                    yield from self._validate(inst[key], subschema, f"{path}.{key}",
                                              f"{spath}/properties/{key}", depth + 1)
            for key, subschema in schema.get("patternProperties", {}).items():
                rx = re.compile(key)
                for k, v in inst.items():
                    if rx.search(k):
                        yield from self._validate(v, subschema, f"{path}.{k}",
                                                  f"{spath}/patternProperties/{key}", depth + 1)
            if "additionalProperties" in schema:
                ap = schema["additionalProperties"]
                known = set(props) | {p for p in schema.get("patternProperties", {})
                                      if any(re.search(p, k) for k in inst)}
                for k, v in inst.items():
                    if k not in known:
                        if ap is False:
                            yield ValidationError(path, "additionalProperties",
                                                  f"property {k!r} is not allowed",
                                                  spath + "/additionalProperties")
                        else:
                            yield from self._validate(v, ap, f"{path}.{k}",
                                                      f"{spath}/additionalProperties", depth + 1)
            if "propertyNames" in schema:
                for k in inst:
                    yield from self._validate(k, schema["propertyNames"], f"{path}.<name:{k}>",
                                              f"{spath}/propertyNames", depth + 1)
            if "minProperties" in schema and len(inst) < schema["minProperties"]:
                yield ValidationError(path, "minProperties",
                                      f"{len(inst)} properties < {schema['minProperties']}",
                                      spath + "/minProperties")
            if "maxProperties" in schema and len(inst) > schema["maxProperties"]:
                yield ValidationError(path, "maxProperties",
                                      f"{len(inst)} properties > {schema['maxProperties']}",
                                      spath + "/maxProperties")
            for dep, spec in schema.get("dependentRequired", {}).items():
                if dep in inst:
                    for key in spec:
                        if key not in inst:
                            yield ValidationError(path, "dependentRequired",
                                                  f"{dep!r} requires {key!r}", spath + "/dependentRequired")

        # array ---------------------------------------------------------
        if isinstance(inst, list):
            if "minItems" in schema and len(inst) < schema["minItems"]:
                yield ValidationError(path, "minItems",
                                      f"{len(inst)} items < {schema['minItems']}", spath + "/minItems")
            if "maxItems" in schema and len(inst) > schema["maxItems"]:
                yield ValidationError(path, "maxItems",
                                      f"{len(inst)} items > {schema['maxItems']}", spath + "/maxItems")
            if schema.get("uniqueItems"):
                seen: list[Any] = []
                for i, item in enumerate(inst):
                    if any(self._eq(item, prev) for prev in seen):
                        yield ValidationError(path, "uniqueItems", f"item #{i} is a duplicate",
                                              spath + "/uniqueItems")
                        break
                    seen.append(item)
            items = schema.get("items")
            prefix = schema.get("prefixItems")
            if isinstance(prefix, list):
                for i, item in enumerate(inst):
                    if i < len(prefix):
                        yield from self._validate(item, prefix[i], f"{path}[{i}]",
                                                  f"{spath}/prefixItems/{i}", depth + 1)
                    elif items is not None:
                        yield from self._validate(item, items, f"{path}[{i}]",
                                                  f"{spath}/items", depth + 1)
            elif items is not None:
                for i, item in enumerate(inst):
                    yield from self._validate(item, items, f"{path}[{i}]",
                                              f"{spath}/items", depth + 1)
            if "contains" in schema:
                matched = [i for i, item in enumerate(inst)
                           if not any(True for _ in self._validate(item, schema["contains"],
                                                                   f"{path}[{i}]", spath, depth + 1))]
                lo = schema.get("minContains", 1)
                hi = schema.get("maxContains")
                if len(matched) < lo:
                    yield ValidationError(path, "contains",
                                          f"needs >= {lo} item(s) matching contains, found {len(matched)}",
                                          spath + "/contains")
                if hi is not None and len(matched) > hi:
                    yield ValidationError(path, "maxContains",
                                          f"matched {len(matched)} > maxContains {hi}", spath + "/maxContains")

        # applicators ---------------------------------------------------
        for kw in ("allOf", "anyOf", "oneOf"):
            if kw in schema:
                ok_count = 0
                for i, subschema in enumerate(schema[kw]):
                    errs = list(self._validate(inst, subschema, path, f"{spath}/{kw}/{i}", depth + 1))
                    if not errs:
                        ok_count += 1
                    elif kw == "allOf":
                        yield from errs
                if kw == "anyOf" and ok_count == 0:
                    yield ValidationError(path, "anyOf", "no subschema in anyOf matched",
                                          f"{spath}/anyOf")
                if kw == "oneOf" and ok_count != 1:
                    yield ValidationError(path, "oneOf",
                                          f"exactly 1 subschema must match, {ok_count} matched",
                                          f"{spath}/oneOf")
        if "not" in schema:
            if not any(True for _ in self._validate(inst, schema["not"], path,
                                                    f"{spath}/not", depth + 1)):
                yield ValidationError(path, "not", "value must not match the 'not' subschema",
                                      f"{spath}/not")
        if "if" in schema:
            cond_ok = not any(True for _ in self._validate(inst, schema["if"], path,
                                                           f"{spath}/if", depth + 1))
            branch = "then" if cond_ok else "else"
            if branch in schema:
                yield from self._validate(inst, schema[branch], path, f"{spath}/{branch}", depth + 1)

    # --------------------------------------------------------------- typing
    @staticmethod
    def _json_type(inst: Any) -> str:
        if inst is None:
            return "null"
        if isinstance(inst, bool):
            return "boolean"
        if isinstance(inst, (int, float)):
            return "integer" if isinstance(inst, int) else "number"
        if isinstance(inst, str):
            return "string"
        if isinstance(inst, list):
            return "array"
        if isinstance(inst, dict):
            return "object"
        return type(inst).__name__

    @staticmethod
    def _type_ok(inst: Any, name: str) -> bool:
        if name == "integer":
            return isinstance(inst, int) and not isinstance(inst, bool)
        if name == "number":
            return isinstance(inst, (int, float)) and not isinstance(inst, bool)
        if name == "boolean":
            return isinstance(inst, bool)
        cls = _TYPES.get(name)
        return bool(cls) and isinstance(inst, cls)

    @staticmethod
    def _is_number(inst: Any) -> bool:
        return isinstance(inst, (int, float)) and not isinstance(inst, bool)

    @staticmethod
    def _eq(a: Any, b: Any) -> bool:
        if isinstance(a, bool) != isinstance(b, bool):
            return False
        if isinstance(a, (int, float)) and isinstance(b, (int, float)):
            return float(a) == float(b)
        if isinstance(a, dict) and isinstance(b, dict):
            return a.keys() == b.keys() and all(MiniValidator._eq(a[k], b[k]) for k in a)
        if isinstance(a, list) and isinstance(b, list):
            return len(a) == len(b) and all(MiniValidator._eq(x, y) for x, y in zip(a, b))
        return a == b

    @staticmethod
    def _valid_datetime(text: str) -> bool:
        try:
            _dt.datetime.fromisoformat(text.replace("Z", "+00:00").replace("z", "+00:00"))
            return True
        except ValueError:
            return False


def load_json(path: str) -> Any:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


if __name__ == "__main__":  # pragma: no cover - 手工冒烟
    import sys

    schema_doc = load_json(sys.argv[1])
    doc = load_json(sys.argv[2])
    v = MiniValidator(schema_doc)
    v.check_schema()
    targets = doc if isinstance(doc, list) else doc.get("records", [])
    for rec in targets:
        errs = v.collect(rec)
        print(rec.get("id", "?"), "OK" if not errs else [str(e) for e in errs])
