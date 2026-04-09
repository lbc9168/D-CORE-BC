"""
历史数据清洗脚本
将 projects.db 中 project_stage / involvement_type / category
三个字段的非规范值映射为合法枚举值。

运行方式：
    cd E:\Projects\BRI_RE_Database_v2
    uv --project s01b_project_extractor run python scripts/clean_historical_data.py
"""

import sqlite3
import sys
from pathlib import Path

PROJ_DB = Path(__file__).parent.parent / "s01b_project_extractor" / "data" / "projects.db"

# ─── 合法值集合 ───────────────────────────────────────────────────────────────
VALID_STAGES = {
    "拟开发", "收购", "中标", "签约", "开工", "在建", "竣工", "投入运营", "Unknown",
}
VALID_INVOLVEMENT = {
    "BOT/BOOT", "EPC总承包", "设计/施工服务方", "股权投资方",
    "设备供应商", "投融资+EPC", "融资方", "Unknown",
}
VALID_CATEGORIES = {
    "新能源制造业", "输变电建设", "火电", "光伏", "风电", "水电",
    "核电", "地热及余热", "生物质", "储能", "综合能源", "Unknown",
}

# ─── project_stage 映射表 ─────────────────────────────────────────────────────
STAGE_MAP: dict[str | None, str] = {
    # 签约相关
    "融资关闭":     "签约",
    "融资封闭":     "签约",
    "融资":         "签约",
    "融资完成":     "签约",
    "融资支持":     "签约",
    "设备订单":     "签约",
    "设备供应":     "签约",
    "已供货":       "签约",
    "承接":         "签约",
    # 拟开发相关
    "规划中":       "拟开发",
    "规划":         "拟开发",
    "规划获批":     "拟开发",
    "获批":         "拟开发",
    "获得投资许可": "拟开发",
    "获得开发许可": "拟开发",
    "已获批准":     "拟开发",
    "批准":         "拟开发",
    "审批":         "拟开发",
    "备案":         "拟开发",
    "立项":         "拟开发",
    "申报发行":     "拟开发",
    "项目开发":     "拟开发",
    "开发中":       "拟开发",
    "开发":         "拟开发",
    "合作开发":     "拟开发",
    "前期研究":     "拟开发",
    "可行性研究通过": "拟开发",
    "Concept Reviewed": "拟开发",
    "资格预审":     "拟开发",
    "资格预审对话": "拟开发",
    "预审评估":     "拟开发",
    "设计":         "拟开发",
    "设计启动":     "拟开发",
    "设计获批":     "拟开发",
    "前端工程设计": "拟开发",
    "勘察设计":     "拟开发",
    "招标":         "拟开发",
    "投标":         "拟开发",
    "计划于2026年同步启动改造两台机组": "拟开发",
    # 开工相关
    "施工准备":     "开工",
    "准备建设":     "开工",
    "启动":         "开工",
    # 在建相关
    "建设阶段":     "在建",
    "实施阶段":     "在建",
    "设备制造":     "在建",
    "设备发运":     "在建",
    "设备运输":     "在建",
    # 竣工相关
    "已完工":       "竣工",
    "收尾":         "竣工",
    "获得并网许可": "竣工",
    # 投入运营相关
    "获得发电业务许可证": "投入运营",
    # Unknown
    "项目建设许可被取消": "Unknown",
    "终止":         "Unknown",
    "合作":         "Unknown",
    "一般合作":     "Unknown",
    "试点":         "Unknown",
    "设备更换":     "Unknown",
    "推进":         "Unknown",
    "技术研发":     "Unknown",
    "irrelevant":   "Unknown",
    "合作开发":     "Unknown",
    None:           "Unknown",
}

