#!/usr/bin/env python3
"""Profile-driven C-to-Rust conversion harness agents.

All project-specific behaviour is supplied by the loaded markdown profile. The
agents below are reusable across C projects that declare source layout, expected
outputs, test mappings, parity tokens, and rejection rules in `json
harness-profile`.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any

from generic_harness import (
    Agent,
    CompileAgent,
    ConstraintLoadingAgent,
    ConversionContext,
    OutputScaffoldAgent,
    RepairAgent,
    check_artifact_structure,
    check_required_files,
    check_token_map,
    count_token_in_rust,
    read_text,
    run_agents,
    run_cargo,
    text_block,
    write,
)
from model_artifacts import (
    display_name,
    generate_model_brief,
    generate_report,
    generate_workspace_scaffold,
    list_relative,
)


def _profile_name(profile: dict[str, Any]) -> str:
    return str(profile.get("profile") or profile.get("name") or "project")


def _layout(profile: dict[str, Any]) -> dict[str, Any]:
    return profile.get("source_layout", {})


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    return [str(value)]


class ProfileProjectAnalysisAgent(Agent):
    name = "ProjectAnalysisAgent"

    def __init__(self, profile: dict[str, Any]) -> None:
        self.profile = profile

    def run(self, ctx: ConversionContext) -> None:
        layout = _layout(self.profile)
        src_files = self._list_many(ctx.source, _as_list(layout.get("source_dirs")))
        test_files = self._list_many(ctx.source, _as_list(layout.get("test_dirs")))
        include_files = self._list_many(ctx.source, _as_list(layout.get("include_dirs")))
        all_files = src_files + include_files + test_files
        components = self._collect_components(all_files)
        source_test_runs = self._collect_source_test_runs(ctx.source)
        public_apis = self._collect_public_apis(ctx.source / str(layout.get("public_api_header", "")))
        internal_anchors = self._collect_internal_anchors(ctx.source)
        ctx.analysis = {
            "profile": _profile_name(self.profile),
            "source_path": str(ctx.source),
            "source_exists": ctx.source.exists(),
            "src_files": src_files,
            "test_files": test_files,
            "include_files": include_files,
            "components": components,
            "source_test_runs": source_test_runs,
            "public_apis": public_apis,
            "internal_parity_anchors": internal_anchors,
        }
        write(ctx.artifact("01-analysis.json"), json.dumps(ctx.analysis, indent=2, ensure_ascii=False))
        write(ctx.artifact("01-dependency-map.md"), self._dependency_markdown(ctx, src_files, test_files, include_files))

    def _list_many(self, root: Path, subdirs: list[str]) -> list[str]:
        files: list[str] = []
        for subdir in subdirs:
            files.extend(list_relative(root, subdir))
        return files

    def _collect_components(self, files: list[str]) -> dict[str, list[str]]:
        components: dict[str, list[str]] = {}
        filters = self.profile.get("component_filters", {})
        for name, tokens in filters.items():
            lowered_tokens = [str(token).lower() for token in tokens]
            components[name] = [path for path in files if any(token in path.lower() for token in lowered_tokens)]
        return components

    def _collect_source_test_runs(self, source: Path) -> dict[str, list[str]]:
        return {
            name: self._test_runs_from_file(source / spec["source"])
            for name, spec in self.profile.get("test_suites", {}).items()
        }

    def _test_runs_from_file(self, path: Path) -> list[str]:
        pattern = _layout(self.profile).get("test_run_pattern")
        if not pattern or not path.exists():
            return []
        text = path.read_text(encoding="utf-8", errors="ignore")
        return re.findall(str(pattern), text)

    def _collect_public_apis(self, header: Path) -> list[str]:
        pattern = _layout(self.profile).get("public_api_pattern")
        if not pattern or not header.exists():
            return []
        text = header.read_text(encoding="utf-8", errors="ignore")
        return sorted(set(re.findall(str(pattern), text)))

    def _collect_internal_anchors(self, source: Path) -> dict[str, dict[str, Any]]:
        anchors: dict[str, dict[str, Any]] = {}
        search_dirs = _as_list(_layout(self.profile).get("anchor_search_dirs")) or _as_list(_layout(self.profile).get("source_dirs"))
        for relative, expected in self.profile.get("internal_parity_anchors", {}).items():
            path = self._resolve_source_path(source, relative, search_dirs)
            text = path.read_text(encoding="utf-8", errors="ignore") if path.exists() else ""
            expected_tokens = _as_list(expected)
            anchors[relative] = {
                "path": str(path),
                "exists": path.exists(),
                "expected": expected_tokens,
                "present": [name for name in expected_tokens if name in text],
                "missing_in_source": [name for name in expected_tokens if name not in text],
            }
        return anchors

    def _resolve_source_path(self, source: Path, relative: str, search_dirs: list[str]) -> Path:
        direct = source / relative
        if direct.exists() or "/" in relative or "\\" in relative:
            return direct
        for subdir in search_dirs:
            candidate = source / subdir / relative
            if candidate.exists():
                return candidate
        return source / (search_dirs[0] if search_dirs else "") / relative

    def _dependency_markdown(
        self,
        ctx: ConversionContext,
        src_files: list[str],
        test_files: list[str],
        include_files: list[str],
    ) -> str:
        components = ctx.analysis.get("components", {})
        source_runs = ctx.analysis.get("source_test_runs", {})
        component_lines = "\n".join(f"- {name}: {len(paths)} files" for name, paths in components.items()) or "- none"
        test_lines = "\n".join(f"- {name}: {len(entries)} source test entries" for name, entries in source_runs.items()) or "- none"
        return f"""
        # Project Analysis

        Profile: `{_profile_name(self.profile)}`
        Source path: `{ctx.source}`

        - Source files: {len(src_files)}
        - Test files: {len(test_files)}
        - Header/include files: {len(include_files)}
        - Public API entries: {len(ctx.analysis.get("public_apis", []))}
        - Internal parity anchor groups: {len(ctx.analysis.get("internal_parity_anchors", {}))}

        ## Component buckets

        {component_lines}

        ## Source test entries

        {test_lines}
        """


class ProfileSkeletonGenerationAgent(Agent):
    name = "SkeletonGenerationAgent"

    def __init__(self, profile: dict[str, Any]) -> None:
        self.profile = profile

    def run(self, ctx: ConversionContext) -> None:
        generate_workspace_scaffold(ctx.out, self.profile)
        write(
            ctx.artifact("02-skeleton.md"),
            f"""
            # Skeleton Generation

            Prepared the Rust crate work area at `{ctx.out}`.

            Python creates only directories and Cargo manifest scaffolding.
            The model must author `src/*.rs` and `tests/*.rs` from the profile,
            specs, and source code.
            """,
        )


class ProfileContextBuilderAgent(Agent):
    name = "ContextBuilderAgent"

    def __init__(self, profile: dict[str, Any]) -> None:
        self.profile = profile

    def run(self, ctx: ConversionContext) -> None:
        module_contexts: dict[str, Any] = {}
        for name, spec in self.profile.get("module_contexts", {}).items():
            component_key = spec.get("component_key")
            module_contexts[name] = {
                "source_hints": ctx.analysis.get("components", {}).get(component_key, []),
                "target": spec.get("target"),
                "required_mechanisms": spec.get("required_mechanisms", []),
            }
        ctx.context_index = {
            "module_contexts": module_contexts,
            "function_hints": self._collect_function_hints(ctx.source),
            "public_apis": ctx.analysis.get("public_apis", []),
            "internal_parity_anchors": ctx.analysis.get("internal_parity_anchors", {}),
        }
        write(ctx.artifact("03-context.json"), json.dumps(ctx.context_index, indent=2, ensure_ascii=False))

    def _collect_function_hints(self, source: Path) -> list[dict[str, str]]:
        hints: list[dict[str, str]] = []
        if not source.exists():
            return hints
        layout = _layout(self.profile)
        roots = _as_list(layout.get("source_dirs")) + _as_list(layout.get("include_dirs")) + _as_list(layout.get("test_dirs"))
        globs = _as_list(layout.get("function_hint_globs")) or ["*.c", "*.h"]
        seen: set[Path] = set()
        paths: list[Path] = []
        for subdir in roots:
            base = source / subdir
            if not base.exists():
                continue
            for glob in globs:
                for path in sorted(base.rglob(glob)):
                    if path.is_file() and path not in seen:
                        paths.append(path)
                        seen.add(path)
        for path in paths:
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for token in self.profile.get("function_hint_tokens", []):
                if str(token) in text:
                    hints.append({"file": str(path.relative_to(source)).replace("\\", "/"), "symbol_prefix": str(token)})
        return hints


class ProfileParityMatrixAgent(Agent):
    name = "ParityMatrixAgent"

    def __init__(self, profile: dict[str, Any]) -> None:
        self.profile = profile

    def run(self, ctx: ConversionContext) -> None:
        layout = _layout(self.profile)
        header = ctx.source / str(layout.get("public_api_header", ""))
        public_apis = self._public_apis(header)
        parity = {
            "profile": _profile_name(self.profile),
            "public_apis": public_apis,
            "expected_public_apis": self.profile.get("c_api_parity_symbols", {}),
            "expected_one_to_one_features": self.profile.get("one_to_one_features", {}),
            "source_to_rust_modules": self.profile.get("source_to_rust_modules", {}),
            "notes": [
                "This matrix is supplied by the markdown profile, not by project-specific Python.",
                "Validation fails when generated Rust lacks required public API parity names or profile features.",
                "Profile-defined shortcut rejection rules are enforced even if high-level tests pass.",
            ],
        }
        write(ctx.artifact("04-function-parity.json"), json.dumps(parity, indent=2, ensure_ascii=False))

    def _public_apis(self, header: Path) -> list[str]:
        pattern = _layout(self.profile).get("public_api_pattern")
        if not pattern or not header.exists():
            return []
        text = header.read_text(encoding="utf-8", errors="ignore")
        return sorted(set(re.findall(str(pattern), text)))


class ProfileTranslationAgent(Agent):
    name = "TranslationAgent"

    def __init__(self, profile: dict[str, Any]) -> None:
        self.profile = profile

    def run(self, ctx: ConversionContext) -> None:
        generate_model_brief(ctx.root, ctx.source, ctx.out, ctx.result, ctx.logs, self.profile, ctx.analysis, ctx.context_index)
        write(
            ctx.artifact("04-translation.md"),
            f"""
            # Model-Guided Translation

            The harness emitted model-facing generation guidance instead of
            writing Rust implementation code from Python.

            - Work item: `{ctx.out / "MODEL_TASK.md"}`
            - Harness brief: `{ctx.result / "harness" / "04-model-generation-brief.md"}`
            - Parity matrix: `{ctx.result / "harness" / "04-function-parity.json"}`

            The model must author or repair the Rust crate under `{ctx.out}`.
            Validation failures are kept as feedback for that generation loop.
            """,
        )


class ProfileValidationAgent(Agent):
    name = "ValidationAgent"

    def __init__(self, profile: dict[str, Any]) -> None:
        self.profile = profile
        self.duplicate_test_name_map = {
            (item["suite"], item["source"], item["occurrence"]): item["target"]
            for item in profile.get("duplicate_test_name_map", [])
        }

    def run(self, ctx: ConversionContext) -> None:
        cargo_path = shutil.which(ctx.cargo)
        self._ensure_report_placeholders(ctx)
        checks = {
            "required_artifact_structure": check_artifact_structure(
                ctx,
                [("result_function_parity_json", ctx.result / "harness" / "04-function-parity.json")],
            ),
            "constraint_docs": {item["path"]: item["exists"] for item in ctx.constraints},
            "required_files": check_required_files(ctx.out, self.profile.get("required_output_files", [])),
            "api_symbols": check_token_map(ctx.out, self.profile.get("api_symbols", {})),
            "c_api_parity": self._check_c_api_parity(ctx.out),
            "one_to_one_features": self._check_one_to_one_features(ctx.out),
            "behaviour_model_rejection": self._check_behaviour_model_rejection(ctx.out),
            "translated_test_coverage": self._check_translated_test_coverage(ctx),
            "readme_test_coverage": self._check_readme_test_coverage(ctx.out),
            "unsafe_occurrences": count_token_in_rust(ctx.out, "unsafe") if self.profile.get("disallow_unsafe", True) else 0,
        }
        if ctx.skip_cargo or cargo_path is None:
            cargo_test = {
                "status": "skipped",
                "reason": "cargo not found" if cargo_path is None else "disabled by --skip-cargo",
            }
        else:
            cargo_test = run_cargo(ctx, ["test"])
        ctx.validation_result = {
            "status": self._validation_status(checks, cargo_test),
            "failures": self._validation_failures(checks, cargo_test),
            "checks": checks,
            "cargo_test": cargo_test,
        }
        write(ctx.artifact("07-validation.json"), json.dumps(ctx.validation_result, indent=2, ensure_ascii=False))
        generate_report(ctx.root, ctx.source, ctx.out, ctx.result, ctx.logs, ctx.validation_result, ctx.analysis, self.profile)
        self._append_harness_report(ctx)

    def _ensure_report_placeholders(self, ctx: ConversionContext) -> None:
        project = display_name(self.profile)
        if not (ctx.result / "output.md").exists():
            write(ctx.result / "output.md", f"# {project} Rust Conversion Execution Report\n\nPending validation.\n")
        if not (ctx.result / "issues" / "00-summary.md").exists():
            write(ctx.result / "issues" / "00-summary.md", "# Conversion summary\n\nPending validation.\n")

    def _check_c_api_parity(self, out: Path) -> dict[str, Any]:
        result: dict[str, Any] = {}
        modules_by_group = self.profile.get("c_api_parity_modules", {})
        for group, symbols in self.profile.get("c_api_parity_symbols", {}).items():
            text = "\n".join(read_text(out / relative) for relative in modules_by_group.get(group, []))
            missing = [symbol for symbol in symbols if symbol not in text]
            result[group] = {
                "ok": not missing,
                "missing": missing,
            }
        return result

    def _check_one_to_one_features(self, out: Path) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for relative, features in self.profile.get("one_to_one_features", {}).items():
            text = read_text(self._output_path(out, relative, default_dir="src"))
            feature_result = {}
            for feature, tokens in features.items():
                missing = [token for token in tokens if token not in text]
                feature_result[feature] = {
                    "ok": not missing,
                    "missing": missing,
                }
            result[relative] = feature_result
        return result

    def _check_behaviour_model_rejection(self, out: Path) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for name, rule in self.profile.get("behaviour_model_rejection", {}).items():
            text = read_text(out / rule["file"])
            bad_hits = [token for token in rule.get("bad", []) if token in text]
            missing_offsets = [token for token in rule.get("required_offsets", []) if token not in text]
            result[name] = {
                "ok": not (bad_hits and missing_offsets),
                "bad_hits": bad_hits,
                "missing_required_offsets": missing_offsets,
            }
        return result

    def _check_translated_test_coverage(self, ctx: ConversionContext) -> dict[str, Any]:
        source_runs = ctx.analysis.get("source_test_runs", {})
        suites = self.profile.get("test_suites", {})
        expected = {
            suite: self._expected_rust_test_names(source_runs.get(suite, []), suite)
            for suite in suites
        }
        actual = {
            suite: self._rust_test_names(ctx.out / spec["target"])
            for suite, spec in suites.items()
        }
        return {
            "source_runs": source_runs,
            "expected_rust_tests": expected,
            "actual_rust_tests": actual,
            "missing": {
                suite: [name for name in expected[suite] if name not in actual[suite]]
                for suite in suites
            },
        }

    def _check_readme_test_coverage(self, out: Path) -> dict[str, Any]:
        coverage = self.profile.get("readme_test_coverage", {})
        required = coverage.get("required_rust_tests", {})
        result: dict[str, Any] = {
            "source": coverage.get("source"),
            "required_rust_tests": required,
            "actual_rust_tests": {},
            "missing": {},
        }
        for relative, expected in required.items():
            actual = self._rust_test_names(out / relative)
            result["actual_rust_tests"][relative] = actual
            result["missing"][relative] = [name for name in expected if name not in actual]
        return result

    def _expected_rust_test_names(self, source_runs: list[str], suite: str) -> list[str]:
        counts: dict[str, int] = {}
        expected = []
        for name in source_runs:
            counts[name] = counts.get(name, 0) + 1
            mapped = self.duplicate_test_name_map.get((suite, name, counts[name]))
            expected.append(mapped or name)
        return expected

    def _rust_test_names(self, path: Path) -> list[str]:
        if not path.exists():
            return []
        text = path.read_text(encoding="utf-8", errors="ignore")
        return re.findall(r"fn\s+(test_[A-Za-z0-9_]+)\s*\(", text)

    def _output_path(self, out: Path, relative: str, default_dir: str) -> Path:
        direct = out / relative
        if direct.exists() or "/" in relative or "\\" in relative:
            return direct
        return out / default_dir / relative

    def _validation_failures(self, checks: dict[str, Any], cargo_test: dict[str, Any]) -> list[str]:
        failures: list[str] = []
        for path, exists in checks.get("constraint_docs", {}).items():
            if not exists:
                failures.append(f"missing constraint document: {path}")
        for artifact, exists in checks.get("required_artifact_structure", {}).items():
            if not exists:
                failures.append(f"missing required artifact: {artifact}")
        for path, exists in checks.get("required_files", {}).items():
            if not exists:
                failures.append(f"missing generated file: {path}")
        for path, result in checks.get("api_symbols", {}).items():
            if not result["ok"]:
                failures.append(f"missing API symbols in {path}: {', '.join(result['missing'])}")
        for group, result in checks.get("c_api_parity", {}).items():
            if not result["ok"]:
                failures.append(f"missing C API parity symbols in {group}: {', '.join(result['missing'])}")
        for module, features in checks.get("one_to_one_features", {}).items():
            for feature, result in features.items():
                if not result["ok"]:
                    failures.append(f"missing one-to-one feature {module}:{feature}: {', '.join(result['missing'])}")
        for rule, result in checks.get("behaviour_model_rejection", {}).items():
            if not result["ok"]:
                failures.append(
                    f"behaviour-model shortcut detected ({rule}): bad tokens {', '.join(result['bad_hits'])}; "
                    f"missing offsets {', '.join(result['missing_required_offsets'])}"
                )
        for suite, missing in checks.get("translated_test_coverage", {}).get("missing", {}).items():
            if missing:
                failures.append(f"missing translated {suite.upper()} tests: {', '.join(missing)}")
        for path, missing in checks.get("readme_test_coverage", {}).get("missing", {}).items():
            if missing:
                failures.append(f"missing README/benchmark coverage in {path}: {', '.join(missing)}")
        if checks.get("unsafe_occurrences", 0) != 0:
            failures.append(f"unsafe occurrences must be 0, got {checks['unsafe_occurrences']}")
        if cargo_test.get("status") == "failed":
            failures.append("cargo test failed")
        if cargo_test.get("status") == "skipped" and self.profile.get("cargo_test_required", True):
            failures.append(f"cargo test skipped: {cargo_test.get('reason', 'unknown reason')}")
        return failures

    def _validation_status(self, checks: dict[str, Any], cargo_test: dict[str, Any]) -> str:
        return "failed" if self._validation_failures(checks, cargo_test) else "passed"

    def _append_harness_report(self, ctx: ConversionContext) -> None:
        report = ctx.result / "output.md"
        existing = report.read_text(encoding="utf-8") if report.exists() else ""
        appendix = self.profile.get("harness_report_appendix")
        if appendix:
            rendered = str(appendix).format(harness_dir=ctx.result / "harness")
        else:
            rendered = f"""
            ## Agent harness execution

            Harness artifacts are available under `{ctx.result / "harness"}`.

            - OutputScaffoldAgent: result and logs artifact structure.
            - ConstraintLoadingAgent: markdown profile constraint loading.
            - ProjectAnalysisAgent: source inventory and component buckets.
            - SkeletonGenerationAgent: Cargo crate layout.
            - ContextBuilderAgent: module/function context.
            - ParityMatrixAgent: profile-provided parity matrix.
            - TranslationAgent: model-facing Rust generation task.
            - CompileAgent: `cargo check` diagnostics when cargo is available.
            - RepairAgent: compile-result triage.
            - ValidationAgent: profile-driven validation gates.
            """
        write(report, existing + text_block(rendered))


def build_profile_agents(profile: dict[str, Any]) -> list[Agent]:
    return [
        OutputScaffoldAgent(),
        ConstraintLoadingAgent(profile.get("constraint_files", []), profile.get("constraint_summary_md")),
        ProfileProjectAnalysisAgent(profile),
        ProfileSkeletonGenerationAgent(profile),
        ProfileContextBuilderAgent(profile),
        ProfileParityMatrixAgent(profile),
        ProfileTranslationAgent(profile),
        CompileAgent(),
        RepairAgent(),
        ProfileValidationAgent(profile),
    ]


def run_profile_harness(ctx: ConversionContext, profile: dict[str, Any]) -> ConversionContext:
    return run_agents(ctx, build_profile_agents(profile))
