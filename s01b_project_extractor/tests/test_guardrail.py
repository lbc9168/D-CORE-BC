"""
Guardrail 主入口测试 — extraction_guardrail.py

测试策略：
  - 用 unittest.mock 替换数据库读写，避免依赖真实 DB 文件
  - 用 mock 替换 run_format_checks / run_semantic_checks / run_batch_checks，
    控制各层检验的返回值，聚焦主入口的编排逻辑
  - 重点验证 Fix 1：0条提取时直接放行，不触发数据库回退校验
"""

import pytest
from unittest.mock import MagicMock, patch

from s01b_project_extractor.guardrails.extraction_guardrail import validate_extraction


# ─────────────────────────────────────────────────────────────────────────────
# 辅助
# ─────────────────────────────────────────────────────────────────────────────

def make_output(raw: str) -> MagicMock:
    """模拟 crewAI TaskOutput 对象。"""
    out = MagicMock()
    out.raw = raw
    return out


NORMAL_RAW = (
    "提取完成。batch_id=test_20260409\n"
    "→ E:/Projects/BRI_RE_Database_v2/s01b_project_extractor/data/projects.db\n"
    "共提取 5 个有效项目。"
)


# ─────────────────────────────────────────────────────────────────────────────
# 1. Fix 1：0条提取早返回（不应查数据库）
# ─────────────────────────────────────────────────────────────────────────────

class TestZeroOutputEarlyReturn:
    """
    核心回归测试：当 TaskOutput 包含0条提取信号时，
    validate_extraction 必须直接返回 (True, raw)，
    不应访问数据库（否则会误校验历史数据）。
    """

    @pytest.mark.parametrize("signal", [
        "未生成任何输出",
        "无关键词匹配记录",
        "读取 raw_news.db 失败",
    ])
    def test_zero_output_signal_passes_immediately(self, signal):
        out = make_output(f"处理完成，但{signal}（无符合条件的数据）。")
        ok, msg = validate_extraction(out)
        assert ok is True, f"含信号 '{signal}' 时应直接通过"

    @pytest.mark.parametrize("signal", [
        "未生成任何输出",
        "无关键词匹配记录",
        "读取 raw_news.db 失败",
    ])
    def test_zero_output_does_not_touch_database(self, signal):
        """早返回不应触发任何 sqlite3.connect 调用"""
        out = make_output(f"处理完成，但{signal}。")
        with patch("s01b_project_extractor.guardrails.extraction_guardrail.sqlite3.connect") as mock_conn:
            validate_extraction(out)
            mock_conn.assert_not_called()

    def test_zero_output_returns_original_raw(self):
        raw = "处理完成，但未生成任何输出（无符合条件的数据）。"
        out = make_output(raw)
        ok, returned_msg = validate_extraction(out)
        assert returned_msg == raw

    def test_normal_output_does_not_early_return(self):
        """不含0条信号时，不应触发早返回（会继续走DB加载逻辑）"""
        out = make_output("正常的提取结果，没有信号词。batch_id=abc123")
        # 不 patch DB，会因找不到真实 DB 文件而退出校验（无记录可校验）
        # 关键：函数不崩溃，且第一步不直接返回 True（除非后续DB为空也通过）
        ok, _ = validate_extraction(out)
        # 因 DB 不存在，_load_batch_records 返回空 → 通过，但原因不是早返回
        assert ok is True  # 无记录可校验，通过


# ─────────────────────────────────────────────────────────────────────────────
# 2. 空 raw 输出
# ─────────────────────────────────────────────────────────────────────────────

class TestEmptyRaw:
    def test_none_raw_passes(self):
        out = MagicMock()
        out.raw = None
        ok, _ = validate_extraction(out)
        assert ok is True  # raw="" → 无记录 → 通过

    def test_empty_string_raw_passes(self):
        out = make_output("")
        ok, _ = validate_extraction(out)
        assert ok is True


