# 配置加载与校验测试：6 份真实 YAML 全部可加载，非法配置立即抛错
import pytest
from pydantic import ValidationError

from s01a2_multi_crawler.config import SiteConfig, load_site_configs


def test_load_all_site_configs():
    """config/sites 下 6 份 YAML 全部通过 Pydantic 校验"""
    configs = load_site_configs()
    names = {c.name for c in configs}
    assert names == {"yidaiyilu", "powerchina", "powerchina_intl", "seetao", "imsilkroad", "solarbe",
                     "cpnn", "nengyuanjie", "sasac"}
    assert {c.name for c in configs if c.tier == 2} == {"cpnn", "nengyuanjie", "sasac"}
    for c in configs:
        assert c.rate_limit_seconds >= 1.0
        if c.parser == "sitemap_daily":
            assert c.sitemap_index is not None
        else:
            assert c.listing_urls


def test_invalid_parser_rejected():
    """未知 parser 类型必须在入口抛错"""
    with pytest.raises(ValidationError):
        SiteConfig(name="x", label="x", parser="nope", article_url_pattern=".")


def test_unknown_site_rejected():
    """--site 指定不存在的站点名必须抛错"""
    with pytest.raises(ValueError):
        load_site_configs(only="不存在的站")
