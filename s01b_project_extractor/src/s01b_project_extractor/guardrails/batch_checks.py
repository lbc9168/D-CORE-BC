"""Layer 3: 批次级统计校验 — 纯 Python 统计分析，毫秒级"""

from collections import Counter


def run_batch_checks(
    records: list[dict],
    total_input_articles: int = 0,
) -> tuple[list[str], list[str]]:
    """
    对一批提取记录执行批次级统计校验。

    Args:
        records: 从 projects.db 查出的字典列表（本批次的有效项目）
        total_input_articles: 本批次输入的总新闻数（用于计算有效率）

    Returns:
        (hard_errors, warnings):
        - hard_errors: 必须修复的问题（触发重试）
        - warnings: 仅记录日志的软警告
    """
    hard_errors: list[str] = []
    warnings: list[str] = []

    n_valid = len(records)

    # ── 有效项目占比过低 ──
    if total_input_articles > 5 and n_valid == 0:
        hard_errors.append(
            f"有效项目为 0 条（输入 {total_input_articles} 篇新闻），"
            "提取可能完全失败"
        )

    if total_input_articles > 20 and n_valid > 0:
        ratio = n_valid / total_input_articles
        if ratio < 0.05:
            warnings.append(
                f"有效项目占比过低: {n_valid}/{total_input_articles} = {ratio:.1%}，"
                "可能分类逻辑出错"
            )

    if n_valid == 0:
        return hard_errors, warnings

    # ── 单一 Category 占比过高 ──
    if n_valid > 10:
        cat_counter = Counter(
            str(r.get("category", "")).strip() for r in records if r.get("category")
        )
        if cat_counter:
            most_common_cat, most_common_count = cat_counter.most_common(1)[0]
            if most_common_count / n_valid > 0.8:
                warnings.append(
                    f"分类过于集中: '{most_common_cat}' 占 {most_common_count}/{n_valid} "
                    f"= {most_common_count/n_valid:.0%}，检查是否所有项目被误判为同一类"
                )

    # ── 容量缺失率 ──
    concrete_records = [
        r for r in records
        if str(r.get("record_type", "")).strip() == "具体项目"
    ]
    if concrete_records:
        n_missing_cap = sum(
            1 for r in concrete_records
            if not str(r.get("capacity_mw", "")).strip()
            or str(r.get("capacity_mw", "")).strip() in ("", "nan", "NA", "None")
        )
        if len(concrete_records) > 5 and n_missing_cap / len(concrete_records) > 0.5:
            warnings.append(
                f"容量缺失率过高: {n_missing_cap}/{len(concrete_records)} "
                f"= {n_missing_cap/len(concrete_records):.0%} 的具体项目缺少容量信息"
            )

    # ── 国家缺失率 ──
    n_missing_country = sum(
        1 for r in records
        if not str(r.get("country", "")).strip()
        or str(r.get("country", "")).strip() in ("", "nan", "NA", "None")
    )
    if n_valid > 5 and n_missing_country / n_valid > 0.3:
        warnings.append(
            f"国家缺失率过高: {n_missing_country}/{n_valid} "
            f"= {n_missing_country/n_valid:.0%}"
        )

    return hard_errors, warnings
