"""S3 阶段测试：数值经 bri_parse 规范。"""
from s01b_project_extractor.stages.s3_finance import run_s3


class _FakeLLM:
    def __init__(self, p): self._p = p
    def complete(self, prompt, *, stage, json_mode=True):
        assert stage == "s3"; return self._p


def test_run_s3_parses_investment_number():
    out = run_s3("t", "c", "项目", llm=_FakeLLM({"Total Project Investment (Million USD)": "1,200", "Financing Model": "信贷融资"}))
    assert out["Total Project Investment (Million USD)"] == 1200.0
    assert out["Financing Model"] == "信贷融资"


def test_run_s3_empty_investment_kept_empty():
    out = run_s3("t", "c", "项目", llm=_FakeLLM({"Total Project Investment (Million USD)": "", "Financing Model": "Unknown"}))
    assert out["Total Project Investment (Million USD)"] == ""