# ─── involvement_type 映射表 ──────────────────────────────────────────────────
INVOLVEMENT_MAP: dict[str | None, str] = {
    # → Unknown（空/模糊/临时状态）
    "":             "Unknown",
    None:           "Unknown",
    "未知":         "Unknown",
    "合作":         "Unknown",
    "合作方":       "Unknown",
    "合作伙伴":     "Unknown",
    "合作推进":     "Unknown",
    "合作探讨":     "Unknown",
    "合作协议":     "Unknown",
    "合作协定":     "Unknown",
    "战略合作":     "Unknown",
    "战略合作方":   "Unknown",
    "战略合作伙伴": "Unknown",
    "一般合作":     "Unknown",
    "投标方":       "Unknown",
    "投标":         "Unknown",
    "投标者":       "Unknown",
    "投标牵头方":   "Unknown",
    "联合竞标":     "Unknown",
    "联合竞标方":   "Unknown",
    "联合投标人":   "Unknown",
    "联合体牵头方/投标方": "Unknown",
    "签约":         "Unknown",
    "签约方":       "Unknown",
    "支持方":       "Unknown",
    "参与方":       "Unknown",
    "参与":         "Unknown",
    "项目方":       "Unknown",
    "项目合作方":   "Unknown",
    "项目主体":     "Unknown",
    "项目主导方":   "Unknown",
    "项目牵头方":   "Unknown",
    "联合体成员":   "Unknown",
    "联合体成员/技术提供方": "Unknown",
    "联营体成员":   "Unknown",
    "联合项目团队": "Unknown",
    "联合签约方":   "Unknown",
    "联合签约":     "Unknown",
    "牵头联合体":   "Unknown",
    "牵头财团成员": "Unknown",
    "牵头":         "Unknown",
    "见证方":       "Unknown",
    "买方":         "Unknown",
    "潜在投资者":   "Unknown",
    "潜在战略投资者": "Unknown",
    "潜在合作伙伴": "Unknown",
    "土地提供方及热能采购方": "Unknown",
    "开发合作":     "Unknown",
    "基建优势，保障项目推进": "Unknown",
    "政府支持":     "Unknown",
    "技术合作":     "Unknown",
    # → 股权投资方
    "业主":         "股权投资方",
    "项目业主":     "股权投资方",
    "业主单位":     "股权投资方",
    "项目业主/合作方": "股权投资方",
    "项目业主 (推测)": "股权投资方",
    "项目 Developer": "股权投资方",
    "项目开发商":   "股权投资方",
    "项目开发者":   "股权投资方",
    "项目开发方":   "股权投资方",
    "项目开发商/设备制造商": "股权投资方",
    "项目开发商/设备供应商": "股权投资方",
    "项目开发商/业主": "股权投资方",
    "项目开发者/业主": "股权投资方",
    "开发商":       "股权投资方",
    "开发主体":     "股权投资方",
    "开发建设":     "股权投资方",
    "开发":         "股权投资方",
    "Project Developer": "股权投资方",
    "光伏独立发电商（IPP）": "股权投资方",
    "联合开发商":   "股权投资方",
    "合作开发商":   "股权投资方",
    "合作开发":     "股权投资方",
    "投资方":       "股权投资方",
    "投资建设":     "股权投资方",
    "投资建设方":   "股权投资方",
    "投资方, 建设方": "股权投资方",
    "联合投资":     "股权投资方",
    "联合投资建设": "股权投资方",
    "投资":         "股权投资方",
    "投资人":       "股权投资方",
    "投资开发":     "股权投资方",
    "新能源投资方": "股权投资方",
    "参与投资":     "股权投资方",
    "控股股东":     "股权投资方",
    "控股经营":     "股权投资方",
    "控股":         "股权投资方",
    "收购方":       "股权投资方",
    "发起人":       "股权投资方",
    "合资组建及运营": "股权投资方",
    "参股并承建":   "股权投资方",
    "运营方":       "股权投资方",
    "运营商":       "股权投资方",
    "运营管理":     "股权投资方",
    "技术投资者":   "股权投资方",
    "自主开发并独立实施": "股权投资方",
    "项目投资方、运营方和技术解决方案供应商": "股权投资方",
    # → EPC总承包
    "EPC":          "EPC总承包",
    "EPC服务方":    "EPC总承包",
    "EPC总包方":    "EPC总承包",
    "EPC管理":      "EPC总承包",
    "EPC代理":      "EPC总承包",
    "EPCM总承包":   "EPC总承包",
    "EPCIC合同":    "EPC总承包",
    "EPCC工程":     "EPC总承包",
    "EPC和O&M服务": "EPC总承包",
    "EPC总承包, 设备供应商": "EPC总承包",
    "施工总承包":   "EPC总承包",
    "总承包商":     "EPC总承包",
    "总包方":       "EPC总承包",
    "总包":         "EPC总承包",
    "承建":         "EPC总承包",
    "承建方":       "EPC总承包",
    "承包商":       "EPC总承包",
    "主要承包商":   "EPC总承包",
    "承建和运营方": "EPC总承包",
    "中标":         "EPC总承包",
    "中标者":       "EPC总承包",
    "联合中标":     "EPC总承包",
    "联合中标方":   "EPC总承包",
    "联合体中标":   "EPC总承包",
    "联合承建":     "EPC总承包",
    "联合体承建":   "EPC总承包",
    "施工建设方":   "EPC总承包",
    "施工交付":     "EPC总承包",
    "项目总包与资源整合": "EPC总承包",
    "项目实施方":   "EPC总承包",
    "项目实施主体": "EPC总承包",
    "项目建设方":   "EPC总承包",
    "具体实施":     "EPC总承包",
    "执行":         "EPC总承包",
    "实施方":       "EPC总承包",
    "交钥匙工程":   "EPC总承包",
    "设计、供货、土建施工、安装": "EPC总承包",
    "设备供应商, 开发商, 建设方, 运维服务方": "EPC总承包",
    # → 投融资+EPC
    "EPC+F":        "投融资+EPC",
    "BOOT":         "BOT/BOOT",
    "投融资-建设-并网发电-运营": "投融资+EPC",
    "投建营":       "投融资+EPC",
    "投建营一体化": "投融资+EPC",
    "投资、建设和运营": "投融资+EPC",
    "投资、建设、运营": "投融资+EPC",
    "投资、建设并运营管理": "投融资+EPC",
    "投资、建设及运营一体化": "投融资+EPC",
    "投资/建设/运营": "投融资+EPC",
    "建设运营":     "投融资+EPC",
    "独立投资建设": "投融资+EPC",
    "投资、建设、运营一体化": "投融资+EPC",
    # → 设计/施工服务方
    "设计":         "设计/施工服务方",
    "设计方":       "设计/施工服务方",
    "设计服务方":   "设计/施工服务方",
    "施工服务方":   "设计/施工服务方",
    "勘察设计服务方": "设计/施工服务方",
    "咨询和设计服务": "设计/施工服务方",
    "技术支持":     "设计/施工服务方",
    "技术服务":     "设计/施工服务方",
    "技术提供方":   "设计/施工服务方",
    "技术提供商":   "设计/施工服务方",
    "技术供应商":   "设计/施工服务方",
    "技术咨询、分包选择与管理、无功补偿设备供货、设计审核、现场协调": "设计/施工服务方",
    "技术和供货服务": "设计/施工服务方",
    "施工":         "设计/施工服务方",
    "土建":         "设计/施工服务方",
    "土建施工":     "设计/施工服务方",
    "施工管理":     "设计/施工服务方",
    "施工监理方":   "设计/施工服务方",
    "施工监理合同": "设计/施工服务方",
    "分包商":       "设计/施工服务方",
    "分包":         "设计/施工服务方",
    "分包合同签署方": "设计/施工服务方",
    "运维":         "设计/施工服务方",
    "运维承包商":   "设计/施工服务方",
    "运维服务方":   "设计/施工服务方",
    "运维管理":     "设计/施工服务方",
    "运行维护":     "设计/施工服务方",
    "项目管理":     "设计/施工服务方",
    "项目管理服务": "设计/施工服务方",
    "管理方":       "设计/施工服务方",
    "监理服务方":   "设计/施工服务方",
    "工程监理服务": "设计/施工服务方",
    "调试":         "设计/施工服务方",
    "调试服务方":   "设计/施工服务方",
    "专业安装工程": "设计/施工服务方",
    "升级改造":     "设计/施工服务方",
    "物流服务":     "设计/施工服务方",
    "海运段运输服务": "设计/施工服务方",
    "电网接入":     "设计/施工服务方",
    "服务提供商":   "设计/施工服务方",
    "服务方":       "设计/施工服务方",
    "组织实施和监督管理": "设计/施工服务方",
    "施工总承包":   "EPC总承包",  # duplicate from EPC block, kept for safety
    # → 设备供应商
    "设计、采购、运输合同": "设备供应商",
    "设计、供货及服务方": "设备供应商",
    "技术输出、设备供应": "设备供应商",
    "签约供货":     "设备供应商",
    "采购方":       "设备供应商",
    "采购单位":     "设备供应商",
    "设备采购方":   "设备供应商",
    "系统集成":     "设备供应商",
    "系统集成供应商": "设备供应商",
    "系统级解决方案供应商": "设备供应商",
    "储能集成商":   "设备供应商",
    "储能系统供应商": "设备供应商",
    "提供全流程解决方案，涵盖光伏组件、支架、汇流箱等核心设备": "设备供应商",
    # → 融资方
    "援助方":       "融资方",
    "援助":         "融资方",
    "融资方, 设备供应商": "融资方",
    # 多值字段：取主要类型
    "股权投资方, 设备供应商": "股权投资方",
    "股权投资方;设计/施工服务方": "股权投资方",
    "股权投资方; 设备供应商": "股权投资方",
}

