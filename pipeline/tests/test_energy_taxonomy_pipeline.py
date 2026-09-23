"""测试 pipeline 能源分类阶段：严格事实回填后写影子快照和报告。"""

import json
import sqlite3

from bri_pipeline.flow import PipelineConfig, run_energy_taxonomy_stages
from s02a_duplicate_merger.tools.duplicate_merger_tool import _init_db


def _seed(path):
    """创建可由 2B-7/2B-8 消费的最小综合能源项目。"""
    conn = _init_db(str(path))
    conn.execute(
        "INSERT INTO canonical_projects (id,project_name,category,subcategory,excluded) "
        "VALUES ('ITA-PV-0001','意大利光储项目','综合能源','光储',0)"
    )
    conn.execute(
        """INSERT INTO project_events
           (id,canonical_id,capacity_details,project_summary,generation_sources_json,
            storage_status,storage_power_mw_raw,storage_energy_mwh_raw,
            storage_power_mw,storage_energy_mwh,energy_composition_evidence)
           VALUES ('ITA-PV-0001-01','ITA-PV-0001','PV:35MW; BESS:13.75MW/50.16MWh',
            '光伏配套储能','["光伏"]','confirmed','13.75MW','50.16MWh',13.75,50.16,'光伏配套储能')"""
    )
    conn.commit()
    conn.close()


def test_pipeline_config_enables_taxonomy_shadow_by_default():
    """完整 pipeline 默认必须执行影子分类，但仍保留旧开关兼容。"""
    config = PipelineConfig()
    assert config.run_entity_suggestions is False
    assert config.run_entity_normalize is False
    assert config.run_storage_subcat_backfill is True
    assert config.run_energy_taxonomy_shadow is True


def test_energy_taxonomy_stages_write_shadow_report_without_reclassification(tmp_path):
    """集成阶段应写 shadow/报告，同时逐字保留正式 ID、类别和旧子类。"""
    db = tmp_path / "bri.db"
    reports = tmp_path / "reports"
    _seed(db)
    result = run_energy_taxonomy_stages(str(db), report_dir=str(reports))
    conn = sqlite3.connect(db)
    canonical = conn.execute("SELECT id,category,subcategory FROM canonical_projects").fetchone()
    shadow = conn.execute(
        "SELECT proposed_category,technology_tags_json FROM project_classification_shadow"
    ).fetchone()
    conn.close()
    json_reports = list(reports.glob("energy_taxonomy_shadow_*.json"))
    csv_reports = list(reports.glob("energy_taxonomy_shadow_*.csv"))
    assert canonical == ("ITA-PV-0001", "综合能源", "光储")
    assert shadow[0] == "光伏"
    # 与现行 energy_taxonomy_v2.1 一致；“含储能”已不属于技术标签枚举。
    assert json.loads(shadow[1]) == ["光储"]
    assert result["shadow"]["would_change"] == 1
    assert len(json_reports) == 1 and len(csv_reports) == 1
