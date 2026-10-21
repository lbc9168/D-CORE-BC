"""S1 阶段：调 LLM 做相关性+中企门控，in-memory 校验 status 枚举合法。"""
import logging
import json
import hashlib
from pathlib import Path

from s01b_project_extractor.stages.entity_normalize import hierarchy_path, verified_chinese_mentions

S1_IDENTITY_RECALL_VERSION = "2026-09-23.4-semantic-candidate"

logger = logging.getLogger(__name__)

_VALID_STATUS = {"valid_project", "no_chinese_participation", "irrelevant", "chinese_unnamed_participation"}


def run_s1(title: str, content: str, date: str, *, llm) -> dict:
    """跑 S1 门控，返回 {status, project_subject, is_power_generation_plant, reasoning}。"""
    from s01b_project_extractor.prompts.s1_relevance import build_s1_prompt
    prompt = build_s1_prompt(title, content, date)
    mentions = verified_chinese_mentions(title + "\n" + content)
    if mentions:
        prompt += (
            "\n### 受控名录名称线索（仅证明企业身份口径，不证明原文指代或参与当前项目）\n"
            + json.dumps(mentions, ensure_ascii=False)
            + "\n以上是名称字符串匹配。先确认原文确实指向名录企业；外国的国家电网等同名泛称不能套用中企身份。"
              "仍须区分目标项目参与与背景提及。"
              "不能因不熟悉名称判无中企，也不能仅凭命中推断项目参与或Developer。"
        )
    out = llm.complete(prompt, stage="s1", json_mode=True)
    if not isinstance(out, dict):
        raise RuntimeError(f"S1 返回非 dict: {type(out)}")
    out = dict(out)
    original_status = out.get("status")
    candidates = out.get("candidate_entities")
    if candidates is not None:
        if not isinstance(candidates, list) or any(not isinstance(c, dict) or not isinstance(c.get("name"), str)
                or c.get("chinese_likelihood") not in {"likely", "uncertain", "non_chinese"} for c in candidates):
            from s01b_project_extractor.stages.entity_response import EntityResponseError
            raise EntityResponseError("s1: invalid candidate_entities", out)
        if original_status == "no_chinese_participation" and any(c["chinese_likelihood"] in {"likely", "uncertain"} for c in candidates):
            out["status"] = "valid_project"
            out["reasoning"] = str(out.get("reasoning", "")) + "；显式语义候选仍有可能中企，继续逐项目提取"
    root = Path(__file__).resolve().parents[4]
    registry = hierarchy_path()
    registry_hashes = {
        "developer_registry.json": hashlib.sha256((root / "developer_registry.json").read_bytes()).hexdigest(),
        registry.name: hashlib.sha256(registry.read_bytes()).hexdigest(),
    }
    out["identity_recall"] = {"version": S1_IDENTITY_RECALL_VERSION,
                              "registry_hashes": registry_hashes,
                              "original_status": original_status, "mentions": mentions,
                              "rescued_for_extraction": False}
    # Hints are already supplied before the semantic decision. A substring such
    # as 国家电网 inside a foreign utility must not override an explicit negative.
    # Unfamiliar/uncertain names are candidates under the S1 prompt, independent
    # of whether this auxiliary register found them.
    return out


def validate_s1(s1_out: dict) -> list[str]:
    """S1 输出 in-memory 校验：status 必须是合法枚举。返回错误列表（空=通过）。"""
    errors = []
    status = str(s1_out.get("status", "")).strip()
    if status not in _VALID_STATUS:
        errors.append(f"S1 status 非法: {status!r}（应∈{_VALID_STATUS}）")
    return errors