# ─── category 映射表 ──────────────────────────────────────────────────────────
CATEGORY_MAP: dict[str | None, str] = {
    # 可映射到已有类别
    "光热":         "综合能源",   # CSP/太阳能热发电 → 综合能源
    "抽水蓄能":     "水电",
    "水利水渠":     "水电",
    "垃圾发电":     "生物质",
    "新能源":       "综合能源",
    # → Unknown（超出范畴或信息不足）
    "Unknown":      "Unknown",
    "油气":         "Unknown",
    "油气开发":     "Unknown",
    "油气处理":     "Unknown",
    "油气服务":     "Unknown",
    "石油":         "Unknown",
    "石油化工":     "Unknown",
    "石油开采":     "Unknown",
    "石油开采与处理": "Unknown",
    "石油和天然气": "Unknown",
    "煤炭":         "Unknown",
    "矿产开发":     "Unknown",
    "矿产资源":     "Unknown",
    "矿产资源开发": "Unknown",
    "矿产开采":     "Unknown",
    "矿业开发":     "Unknown",
    "工业园区":     "Unknown",
    "工业安装":     "Unknown",
    "基础设施建设": "Unknown",
    "农业灌溉":     "Unknown",
    "房建":         "Unknown",
    "其他":         "Unknown",
    "一般合作":     "Unknown",
    "irrelevant":   "Unknown",
    "null":         "Unknown",
    None:           "Unknown",
}


