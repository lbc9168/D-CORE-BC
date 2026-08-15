"""Render reproducible production Provider prompts and an independent Reviewer prompt pack."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from .review_cohorts import REVIEW_BY_ID
from .single_article import WORKTREE_ROOT, load_article

TOOL_ROOT = Path(__file__).resolve().parents[2]
RUNS_DIR = TOOL_ROOT / "data" / "runs"
PIPELINE_SRC = WORKTREE_ROOT / "s01b_project_extractor" / "src"
PROMPT_FILES = {
    "s1": PIPELINE_SRC / "s01b_project_extractor" / "prompts" / "s1_relevance.py",
    "s2": PIPELINE_SRC / "s01b_project_extractor" / "prompts" / "s2_basic.py",
    "s34": PIPELINE_SRC / "s01b_project_extractor" / "prompts" / "s34_entity_finance.py",
    "s5": PIPELINE_SRC / "s01b_project_extractor" / "prompts" / "s5_summary.py",
}


def _sha256_text(value: str) -> str:
    """Hash one exact rendered prompt or source text as UTF-8."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _write_text(path: Path, value: str) -> dict[str, str]:
    """Write one generated prompt artifact and return its path and hash."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8", newline="")
    return {"path": str(path), "sha256": _sha256_text(value)}


def _write_json(path: Path, value: Any) -> dict[str, str]:
    """Write stable UTF-8 JSON and return its path and content hash."""
    return _write_text(path, json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def _load_builders() -> tuple[Any, Any, Any, Any, Any]:
    """Import the current production prompt builders and equipment policy from this worktree."""
    for path in (WORKTREE_ROOT, PIPELINE_SRC):
        if str(path) not in sys.path:
            sys.path.insert(0, str(path))
    from s01b_project_extractor.knowledge import main_equipment
    from s01b_project_extractor.prompts.s1_relevance import build_s1_prompt
    from s01b_project_extractor.prompts.s2_basic import build_s2_prompt
    from s01b_project_extractor.prompts.s34_entity_finance import build_s34_prompt
    from s01b_project_extractor.prompts.s5_summary import build_s5_prompt
    return build_s1_prompt, build_s2_prompt, build_s34_prompt, build_s5_prompt, main_equipment


def _builder_versions() -> dict[str, dict[str, str]]:
    """Record each production builder path and exact source hash."""
    return {
        stage: {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        for stage, path in PROMPT_FILES.items()
    }


def prepare_base(article_id: int) -> dict[str, Any]:
    """Render S01B-S1 and S01B-S2 from the immutable article snapshot without database candidates."""
    if article_id not in REVIEW_BY_ID:
        raise ValueError(f"文章 {article_id} 不在当前复核 cohort")
    build_s1, build_s2, _build_s34, _build_s5, _equipment = _load_builders()
    article = load_article(article_id)["article"]
    run_dir = RUNS_DIR / str(article_id)
    snapshot = {
        "id": article["id"], "title": article["title"], "content": article["content"],
        "author": article.get("author"), "publish_date": article.get("publish_date"),
        "url": article.get("url"), "article_sha256": article["article_sha256"],
    }
    s1 = build_s1(article["title"], article["content"], article.get("publish_date") or "")
    s2 = build_s2(article["title"], article["content"], article.get("publish_date") or "")
    result = {
        "article_id": article_id, "article_sha256": article["article_sha256"],
        "cohort": REVIEW_BY_ID[article_id]["cohort"], "split": REVIEW_BY_ID[article_id]["split"],
        "provider_model": "gpt-5.6-terra", "execution_order": ["s1", "s2", "s34[]", "s5[]"],
        "article_snapshot": _write_json(run_dir / "article.json", snapshot),
        "prompts": {"s1": _write_text(run_dir / "s1.prompt.txt", s1),
                    "s2": _write_text(run_dir / "s2.prompt.txt", s2)},
        "builder_versions": _builder_versions(),
    }
    _write_json(run_dir / "base-manifest.json", result)
    return result


def _structured_project(project: dict[str, Any], s34_raw: dict[str, Any]) -> dict[str, Any]:
    """Construct the pre-S01B-S5 project shape supplied by the production S01B orchestrator."""
    structured = copy.deepcopy(project)
    structured["chinese_entities"] = copy.deepcopy(s34_raw.get("chinese_entities") or [])
    common = structured.setdefault("common_info", {})
    for key, value in s34_raw.items():
        if key != "chinese_entities" and value not in (None, ""):
            common[key] = copy.deepcopy(value)
    return structured


def render_dynamic(article_id: int, working_path: Path) -> dict[str, Any]:
    """Render per-project S01B-S34 and the corresponding S01B-S5 production prompts."""
    _s1, _s2, build_s34, build_s5, equipment = _load_builders()
    article = load_article(article_id)["article"]
    working = json.loads(working_path.read_text(encoding="utf-8"))
    projects = (working.get("s2_output") or {}).get("projects") or []
    s34_outputs = working.get("s34_outputs") or []
    run_dir = RUNS_DIR / str(article_id)
    records = []
    for index, project in enumerate(projects):
        common = project.get("common_info") or {}
        name = common.get("Project Name") or common.get("Project Name (EN)") or f"project-{index + 1}"
        category = common.get("Category") or "Unknown"
        s34_prompt = build_s34(article["title"], article["content"], name, category, equipment(category))
        record: dict[str, Any] = {
            "project_index": index, "project_name": name,
            "s34": _write_text(run_dir / f"project-{index + 1:02d}.s34.prompt.txt", s34_prompt),
        }
        if index < len(s34_outputs) and isinstance(s34_outputs[index], dict):
            structured = _structured_project(project, s34_outputs[index])
            record["structured_pre_s5"] = _write_json(
                run_dir / f"project-{index + 1:02d}.pre-s5.json", structured
            )
            record["s5"] = _write_text(
                run_dir / f"project-{index + 1:02d}.s5.prompt.txt", build_s5(structured)
            )
        records.append(record)
    result = {"article_id": article_id, "article_sha256": article["article_sha256"],
              "working_result": str(working_path.resolve()), "projects": records,
              "builder_versions": _builder_versions()}
    _write_json(run_dir / "dynamic-manifest.json", result)
    return result


def render_reviewer(article_id: int, provider_path: Path) -> dict[str, Any]:
    """Render a fixed independent critic prompt from the article and completed Terra candidate."""
    article = load_article(article_id)["article"]
    provider_text = provider_path.read_text(encoding="utf-8")
    provider = json.loads(provider_text)
    if provider.get("article_sha256") != article["article_sha256"]:
        raise ValueError("Provider artifact 与文章 hash 不匹配")
    prompt = f"""你是 Golden Standard 的独立证据审校员。你只能使用下方固定新闻原文审阅 Terra Provider 候选；不得读取数据库值、人工草稿或 Gold。逐项目、逐字段判断 ok / wrong / unsupported / exclude，并输出可直接显示的完整 reviewed projects。每个 present 值必须给出新闻中的逐字证据；证据不足时使用 not_mentioned 或 needs_adjudication，严禁补造。特别检查项目过度拆分、历史项目误收、年发电量/工厂年产能/设备出口/预留配置被误作 confirmed 储能容量，以及模型事实与确定性 taxonomy policy 的职责边界。保持 parent_candidate_id 指向 Terra 候选。

