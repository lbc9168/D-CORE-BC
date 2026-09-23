"""Offline calibration scores, review evidence and explicitly provisional cost estimates."""

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from .pilot import LabelSet, PilotCorpus, assert_sources, private_path, rules_fingerprint
from .sample import file_hash

ACCEPTED = {"event_reference", "project_reference"}


def score(results: list[dict], labels: LabelSet) -> dict:
    """Count unsafe acceptance separately from cautious rejection and stage recall."""
    accepted = [row for row in results if row["decision"] in ACCEPTED]
    unsafe = [row for row in accepted if labels.labels[row["case_id"]].decision not in ACCEPTED]
    wrong_stage = [row for row in accepted if row["decision"] == "event_reference" and labels.labels[row["case_id"]].decision == "project_reference"]
    safe = [row for row in accepted if row not in unsafe and row not in wrong_stage]
    return {
        "cases": len(results), "decisions": dict(Counter(row["decision"] for row in results)),
        "accepted": len(accepted), "safe_accepts": len(safe), "unsafe_accepts": len(unsafe),
        "wrong_stage": len(wrong_stage), "provisional_precision": len(safe) / len(accepted) if accepted else None,
        "safe_projects": len({row["project_id"] for row in safe}),
        "safe_event_references": len({row["event_id"] for row in safe if row["decision"] == "event_reference"}),
        "false_accept_case_ids": [row["case_id"] for row in unsafe + wrong_stage],
        "development_gate": bool(safe) and not unsafe and not wrong_stage,
    }


def usage_summary(run: dict) -> dict:
    """Estimate listed-price usage without treating missing usage as zero cost."""
    input_tokens = output_tokens = cache_hit = known = 0
    seconds = 0.0
    for row in run["results"]:
        call = row.get("call")
        if not call or not call.get("usage"):
            continue
        known += 1
        use = call["usage"]
        if run["provider"] == "gemini":
            input_tokens += use["promptTokenCount"]
            output_tokens += use.get("candidatesTokenCount", 0) + use.get("thoughtsTokenCount", 0)
            cache_hit += use.get("cachedContentTokenCount", 0)
        else:
            input_tokens += use["prompt_tokens"]
            output_tokens += use["completion_tokens"]
            cache_hit += use.get("prompt_cache_hit_tokens", 0)
        seconds += call["elapsed_seconds"]
    if run["provider"] == "gemini":
        # No explicit cache product was requested; use full standard input price conservatively.
        low = high = (input_tokens * .10 + output_tokens * .40) / 1e6
        conservative_per_call = high / known if known else None
    else:
        low = ((input_tokens - cache_hit) * .22 + cache_hit * .007 + output_tokens * .66) / 1e6
        high = low * 2
        conservative_per_call = (input_tokens * .44 + output_tokens * 1.32) / 1e6 / known if known else None
    return {"requests": run["attempted_calls"], "usage_known_requests": known,
            "usage_missing_requests": run["attempted_calls"] - known,
            "input_tokens": input_tokens, "output_tokens": output_tokens, "cache_hit_tokens": cache_hit,
            "elapsed_model_seconds": round(seconds, 3), "listed_price_usd_low": low,
            "listed_price_usd_high": high, "no_cache_high_usd_per_call": conservative_per_call}


def cell(value: object) -> str:
    """Keep arbitrary source text within a single Markdown table cell."""
    return str(value).replace("|", "\\|").replace("\n", " ").replace("\r", " ")