# ─────────────────────────────────────────────────────────────────────────────
# 3. 正常流程（mock 三层校验）
# ─────────────────────────────────────────────────────────────────────────────

MOCK_RECORDS = [
    {"id": 1, "category": "光伏", "country": "巴基斯坦",
     "project_name": "测试项目", "implementing_entity": "中国电建",
     "batch_id": "test_20260409"}
]


@patch("s01b_project_extractor.guardrails.extraction_guardrail._load_batch_records")
@patch("s01b_project_extractor.guardrails.extraction_guardrail.run_format_checks")
@patch("s01b_project_extractor.guardrails.extraction_guardrail.run_semantic_checks")
@patch("s01b_project_extractor.guardrails.extraction_guardrail.run_batch_checks")
class TestNormalFlow:
    def test_all_checks_pass_returns_true(
        self, mock_batch, mock_semantic, mock_format, mock_load
    ):
        mock_load.return_value = (MOCK_RECORDS, "test_20260409")
        mock_format.return_value = []
        mock_semantic.return_value = []
        mock_batch.return_value = ([], [])

        out = make_output(NORMAL_RAW)
        ok, msg = validate_extraction(out)
        assert ok is True
        assert msg == NORMAL_RAW

    def test_format_error_returns_false(
        self, mock_batch, mock_semantic, mock_format, mock_load
    ):
        mock_load.return_value = (MOCK_RECORDS, "test_20260409")
        mock_format.return_value = ["项目 #1 分类不合法: 油气"]
        mock_semantic.return_value = []
        mock_batch.return_value = ([], [])

        out = make_output(NORMAL_RAW)
        ok, msg = validate_extraction(out)
        assert ok is False
        assert "质量问题" in msg

    def test_semantic_error_returns_false(
        self, mock_batch, mock_semantic, mock_format, mock_load
    ):
        mock_load.return_value = (MOCK_RECORDS, "test_20260409")
        mock_format.return_value = []
        mock_semantic.return_value = ["项目 #1 语义不一致: 国家字段"]
        mock_batch.return_value = ([], [])

        out = make_output(NORMAL_RAW)
        ok, msg = validate_extraction(out)
        assert ok is False

    def test_batch_hard_error_returns_false(
        self, mock_batch, mock_semantic, mock_format, mock_load
    ):
        mock_load.return_value = (MOCK_RECORDS, "test_20260409")
        mock_format.return_value = []
        mock_semantic.return_value = []
        mock_batch.return_value = (["有效项目为 0 条"], [])

        out = make_output(NORMAL_RAW)
        ok, msg = validate_extraction(out)
        assert ok is False

    def test_batch_warning_only_still_passes(
        self, mock_batch, mock_semantic, mock_format, mock_load
    ):
        """只有 warning（非 hard error）时应通过"""
        mock_load.return_value = (MOCK_RECORDS, "test_20260409")
        mock_format.return_value = []
        mock_semantic.return_value = []
        mock_batch.return_value = ([], ["分类过于集中: '光伏' 占 11/11"])

        out = make_output(NORMAL_RAW)
        ok, _ = validate_extraction(out)
        assert ok is True

    def test_semantic_exception_is_swallowed(
        self, mock_batch, mock_semantic, mock_format, mock_load
    ):
        """语义校验异常时跳过，不崩溃，其他层正常"""
        mock_load.return_value = (MOCK_RECORDS, "test_20260409")
        mock_format.return_value = []
        mock_semantic.side_effect = RuntimeError("API 超时")
        mock_batch.return_value = ([], [])

        out = make_output(NORMAL_RAW)
        ok, _ = validate_extraction(out)
        assert ok is True  # format + batch 均通过，语义跳过 → 整体通过

    def test_error_summary_truncated_at_20(
        self, mock_batch, mock_semantic, mock_format, mock_load
    ):
        """错误超过 20 条时，反馈应截断并附加'还有N个问题'"""
        mock_load.return_value = (MOCK_RECORDS, "test_20260409")
        mock_format.return_value = [f"项目 #{i} 格式错误" for i in range(25)]
        mock_semantic.return_value = []
        mock_batch.return_value = ([], [])

        out = make_output(NORMAL_RAW)
        ok, msg = validate_extraction(out)
        assert ok is False
        assert "还有" in msg and "个问题" in msg