【固定文章】
标题：{article['title']}
发布日期：{article.get('publish_date') or ''}
来源：{article.get('url') or ''}
正文：
{article['content']}

【Terra Provider 完整候选】
{provider_text}

只输出严格 JSON，顶层至少包含 candidate_id、execution_role="reviewer"、model="gpt-5.6-sol"、article_id、article_sha256、parent_candidate_id、article_status、non_project_reason、projects。projects 中保持 Provider 的字段 envelope，并为每个字段加入 verdict 和 reviewer_note；允许修正 value/value_state/evidence/confidence。负样本 projects 必须为空数组。
"""
    run_dir = RUNS_DIR / str(article_id)
    record = _write_text(run_dir / "reviewer.prompt.txt", prompt)
    result = {"article_id": article_id, "article_sha256": article["article_sha256"],
              "reviewer_model": "gpt-5.6-sol", "parent_candidate_id": provider.get("candidate_id"),
              "provider_path": str(provider_path.resolve()), "provider_sha256": _sha256_text(provider_text),
              "prompt": record}
    _write_json(run_dir / "reviewer-manifest.json", result)
    return result


def main() -> None:
    """Expose prompt rendering steps for Codex Provider and Reviewer Subagents."""
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare = subparsers.add_parser("prepare")
    prepare.add_argument("article_id", type=int)
    dynamic = subparsers.add_parser("dynamic")
    dynamic.add_argument("article_id", type=int)
    dynamic.add_argument("working_path", type=Path)
    reviewer = subparsers.add_parser("reviewer")
    reviewer.add_argument("article_id", type=int)
    reviewer.add_argument("provider_path", type=Path)
    args = parser.parse_args()
    if args.command == "prepare":
        result = prepare_base(args.article_id)
    elif args.command == "dynamic":
        result = render_dynamic(args.article_id, args.working_path)
    else:
        result = render_reviewer(args.article_id, args.provider_path)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