def build_report(corpus_path: Path) -> dict:
    """Write local review artifacts from frozen inputs with no external requests."""
    corpus_path = private_path(corpus_path, existing=True)
    directory = corpus_path.parent
    corpus = PilotCorpus.model_validate_json(corpus_path.read_text(encoding="utf-8"))
    labels_path = corpus_path.with_suffix(".labels.json")
    labels = LabelSet.model_validate_json(labels_path.read_text(encoding="utf-8"))
    if labels.corpus_sha256 != file_hash(corpus_path) or set(labels.labels) != {c.case_id for c in corpus.cases}:
        raise ValueError("labels_do_not_match_corpus")
    assert_sources(corpus.source_hashes)
    selection = json.loads((directory / "selected_events.json").read_text(encoding="utf-8"))
    if file_hash(directory / "selected_events.json") != corpus.selected_sha256:
        raise ValueError("selection_changed")
    targets = json.loads((directory / "targets.json").read_text(encoding="utf-8"))["summary"]
    runs = [json.loads(path.read_text(encoding="utf-8")) for path in directory.glob("evaluation-*.json")]
    runs = [run for run in runs if run["corpus_sha"] == labels.corpus_sha256]
    latest = [run for run in runs if run["rules_sha"] == rules_fingerprint()]
    searches = [json.loads(path.read_text(encoding="utf-8")) for path in (directory / "searches").glob("*.json")]
    summary = {"created_at": datetime.now(timezone.utc).isoformat(), "corpus_sha256": labels.corpus_sha256,
               "labels_sha256": file_hash(labels_path), "rules_sha256": rules_fingerprint(),
               "human_confirmed_labels": labels.human_confirmed, "source_unchanged": True,
               "targets": targets, "sample": selection["summary"], "candidate_cases": len(corpus.cases),
               "readable_cases": sum(not case.page.problem for case in corpus.cases),
               "search_queries": len(searches), "search_http_attempts": sum(s.get("http_attempts") or 0 for s in searches),
               "runs": [{"run_key": run["run_key"], "provider": run["provider"], "split": run["split"],
                         "current_rules": run in latest, "complete": run["complete"],
                         "score": score(run["results"], labels), "usage": usage_summary(run)} for run in runs]}
    lines = ["# 30 事件找源校准报告", "", "当前停在样本阶段：两种模型的开发样本都有错误放行，未达到全量自动通过条件。10 个留出事件尚未调用模型，保留用于后续冻结规则验收。",
             "", "独立标签为模型调用前的代理复核草稿，尚未经用户逐条确认，不能称为人工 Golden Standard。下列指标是对草稿标签的暂定比较；明确阶段错误足以阻止本轮全量执行。",
             "", f"目标总体：{targets['all']['projects']} 项目 / {targets['all']['events']} 事件；样本 29 项目 / 30 事件（主表20、TX5、MF5）。开发集20事件（其中1事件没有候选），留出集10事件。",
             f"候选 {len(corpus.cases)} 条（旧链27、新候选9），可读 {summary['readable_cases']} 条；补搜 {len(searches)} 次。原样本33条旧链按每事件最多2条抽取27条，未处理的6条不计作拒绝。", "",
             "## 开发样本结果", "", "覆盖率分母为开发集20事件/19项目。这里只统计与冻结标签一致的安全接受；同项目不同阶段的项目参考也可正确接受。", "",
             "| 模型/规则 | 调用 | 接受 | 安全接受 | 不应接受 | 错误阶段 | 项目覆盖 | 事件覆盖 | 待确认 |", "|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for run in summary["runs"]:
        s = run["score"]
        lines.append(f"| {run['provider']} / {'v2' if run['current_rules'] else 'v1'} | {run['usage']['requests']} | {s['accepted']} | {s['safe_accepts']} | {s['unsafe_accepts']} | {s['wrong_stage']} | {s['safe_projects']}/19 | {s['safe_event_references']}/20 | {s['decisions'].get('pending',0)}/{s['cases']} |")
    lines += ["", "‘不应接受’包括容量/身份未确认等 pending 被放行；‘错误阶段’是可作项目参考但错误挂到事件上。两类不重复。美国矿区订单被模型拒绝、标签待确认，均隐藏，不算错误放行。",
              "", "## 费用与全量规模估计", "", "以下是按官方公开价和返回 token 估算的资源消耗，并非账户账单；不含本任务代理与网页工具费用。一次初版 Gemini schema 错误未保留 token，用量缺失单列，不能记为零。",
              "", "| 模型/规则 | 输入token | 输出token | 用量缺失请求 | 估计USD低—高 |", "|---|---:|---:|---:|---:|"]
    for run in summary["runs"]:
        u = run["usage"]
        lines.append(f"| {run['provider']} / {'v2' if run['current_rules'] else 'v1'} | {u['input_tokens']} | {u['output_tokens']} | {u['usage_missing_requests']} | {u['listed_price_usd_low']:.4f}—{u['listed_price_usd_high']:.4f} |")
    lines += ["", f"Serper 本轮 {len(searches)} 查询按 $1/千次约 ${len(searches)/1000:.3f}；Starter 需充值 $50/50,000 credits，消耗估计不代表需要购买套餐。",
              "全量存量有 2,856 个事件—URL 关联（2,106 个唯一 URL）；正文可复用，事件判断不能仅按 URL 复用。",
              "在最宽范围假设下，2,496 事件×3查询=7,488 查询；全部每轮5个新候选加旧链最多40,296次模型判断。该范围远高于正常早停路径，是计算场景上界，不是已授权预算。网络/API重试额外计数，实际执行必须有总请求及费用硬上限。"]
    for run in summary["runs"]:
        if run["current_rules"]:
            per = run["usage"]["no_cache_high_usd_per_call"]
            if per:
                lines.append(f"- {run['provider']}：按本轮平均token、无缓存高价估计，旧链全可读约 ${2856*per:.2f}；全部候选场景约 ${40296*per+7.488:.2f}（含搜索）。页面长度和访问率变化会改变费用；这不是绝对费用封顶。")
    lines += ["", "价格核对日：2026-09-06。 [Gemini 官方价格](https://ai.google.dev/gemini-api/docs/pricing)：Flash-Lite输入$0.10/M、输出$0.40/M；[DeepSeek 官方价格](https://api-docs.deepseek.com/quick_start/pricing/)：Flash非高峰输入未命中$0.22/M、命中$0.007/M、输出$0.66/M，高峰翻倍（工作日UTC 01–04、06–10）；[Serper 官方套餐](https://serper.dev/)：Starter $1/千次。",
              "", "## 下一步", "", "先核对下表中的阶段/容量/发布者争议，并将实际阶段、设施范围及来源身份变成可核查的结构化约束；仅重复提示词未解决问题。保持留出集不参与修订。开发门槛通过后再运行留出验收，之后才选择模型并确认全量预算。当前没有启用自动发布、生产验证库或s03/流水线接入。",
              "", "## 逐条复核", "", "页面标题、URL、引文与理由均保留在本地；完整正文在 corpus 文件中。标签未因模型结果改写。"]
    maps = {run["provider"]: {row["case_id"]: row for row in run["results"]} for run in latest}
    for case in corpus.cases:
        label = labels.labels[case.case_id]
        lines += ["", f"### {case.context.event_id} / {case.case_id}", "", f"{case.domain} · {case.split} · 目标阶段：{case.context.event.get('project_stage')} · 候选来源：{case.candidate.get('origin')}",
                  "", f"[{cell(case.page.title or case.page.problem or '页面')}]({case.page.final_url})", "", f"冻结标签：**{label.decision}**。{label.reason}",
                  "", "证据：" + "；".join(cell(q) for q in label.evidence)]
        for provider, rows in maps.items():
            row = rows.get(case.case_id)
            lines.append(f"- {provider}：{row['decision']} — {row['reason']}" if row else f"- {provider}：留出，未调用。")
    lines += ["", "## 没有候选的事件", ""] + [f"- {row['event_id']}：有限搜索后仍留空。" for row in corpus.events_without_candidates]
    (directory / "calibration-summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    (directory / "calibration-report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"report": str(directory / "calibration-report.md"), "summary": str(directory / "calibration-summary.json"), "source_unchanged": True}


def main() -> None:
    """Generate an offline report without paid calls or source writes."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("corpus", type=Path)
    print(json.dumps(build_report(parser.parse_args().corpus), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
