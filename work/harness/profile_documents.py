#!/usr/bin/env python3
"""Discover project documentation and normalize it into conversion constraints."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path
from typing import Any

from generic_harness import ConversionContext, HarnessStage, text_block, write
from profile_trace import _record_profile_trace


DEFAULT_SUFFIXES = {".md", ".markdown", ".mdown", ".rst", ".txt", ".adoc"}
EXCLUDED_DIRS = {
    ".git",
    ".github",
    ".idea",
    ".vscode",
    "build",
    "dist",
    "node_modules",
    "target",
    "vendor",
}
DOCUMENT_DIRS = {
    "doc",
    "docs",
    "documentation",
    "guide",
    "guides",
    "manual",
    "samples",
    "sample",
    "examples",
    "example",
    "demos",
    "demo",
    "tests",
    "test",
}
DOCUMENT_NAME_TOKENS = {
    "api",
    "architecture",
    "configuration",
    "design",
    "example",
    "guide",
    "manual",
    "migration",
    "porting",
    "quickstart",
    "sample",
    "test",
    "testing",
    "usage",
    "接口",
    "使用",
    "配置",
    "移植",
    "测试",
    "用例",
}
KNOWN_CATEGORIES = (
    "api_contract",
    "architecture",
    "configuration",
    "overview",
    "porting",
    "testing",
    "usage",
)
MUST_MARKERS = (
    "must",
    "required",
    "shall",
    "do not",
    "don't",
    "cannot",
    "must not",
    "必须",
    "务必",
    "不得",
    "禁止",
    "不能",
    "需要",
)
SHOULD_MARKERS = ("should", "recommended", "建议", "应该", "推荐", "最好")
NEGATIVE_MARKERS = ("must not", "do not", "don't", "cannot", "不得", "禁止", "不能")
IDENTIFIER_RE = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]{2,}\b")
STOP_IDENTIFIERS = {
    "and",
    "api",
    "bool",
    "char",
    "const",
    "else",
    "false",
    "for",
    "from",
    "include",
    "int",
    "main",
    "null",
    "return",
    "should",
    "struct",
    "true",
    "typedef",
    "void",
    "while",
    "with",
}


class ProjectDocumentStage(HarnessStage):
    """Read project-owned documentation before inspecting C implementation files."""

    name = "ProjectDocumentStage"

    def __init__(self, profile: dict[str, Any]) -> None:
        self.profile = profile

    def run(self, ctx: ConversionContext) -> None:
        config = self.profile.get("documentation_discovery", {}) or {}
        if not isinstance(config, dict):
            config = {}
        payload = build_document_constraints(ctx.source, config)
        ctx.document_constraints = payload
        artifact_paths = write_document_constraint_artifacts(ctx, payload)
        index = build_constraint_index(payload)
        write(
            ctx.artifact("00-project-document-constraints.json"),
            json.dumps(index, indent=2, ensure_ascii=False),
        )
        write(ctx.artifact("00-project-document-constraints.md"), render_constraint_summary(payload, index))
        _record_profile_trace(
            ctx,
            self.name,
            "normalize_project_documentation",
            status=payload["status"],
            documents=len(payload["documents"]),
            constraints=len(payload["constraints"]),
            must_constraints=payload["summary"]["levels"].get("must", 0),
            outputs=[
                "result/harness/00-project-document-constraints.json",
                "result/harness/00-project-document-constraints.md",
                *artifact_paths,
            ],
        )


def write_document_constraint_artifacts(ctx: ConversionContext, payload: dict[str, Any]) -> list[str]:
    base = ctx.artifact("document-constraints")
    if base.exists():
        shutil.rmtree(base)
    base.mkdir(parents=True, exist_ok=True)
    write(
        base / "documents.json",
        json.dumps(
            {
                "schema": "project-document-catalog.v1",
                "source_root": payload["source_root"],
                "reading_order": payload["reading_order"],
                "documents": payload["documents"],
            },
            indent=2,
            ensure_ascii=False,
        ),
    )
    outputs = ["result/harness/document-constraints/documents.json"]
    categories = sorted(set(KNOWN_CATEGORIES) | set(payload["summary"]["categories"]))
    for category in categories:
        constraints = [item for item in payload["constraints"] if item["category"] == category]
        path = base / f"{category}.json"
        write(
            path,
            json.dumps(
                {
                    "schema": "project-document-constraint-category.v1",
                    "category": category,
                    "summary": {
                        "constraint_count": len(constraints),
                        "levels": _counts(constraints, "level"),
                    },
                    "constraints": constraints,
                },
                indent=2,
                ensure_ascii=False,
            ),
        )
        outputs.append(f"result/harness/document-constraints/{category}.json")
    return outputs


def build_constraint_index(payload: dict[str, Any]) -> dict[str, Any]:
    categories = {}
    categories_to_index = sorted(set(KNOWN_CATEGORIES) | set(payload["summary"]["categories"]))
    for category in categories_to_index:
        items = [item for item in payload["constraints"] if item["category"] == category]
        categories[category] = {
            "path": f"result/harness/document-constraints/{category}.json",
            "constraint_count": len(items),
            "levels": _counts(items, "level"),
        }
    critical = []
    for item in payload["constraints"]:
        if item["level"] not in {"must", "should"}:
            continue
        source = item["sources"][0] if item["sources"] else {}
        critical.append(
            {
                "id": item["id"],
                "category": item["category"],
                "level": item["level"],
                "statement": item["statement"],
                "source": source,
                "related_symbols": item["related_symbols"],
            }
        )
    return {
        "schema": "project-document-constraint-index.v1",
        "status": payload["status"],
        "source_root": payload["source_root"],
        "policy": payload["policy"],
        "summary": payload["summary"],
        "catalog": "result/harness/document-constraints/documents.json",
        "categories": categories,
        "critical_constraints": critical,
        "loading_policy": {
            "initial_read": "Read this index and critical_constraints only.",
            "on_demand": "Load only category files relevant to the current module, test, or validation failure.",
            "avoid": "Do not load every category file into one agent context by default.",
        },
    }


def build_document_constraints(source: Path, config: dict[str, Any] | None = None) -> dict[str, Any]:
    config = config or {}
    enabled = bool(config.get("enabled", True))
    precedence = config.get("precedence") or [
        "manual profile overrides",
        "project documentation for intended behavior and usage",
        "public headers for ABI and type signatures",
        "executable tests for observable behavior",
        "C implementation for details not specified elsewhere",
    ]
    if not enabled:
        return _empty_payload(source, "disabled", precedence)

    max_files = max(1, int(config.get("max_files", 80)))
    max_per_file = max(1, int(config.get("max_constraints_per_file", 80)))
    max_total = max(1, int(config.get("max_total_constraints", 800)))
    max_bytes = max(1024, int(config.get("max_bytes_per_file", 512 * 1024)))
    candidates = discover_documents(source, config)[:max_files]
    documents: list[dict[str, Any]] = []
    constraints: list[dict[str, Any]] = []
    seen_statements: dict[str, dict[str, Any]] = {}

    for priority, path in candidates:
        relative = _relative(path, source)
        raw = path.read_bytes()[:max_bytes]
        text = raw.decode("utf-8", errors="ignore")
        category = document_category(relative, "")
        extracted = extract_constraints(text, relative, category, max_per_file)
        documents.append(
            {
                "path": relative,
                "category": category,
                "priority": priority,
                "sha256": _file_sha256(path),
                "bytes_read": len(raw),
                "truncated": path.stat().st_size > len(raw),
                "constraint_count": len(extracted),
            }
        )
        for item in extracted:
            key = re.sub(r"\W+", "", item["statement"].casefold())
            existing = seen_statements.get(key)
            if existing is not None:
                existing["sources"].extend(source_item for source_item in item["sources"] if source_item not in existing["sources"])
                existing["related_symbols"] = sorted(set(existing["related_symbols"]) | set(item["related_symbols"]))
                continue
            seen_statements[key] = item
            constraints.append(item)
            if len(constraints) >= max_total:
                break
        if len(constraints) >= max_total:
            break

    for index, item in enumerate(constraints, start=1):
        item["id"] = f"DOC-{index:04d}"
    levels = _counts(constraints, "level")
    categories = _counts(constraints, "category")
    status = "ready" if documents else "no_documents"
    return {
        "schema": "project-document-constraints.v1",
        "status": status,
        "source_root": str(source.resolve()),
        "policy": {
            "read_before_source_analysis": True,
            "precedence": [str(item) for item in precedence],
            "conflict_action": (
                "Do not silently choose one source. Record the conflicting document/header/test evidence "
                "and preserve ABI plus tested behavior until the intended contract is resolved."
            ),
        },
        "reading_order": [item["path"] for item in documents],
        "documents": documents,
        "constraints": constraints,
        "summary": {
            "document_count": len(documents),
            "constraint_count": len(constraints),
            "levels": levels,
            "categories": categories,
            "truncated_by_limit": len(constraints) >= max_total,
        },
    }


def discover_documents(source: Path, config: dict[str, Any] | None = None) -> list[tuple[int, Path]]:
    config = config or {}
    suffixes = {str(item).lower() for item in config.get("include_suffixes", DEFAULT_SUFFIXES)}
    explicit = {str(item).replace("\\", "/") for item in config.get("files", [])}
    excluded = EXCLUDED_DIRS | {str(item).lower() for item in config.get("exclude_dirs", [])}
    found: dict[str, tuple[int, Path]] = {}
    if not source.exists():
        return []
    for path in source.rglob("*"):
        if not path.is_file():
            continue
        relative = _relative(path, source)
        parts = {part.lower() for part in path.relative_to(source).parts[:-1]}
        if parts & excluded:
            continue
        lower_name = path.name.lower()
        is_readme = lower_name.startswith("readme")
        is_explicit = relative in explicit
        in_document_dir = bool(parts & DOCUMENT_DIRS)
        stem_tokens = {token for token in re.split(r"[^\w\u4e00-\u9fff]+", path.stem.casefold()) if token}
        has_document_name = bool(stem_tokens & DOCUMENT_NAME_TOKENS)
        if not is_explicit and not is_readme and not (
            path.suffix.lower() in suffixes and (in_document_dir or has_document_name)
        ):
            continue
        priority = document_priority(relative, is_explicit)
        found[relative] = max(found.get(relative, (0, path)), (priority, path), key=lambda item: item[0])
    return sorted(found.values(), key=lambda item: (-item[0], _relative(item[1], source).casefold()))


def document_priority(relative: str, explicit: bool = False) -> int:
    lower = relative.casefold()
    name = Path(lower).name
    depth = len(Path(relative).parts)
    score = 200 if explicit else 0
    if depth == 1 and name.startswith("readme"):
        score += 120
    elif "api" in lower or "接口" in lower:
        score += 115
    elif "test" in lower or "benchmark" in lower or "测试" in lower or "用例" in lower:
        score += 110
    elif any(token in lower for token in ("quick", "usage", "sample", "example", "demo", "started", "使用", "示例")):
        score += 105
    elif any(token in lower for token in ("config", "port", "migration", "配置", "移植", "迁移")):
        score += 100
    elif name.startswith("readme"):
        score += 95
    else:
        score += 70
    return score - min(depth, 20)


def document_category(relative: str, heading: str) -> str:
    value = f"{relative} {heading}".casefold()
    categories = (
        ("api_contract", ("api", "接口", "reference")),
        ("testing", ("test", "benchmark", "测试", "用例", "验证")),
        ("configuration", ("config", "配置", "option", "macro")),
        ("porting", ("port", "migration", "移植", "迁移", "platform")),
        ("usage", ("quick", "usage", "sample", "example", "demo", "started", "使用", "示例")),
        ("architecture", ("architecture", "design", "format", "layout", "架构", "设计", "格式", "布局")),
    )
    for category, tokens in categories:
        if any(token in value for token in tokens):
            return category
    return "overview"


def extract_constraints(text: str, relative: str, default_category: str, limit: int) -> list[dict[str, Any]]:
    constraints: list[dict[str, Any]] = []
    heading = ""
    in_fence = False
    for line_number, raw in enumerate(text.splitlines(), start=1):
        stripped = raw.strip()
        if stripped.startswith(("```", "~~~")):
            in_fence = not in_fence
            continue
        if in_fence or not stripped:
            continue
        heading_match = re.match(r"^#{1,6}\s+(.+?)\s*#*$", stripped)
        if heading_match:
            heading = normalize_statement(heading_match.group(1))
            continue
        statement = normalize_statement(stripped)
        if not _is_constraint_candidate(stripped, statement, default_category, heading):
            continue
        level = constraint_level(statement)
        category = document_category(relative, heading)
        identifiers = sorted(
            {
                name
                for name in IDENTIFIER_RE.findall(raw)
                if name.casefold() not in STOP_IDENTIFIERS and ("_" in name or name.isupper())
            }
        )[:20]
        constraints.append(
            {
                "id": "",
                "category": category,
                "level": level,
                "polarity": "negative" if any(marker in statement.casefold() for marker in NEGATIVE_MARKERS) else "positive",
                "statement": statement,
                "related_symbols": identifiers,
                "sources": [{"path": relative, "line": line_number, "heading": heading}],
            }
        )
        if len(constraints) >= limit:
            break
    return constraints


def normalize_statement(value: str) -> str:
    value = re.sub(r"^\s*(?:[-*+]\s+|\d+[.)]\s+)", "", value)
    value = re.sub(r"^\s*\|?|\|?\s*$", "", value)
    value = re.sub(r"!\[[^]]*]\([^)]*\)", "", value)
    value = re.sub(r"\[([^]]+)]\([^)]*\)", r"\1", value)
    value = re.sub(r"<[^>]+>", " ", value)
    value = value.replace("`", "").replace("**", "").replace("__", "")
    value = re.sub(r"\s*\|\s*", " | ", value)
    value = re.sub(r"\s+", " ", value).strip(" \t|-#")
    return value[:1000]


def constraint_level(statement: str) -> str:
    lower = statement.casefold()
    if any(marker in lower for marker in MUST_MARKERS):
        return "must"
    if any(marker in lower for marker in SHOULD_MARKERS):
        return "should"
    return "info"


def render_constraint_summary(payload: dict[str, Any], index: dict[str, Any] | None = None) -> str:
    index = index or build_constraint_index(payload)
    summary = payload["summary"]
    reading_paths = payload["reading_order"][:8]
    reading_order = "\n".join(f"{number}. `{path}`" for number, path in enumerate(reading_paths, start=1)) or "- 未发现项目文档"
    if len(payload["reading_order"]) > len(reading_paths):
        reading_order += f"\n\n其余 `{len(payload['reading_order']) - len(reading_paths)}` 份文档见 `document-constraints/documents.json`。"
    selected = [item for item in payload["constraints"] if item["level"] in {"must", "should"}][:12]
    if not selected:
        selected = payload["constraints"][:12]
    constraint_lines = []
    for item in selected:
        source = item["sources"][0]
        constraint_lines.append(
            f"- **{item['level'].upper()}** `{item['category']}` {item['statement']} "
            f"(`{source['path']}:{source['line']}`)"
        )
    return text_block(
        f"""
        # Project Document Constraints

        项目文档已在 C 源码分析前读取并规范化。后续 Rust 设计、实现、测试和验证必须先读取本产物；
        入口 JSON 只包含索引和关键约束。完整约束按分类存放在
        `result/harness/document-constraints/`，Agent 应按职责按需读取，禁止默认一次加载全部分类。

        - status: `{payload['status']}`
        - documents: `{summary['document_count']}`
        - constraints: `{summary['constraint_count']}`
        - levels: `{json.dumps(summary['levels'], ensure_ascii=False)}`

        ## Category Shards

        {chr(10).join(f"- `{name}`: `{spec['path']}` ({spec['constraint_count']})" for name, spec in index['categories'].items()) or '- 无分类约束'}

        ## Evidence Precedence

        {chr(10).join(f'{index}. {item}' for index, item in enumerate(payload['policy']['precedence'], start=1))}

        发生冲突时不得静默猜测；记录文档、头文件和测试证据，ABI 与已验证行为保持兼容。

        ## Reading Order

        {reading_order}

        ## Normalized Constraints

        {chr(10).join(constraint_lines) or '- 未抽取到可操作约束'}
        """
    )


def _is_constraint_candidate(raw: str, statement: str, default_category: str, heading: str) -> bool:
    if len(statement) < 8 or statement in {"---", "***"}:
        return False
    if re.fullmatch(r"[:| -]+", raw):
        return False
    lower = statement.casefold()
    if any(marker in lower for marker in MUST_MARKERS + SHOULD_MARKERS):
        return True
    is_list_or_table = bool(re.match(r"^\s*(?:[-*+]\s+|\d+[.)]\s+|\|)", raw))
    category = document_category("", heading) if heading else default_category
    if category == "api_contract":
        return len(statement) >= 12
    if category in {"testing", "configuration", "porting", "usage", "architecture"}:
        return is_list_or_table or len(statement) >= 24
    return is_list_or_table and len(statement) >= 12


def _counts(items: list[dict[str, Any]], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        value = str(item.get(key, "unknown"))
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))


def _relative(path: Path, source: Path) -> str:
    return str(path.relative_to(source)).replace("\\", "/")


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(128 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _empty_payload(source: Path, status: str, precedence: list[Any]) -> dict[str, Any]:
    return {
        "schema": "project-document-constraints.v1",
        "status": status,
        "source_root": str(source.resolve()),
        "policy": {
            "read_before_source_analysis": True,
            "precedence": [str(item) for item in precedence],
            "conflict_action": "Record conflicting evidence instead of silently guessing.",
        },
        "reading_order": [],
        "documents": [],
        "constraints": [],
        "summary": {
            "document_count": 0,
            "constraint_count": 0,
            "levels": {},
            "categories": {},
            "truncated_by_limit": False,
        },
    }
