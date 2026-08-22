"""S3 阶段测试：数值经 bri_parse 规范。"""
from s01b_project_extractor.stages.s3_finance import run_s3


class _FakeLLM:
    def __init__(self, p): self._p = p
    def complete(self, prompt, *, stage, json_mode=True):
        assert stage == "s3"; return self._p


def test_run_s3_parses_investment_number():
    content = "纳沃伊光伏项目获得银行贷款。"
    out = run_s3("t", content, "纳沃伊光伏项目", llm=_FakeLLM({
        "Total Project Investment (Million USD)": "1,200",
        "Financing Model": "信贷融资",
        "Financing Evidence": [{
            "tool": "credit", "quote": content, "project_anchor": "纳沃伊光伏项目",
        }],
    }))
    assert out["Total Project Investment (Million USD)"] == 1200.0
    assert out["Financing Model"] == "信贷融资"


def test_run_s3_empty_investment_kept_empty():
    out = run_s3("t", "c", "项目", llm=_FakeLLM({"Total Project Investment (Million USD)": "", "Financing Model": "Unknown"}))
    assert out["Total Project Investment (Million USD)"] == ""


def test_generic_financing_is_downgraded_to_unknown():
    """只有 financing 金额而无贷款/债券/资本金工具时不得标为信贷。"""
    out = run_s3(
        "t",
        "Capital Ventures 将为项目提供4亿美元financing。",
        "项目",
        llm=_FakeLLM({"Financing Model": "信贷融资"}),
    )
    assert out["Financing Model"] == "Unknown"


def test_share_acquisition_does_not_imply_project_equity_financing():
    """项目股权收购证明参与关系，但不自动证明项目采用股权融资。"""
    out = run_s3(
        "t",
        "公司收购该项目公司60%股权。",
        "项目",
        llm=_FakeLLM({
            "Financing Model": "股权融资",
            "Financing Evidence": [{
                "tool": "equity",
                "quote": "公司收购该项目公司60%股权。",
                "project_anchor": "项目公司",
            }],
        }),
    )
    assert out["Financing Model"] == "Unknown"


def test_syndicated_loan_is_credit_even_if_model_requests_blended():
    """项目级银团贷款只有债务工具时必须纠偏为 Credit。"""
    content = "肯尼亚梅嫩盖地热项目获得非洲开发银行和IFC组成的银团贷款。"
    out = run_s3("t", content, "肯尼亚梅嫩盖地热项目", llm=_FakeLLM({
        "Financing Model": "混合融资",
        "Financing Evidence": [{
            "tool": "credit", "quote": content, "project_anchor": "梅嫩盖地热项目",
        }],
    }))
    assert out["Financing Model"] == "信贷融资"


def test_blended_requires_two_bound_tools_for_same_project():
    """同一目标项目的贷款与资本金出资同时有证据时才保留 Blended。"""
    content = "纳沃伊绿能项目由股东资本金出资，并获得银行贷款。"
    out = run_s3("t", content, "纳沃伊绿能项目", llm=_FakeLLM({
        "Financing Model": "混合融资",
        "Financing Evidence": [
            {"tool": "equity", "quote": content, "project_anchor": "纳沃伊绿能项目"},
            {"tool": "credit", "quote": content, "project_anchor": "纳沃伊绿能项目"},
        ],
    }))
    assert out["Financing Model"] == "混合融资"


def test_portfolio_aid_with_generic_anchor_stays_unknown():
    """仪式整体的资金和技术援助不能用泛化锚点套到单个项目包。"""
    content = "启动仪式上的这些项目获得多边开发银行资金和技术援助。"
    out = run_s3("t", content, "乌兹别克斯坦2.3GW项目包", llm=_FakeLLM({
        "Financing Model": "其他",
        "Financing Evidence": [{
            "tool": "other", "quote": content, "project_anchor": "这些项目",
        }],
    }))
    assert out["Financing Model"] == "Unknown"


def test_project_specific_grant_is_other():
    """明确归属于目标项目的赠款应分类为 Other。"""
    content = "GCCA+向卡伦尼奇光伏项目提供赠款。"
    out = run_s3("t", content, "卡伦尼奇光伏项目", llm=_FakeLLM({
        "Financing Model": "其他",
        "Financing Evidence": [{
            "tool": "other", "quote": content, "project_anchor": "卡伦尼奇光伏项目",
        }],
    }))
    assert out["Financing Model"] == "其他"


def test_financing_evidence_for_other_project_is_not_mixed_into_target():
    """同篇文章中另一项目的股权证据不得把目标项目的 Credit 升为 Blended。"""
    content = (
        "纳沃伊光伏项目获得银行贷款。"
        "布哈拉风电项目由股东资本金出资。"
    )
    out = run_s3("t", content, "纳沃伊光伏项目", llm=_FakeLLM({
        "Financing Model": "混合融资",
        "Financing Evidence": [
            {
                "tool": "credit",
                "quote": "纳沃伊光伏项目获得银行贷款。",
                "project_anchor": "纳沃伊光伏项目",
            },
            {
                "tool": "equity",
                "quote": "布哈拉风电项目由股东资本金出资。",
                "project_anchor": "布哈拉风电项目",
            },
        ],
    }))
    assert out["Financing Model"] == "信贷融资"


def test_financing_tool_in_adjacent_project_clause_is_not_reused():
    """引文含目标项目名但贷款只在另一项目分句时不能形成融资标签。"""
    content = "纳沃伊光伏项目正式开工；布哈拉风电项目获得银行贷款。"
    out = run_s3("t", content, "纳沃伊光伏项目", llm=_FakeLLM({
        "Financing Model": "信贷融资",
        "Financing Evidence": [{
            "tool": "credit",
            "quote": content,
            "project_anchor": "纳沃伊光伏项目",
        }],
    }))
    assert out["Financing Model"] == "Unknown"