def _apply_map(conn: sqlite3.Connection, field: str,
               mapping: dict, valid_set: set, dry_run: bool) -> int:
    """将映射表应用到 extracted_projects 的指定字段，返回更新条数。"""
    cur = conn.execute(
        f"SELECT DISTINCT {field} FROM extracted_projects"
    )
    all_vals = [r[0] for r in cur.fetchall()]

    invalid = [v for v in all_vals if v not in valid_set]
    updates: list[tuple[str, str | None]] = []

    for v in invalid:
        target = mapping.get(v)
        if target is None:
            # 不在映射表里 → Unknown
            target = "Unknown"
        if target not in valid_set:
            target = "Unknown"
        updates.append((target, v))

    count = 0
    for new_val, old_val in updates:
        n = conn.execute(
            f"SELECT COUNT(*) FROM extracted_projects WHERE {field} IS ?",
            (old_val,)
        ).fetchone()[0]
        print(f"  {repr(old_val):50s} → {repr(new_val)}  ({n} 条)")
        if not dry_run:
            conn.execute(
                f"UPDATE extracted_projects SET {field}=? WHERE {field} IS ?",
                (new_val, old_val)
            )
        count += n

    return count


def run(dry_run: bool = False) -> None:
    if not PROJ_DB.exists():
        print(f"[ERROR] 找不到数据库：{PROJ_DB}")
        sys.exit(1)

    mode = "DRY-RUN（仅预览，不修改数据）" if dry_run else "正式执行（将修改数据库）"
    print(f"\n{'='*60}")
    print(f"  历史数据清洗  —  {mode}")
    print(f"{'='*60}")
    print(f"  数据库：{PROJ_DB}\n")

    conn = sqlite3.connect(PROJ_DB)

    print("─── project_stage ───────────────────────────────────────")
    n1 = _apply_map(conn, "project_stage", STAGE_MAP, VALID_STAGES, dry_run)
    print(f"  → 共 {n1} 条待更新\n")

    print("─── involvement_type ────────────────────────────────────")
    n2 = _apply_map(conn, "involvement_type", INVOLVEMENT_MAP, VALID_INVOLVEMENT, dry_run)
    print(f"  → 共 {n2} 条待更新\n")

    print("─── category ────────────────────────────────────────────")
    n3 = _apply_map(conn, "category", CATEGORY_MAP, VALID_CATEGORIES, dry_run)
    print(f"  → 共 {n3} 条待更新\n")

    if not dry_run:
        conn.commit()
        print(f"{'='*60}")
        print(f"  清洗完成，共更新 {n1+n2+n3} 条记录。")
        print(f"{'='*60}\n")
    else:
        print(f"{'='*60}")
        print(f"  预览完成，共计 {n1+n2+n3} 条记录将被更新。")
        print(f"  运行 dry_run=False 以正式执行。")
        print(f"{'='*60}\n")

    conn.close()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true",
                        help="只预览映射结果，不修改数据库")
    args = parser.parse_args()
    run(dry_run=args.dry_run)
