"""critic：批判 agent——重读原文质疑某项目的提取字段并挑战相关性/范围，输出经 pydantic 校验的 verdict 列表。"""
import json
import logging
import pathlib
import sys
from typing import Literal, Optional

from pydantic import BaseModel, Field, ValidationError

# 仓库根上溯（含 bri_enums.py），供注入合法枚举
_root = next(p for p in pathlib.Path(__file__).resolve().parents if (p / "bri_enums.py").exists())
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))
import bri_enums  # noqa: E402

logger = logging.getLogger(__name__)


class Verdict(BaseModel):
    """单字段核验结论：verdict 四选一（含 exclude），confidence∈[0,1]。"""
    field: str
    verdict: Literal["ok", "wrong", "unsupported", "exclude"]
    suggested_value: Optional[str] = None
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: str = ""


def _build_critic_prompt(*, article: dict, project: dict, suspicious: list) -> str:
    """拼 critic prompt：注入合法 category/involvement 枚举 + 三类录入范围 + exclude 通道。"""
    cat_list = ", ".join(c for c in bri_enums.valid_categories() if c != "Unknown")
    inv_list = ", ".join(bri_enums.valid_involvement())
    return f"""你是严谨的核查员。下面是一篇新闻原文和从中提取的某个项目记录。
请逐字段核验提取是否被原文支持，并额外挑战：这真的是「在录入范围内、且有中国企业参与」的项目吗？

### 录入范围（只认这三类）
① 发电项目（任何燃料，含火电/燃气/煤电/核电/光伏/风电/水电/储能/生物质/地热/综合能源）；
② 新能源制造业（以产能计的组件厂/电池厂/多晶硅厂等工厂）；
③ 输变电/电网工程。
不在范围（应 exclude）：油气开采/炼化/管道/LNG 储运、采矿冶炼、纯民用基建（学校/医院/机场/铁路/港口/灌溉/供水/数据中心/住宅/工业园区本身）、纯运维检修、纯框架 MOU、纯评论榜单、纯领导访问。
注意：范围是「电力/发电（含火电）+ 制造业 + 输变电」，火电/燃气/煤电同样在范围内，勿用"清洁能源""绿色能源"等窄概念去否定火电。

原文标题：{article.get('title', '')}
原文正文：{article.get('content', '')}

提取记录（JSON）：{json.dumps(project, ensure_ascii=False)}
重点核查这些可疑字段：{suspicious}

### 合法枚举（字段纠正只能从这里选；选不出就别猜）
- category: {cat_list}
- involvement_type: {inv_list}

### 输出（严格 JSON）
{{"verdicts":[{{"field":"字段名或 status","verdict":"ok|wrong|unsupported|exclude","suggested_value":"建议值或null","confidence":0.0,"evidence":"原文依据"}}]}}
- verdict=ok：原文支持当前值。
- verdict=wrong：字段值错，给 suggested_value。**category/involvement 的 suggested_value 必须是上面合法枚举之一**；若合法枚举里没有合适值（如原文是油气/天然气），**不要**硬填——改用 exclude。
- verdict=unsupported：原文找不到依据。
- verdict=exclude：该项目**不在录入范围**或**无中企参与**（field 写 "status"）。判 exclude 时**不要**借 category/involvement 表达。证据充分、确定该排除时 confidence 给 1.0；存疑给 <1.0（会走人工抽检）。
"""


def review_project(*, article: dict, project: dict, suspicious: list, llm) -> list:
    """对一个可疑项目跑批判核验；返回 Verdict 列表；LLM/解析失败由 llm 层抛出，输出非法则抛 ValidationError。"""
    prompt = _build_critic_prompt(article=article, project=project, suspicious=suspicious)
    raw = llm.complete(prompt, stage="critic", json_mode=True)
    if not isinstance(raw, dict):
        logger.warning("critic LLM 返回非 dict: type=%s | raw=%r", type(raw).__name__, raw)
        items = []
    else:
        items = raw.get("verdicts") or []
        if not isinstance(items, list):
            logger.warning("critic LLM verdicts 非 list: type=%s", type(items).__name__)
            items = []
    try:
        return [Verdict.model_validate(it) for it in items]
    except ValidationError as e:
        logger.error("critic 输出校验失败: %s | 原始=%s", e, raw)
        raise
