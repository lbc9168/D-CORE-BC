"""Summarize immutable structured calibration runs and offline rechecks for human review."""

import argparse
import json
from collections import Counter
from pathlib import Path

from .pilot import LabelSet, PilotCorpus, assert_sources, private_path
from .pilot_report import cell, score, usage_summary
from .sample import file_hash, write_json


def build_report(corpus_path: Path) -> dict:
    """Produce one current report while retaining every original provider result."""
    corpus_path = private_path(corpus_path, existing=True)
    directory = corpus_path.parent
    corpus = PilotCorpus.model_validate_json(corpus_path.read_text(encoding="utf-8"))
    labels_path = corpus_path.with_suffix(".labels.json")
    labels = LabelSet.model_validate_json(labels_path.read_text(encoding="utf-8"))
    if labels.corpus_sha256 != file_hash(corpus_path):
        raise ValueError("report_labels_changed")
    assert_sources(corpus.source_hashes)
    runs = []
    for pattern in ("structured-evaluation-*.json", "structured-recheck-*.json"):
        for path in directory.glob(pattern):
            data = json.loads(path.read_text(encoding="utf-8"))
            if data["corpus_sha"] != file_hash(corpus_path) or data["labels_sha256"] != file_hash(labels_path):
                raise ValueError("report_run_inputs_changed")
            if data.get("parent_file") and file_hash(directory / data["parent_file"]) != data["parent_sha256"]:
                raise ValueError("report_recheck_parent_changed")
            runs.append(data | {"file": path.name, "file_sha256": file_hash(path)})
    runs.sort(key=lambda run: run["created_at"])
    summaries = []
    for run in runs:
        s = score(run["results"], labels)
        summaries.append({"file": run["file"], "file_sha256": run["file_sha256"], "provider": run["provider"], "split": run["split"], "offline": "recheck_key" in run, "complete": run["complete"], "score": s,
                          "eligible_for_holdout": run["split"] == "development" and run["complete"] and s["development_gate"] and s["safe_event_references"] > 0,
                          "new_requests": run.get("new_requests", run["attempted_calls"]), "errors": dict(Counter(reason for row in run["results"] if row.get("extraction_failed") for reason in row["reason_codes"]))})
    paid = [run for run in runs if "recheck_key" not in run]
    uses = [usage_summary(run) for run in paid]
    totals = {key: sum(use[key] for use in uses) for key in ("requests", "usage_known_requests", "usage_missing_requests", "input_tokens", "output_tokens", "listed_price_usd_low", "listed_price_usd_high")}
    summary = {"corpus_sha256": file_hash(corpus_path), "labels_sha256": file_hash(labels_path), "human_confirmed": labels.human_confirmed, "source_unchanged": True, "new_search_queries": 0, "usage": totals, "runs": summaries}
    lines = ["# 结构化核验校准结果", "", "本轮仍未达到全量门槛：DeepSeek开发集8条安全接受（其中3条事件参考），但冻结规则留出集接受为0；Gemini原生Schema重测4条安全接受，另11条证据跨度不合规，未进入留出。零错误放行不能掩盖零覆盖。", "",
             "本报告覆盖独立目标卡、模型事实抽取和程序判定。冻结复核标签及目标卡均为代理复核草稿，尚非人工确认金标准；本轮不写主库、不接生产导出、不授权全量。", "",
             "与旧布尔判定不同，模型只提交候选正文段落、项目关系、量值及事件动作；来源身份、阶段、实际/计划、事件时间、子设施和容量由程序分别检查。身份/容量冲突隐藏；同项目但事件不足可降为项目参考。", "",
             "## 结果与可用性", "", "开发集分母为20事件/19项目、23候选/18可读；留出集为10事件、13候选/8可读。安全接受包括项目参考，事件覆盖只统计可挂到事件的参考。", "",
             "| 模型/结果文件 | 集合 | 新请求尝试 | 接受/安全接受 | 错误放行/错阶段 | 事件覆盖 | 完整 | 可进留出 |", "|---|---|---:|---:|---:|---:|---|---|"]
    for row in summaries:
        s = row["score"]
        lines.append(f"| {row['provider']} / [{row['file']}]({row['file']}) | {row['split']}{' 离线重判' if row['offline'] else ''} | {row['new_requests']} | {s['accepted']}/{s['safe_accepts']} | {s['unsafe_accepts']}/{s['wrong_stage']} | {s['safe_event_references']} | {'是' if row['complete'] else '否'} | {'是' if row['eligible_for_holdout'] else '否'} |")
    lines += ["", "完整要求所有候选有终态、无未决请求或抽取失败。进入留出还要求至少一个安全事件参考及零错误放行；不能把全部隐藏或仅一条项目参考当作验收通过。留出即使零误放，低覆盖和未确认标签仍限制结论。",
              "", "## 实现问题与修正", "", "- 目标卡v1将完整项目名当成设施词，造成误降级。v2只保留真实子设施/期次；旧卡保留，目标事实不根据候选结果改写。",
              "- 年产能‘2万吨’按原文换算为20,000吨/年，保留模型原值与换算证据；不混同MW/MWh。",
              "- 日期必须支持同句实际动作，报道时间不能补事件时间；来源署名检查正文尾部。错误动作段落可舍弃，已独立验证的项目证据仍保留。",
              "- Gemini首轮仅JSON MIME约束；原生Schema完整约束被服务端以状态数过多拒绝。简化服务器语法后保留本地完整校验。重测消除了截断，但仍有跨度过长/不连续，未以放松证据规则换取通过。",
              "- DeepSeek保留原18次抽取，离线重新检查，核对原始prompt、正文、标签及来源库哈希，重判零新请求。",
              "", "## 用量", "", f"本轮结构化路径新增请求尝试 {totals['requests']}；返回用量 {totals['usage_known_requests']}，未返回用量 {totals['usage_missing_requests']}。后者包括连接失败和HTTP 400，不能将预留请求次数等同实际计费次数，亦不能把缺失用量记为已确认零费用。无新搜索。",
              f"按既有报告核对的公开单价，已知用量约 USD {totals['listed_price_usd_low']:.5f}—{totals['listed_price_usd_high']:.5f}，缺失用量另计；这不是账单，也不包含此前72次提示词校准及16次Serper搜索。价格与全量估算假设见 [此前报告](calibration-report.md)。",
              "", "## 当前判定逐条核对", "", "优先展示每模型最新完整开发结果；没有完整结果时展示最新完整覆盖候选但有抽取失败的结果。留出结果单列，不用于改规则。"]
    selected = []
    for provider in ("deepseek", "gemini"):
        for split in ("development", "holdout"):
            candidates = [run for run in runs if run["provider"] == provider and run["split"] == split and len(run["results"]) == sum(case.split == split for case in corpus.cases)]
            complete = [run for run in candidates if run["complete"]]
            if complete or candidates:
                selected.append((complete or candidates)[-1])
    for run in selected:
        lines += ["", f"### {run['provider']} / {run['split']} / {run['file']}", "", "| 事件 | 当前判定 | 冻结草稿 | 原因 |", "|---|---|---|---|"]
        for row in run["results"]:
            lines.append(f"| {cell(row['event_id'])} | {row['decision']} | {labels.labels[row['case_id']].decision} | {cell(row['reason'])} |")
    lines += ["", "## 留出失败的具体原因", "", "8个可读候选：6个来源身份未过、1个身份/容量冲突、1个不同项目。来源问题包括未登记北方工业、上海电气风电站点，以及中国能建页面内部署名‘西南院’‘华北院’未覆盖；搜狐托管页原始来源也待确认。5个不可读候选为2个404、3个正文不足。",
              "冻结草稿原本有6条可接受链接（2事件、4项目），本轮全部隐藏。Egypt混合项目的1600MW、储能功率与原文1GW光伏+600MWh确有口径争议，需单独人工裁决，不能为提高分数直接取消冲突。来源表仅覆盖开发页，本轮留出直接证明其泛化不足。",
              "", "## 下一步边界", "", "优先补齐可独立核实的机构身份和集团内部署名规则，再处理证据跨度与目标口径争议；不能不断给已看过的留出样本补名单并声称独立验收通过。本次留出结果保留为诊断证据，任何下一版须另取未见样本进行验收。",
              "程序词法规则和相邻段落约束尚不能取代所有多项目语义判断。覆盖率来自小型定向样本，不外推全库。全量前仍需人工确认标签、确定覆盖率目标和预算，并另行实现生产验证库及导出。"]
    write_json(directory / "structured-summary.json", summary)
    (directory / "structured-report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"report": str(directory / "structured-report.md"), "summary": str(directory / "structured-summary.json"), "usage": totals}


def main() -> None:
    """Build the local review report without contacting providers."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("corpus", type=Path)
    print(json.dumps(build_report(parser.parse_args().corpus), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