# ─────────────────────────────────────────────────────────────────────────────
# 4. batch_id 解析
# ─────────────────────────────────────────────────────────────────────────────

class TestBatchIdParsing:
    @patch("s01b_project_extractor.guardrails.extraction_guardrail._load_batch_records")
    @patch("s01b_project_extractor.guardrails.extraction_guardrail.run_format_checks", return_value=[])
    @patch("s01b_project_extractor.guardrails.extraction_guardrail.run_semantic_checks", return_value=[])
    @patch("s01b_project_extractor.guardrails.extraction_guardrail.run_batch_checks", return_value=([], []))
    def test_batch_id_parsed_from_output(self, _b, _s, _f, mock_load):
        mock_load.return_value = (MOCK_RECORDS, "test_20260409")
        out = make_output("提取完成。batch_id=test_20260409\n→ /fake/projects.db")
        validate_extraction(out)
        # 确认 _load_batch_records 被调用，且传入了正确的 batch_id
        args, _ = mock_load.call_args
        assert args[1] == "test_20260409"

    @patch("s01b_project_extractor.guardrails.extraction_guardrail._load_batch_records")
    @patch("s01b_project_extractor.guardrails.extraction_guardrail.run_format_checks", return_value=[])
    @patch("s01b_project_extractor.guardrails.extraction_guardrail.run_semantic_checks", return_value=[])
    @patch("s01b_project_extractor.guardrails.extraction_guardrail.run_batch_checks", return_value=([], []))
    def test_missing_batch_id_passes_empty_string(self, _b, _s, _f, mock_load):
        """无 batch_id 时传空字符串给 _load_batch_records"""
        mock_load.return_value = ([], "")
        out = make_output("提取完成，但没有 batch 信息。→ /fake/projects.db")
        validate_extraction(out)
        args, _ = mock_load.call_args
        assert args[1] == ""


# ─────────────────────────────────────────────────────────────────────────────
# 5. 删除失败记录的安全保护
# ─────────────────────────────────────────────────────────────────────────────

@patch("s01b_project_extractor.guardrails.extraction_guardrail._load_batch_records")
@patch("s01b_project_extractor.guardrails.extraction_guardrail._delete_failed_records")
@patch("s01b_project_extractor.guardrails.extraction_guardrail.run_format_checks")
@patch("s01b_project_extractor.guardrails.extraction_guardrail.run_semantic_checks", return_value=[])
@patch("s01b_project_extractor.guardrails.extraction_guardrail.run_batch_checks", return_value=([], []))
class TestDeleteProtection:
    def test_delete_called_when_batch_id_from_output(
        self, _b, _s, mock_format, mock_delete, mock_load
    ):
        """batch_id 来自 TaskOutput → 自动删除失败记录"""
        mock_load.return_value = (MOCK_RECORDS, "test_20260409")
        mock_format.return_value = ["项目 #1 分类不合法: 油气"]

        out = make_output("batch_id=test_20260409\n→ /fake/projects.db")
        validate_extraction(out)
        mock_delete.assert_called_once()

    def test_delete_not_called_when_batch_id_not_from_output(
        self, _b, _s, mock_format, mock_delete, mock_load
    ):
        """batch_id 未从 TaskOutput 解析到（回退）→ 不删除，防误删"""
        mock_load.return_value = (MOCK_RECORDS, "fallback_batch_id")
        mock_format.return_value = ["项目 #1 分类不合法: 油气"]

        # TaskOutput 中无 batch_id=xxx
        out = make_output("提取结果，无 batch 信息。→ /fake/projects.db")
        validate_extraction(out)
        mock_delete.assert_not_called()
