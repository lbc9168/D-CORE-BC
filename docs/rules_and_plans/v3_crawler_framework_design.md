# v3 多源爬虫框架设计（草案，待用户过目）

> 日期：2026-07-16
> 状态：设计草案，用户确认后进入实施
> 关联：`v3_site_audit.md`（Tier-1 全部 6 站已圈定）；关键决策见 `docs/changes/2026-W29b.md`

## 一、目标与非目标

**试点阶段目标**（约 1 个月）：
1. 对 6 个 Tier-1 站做定时增量爬取，文章入本地库；
2. 产出健康监控报告（每站新增数/失败率/连续零更新告警）；
3. 攒够数据后量化：每站新增文章量、与 goalfore 的重叠率、解析成功率。

**明确不做（试点期）**：
- ❌ 不接下游 s01b（AI 提取）——爬取与提取解耦，先验证爬取稳定性与信息量，重叠率数据出来后再设计下游整合；
- ❌ 不动 s01a（goalfore 照爬，原样运行，作对照基线）；
- ❌ 不做 headless/WAF 站（sasac 等 Tier-2 留第二批）；
- ❌ 不回爬历史（只从部署日起向前增量；首跑抓列表前 N 页建立水位线即可）。

## 二、模块形态

- 新建独立子项目 **`s01c_multi_crawler`**（沿用 sNN 命名；与 s01a/s01b 平级，纯 Python + uv，不用 crewai——定时批任务不需要 agent 框架）。
- 运行形态：**无状态定时批任务**。`uv run crawl`（全部站）/ `uv run crawl --site seetao`（单站）/ `uv run health-report`。Windows 任务计划每周触发；未来迁服务器 cron 零改动。

## 三、架构：通用框架 + 每站适配器配置

```
s01c_multi_crawler/
├── config/sites/          # 每站一个 YAML（声明式适配器配置）
│   ├── yidaiyilu.yaml
│   ├── powerchina.yaml    # 大汉CMS 类型
│   ├── powerchina_intl.yaml
│   ├── imsilkroad.yaml
│   ├── seetao.yaml
│   └── solarbe.yaml
├── src/s01c_multi_crawler/
│   ├── fetcher.py         # 统一 HTTP 层：UA/限速/重试/状态码策略
│   ├── parsers/
│   │   ├── html_list.py   # 通用：服务端渲染列表页（CSS 选择器）
│   │   └── dahan_cms.py   # 专用：大汉CMS record 块解析（powerchina；日后 ceec 复用）
│   ├── state.py           # 状态库读写（seen_urls / 水位线 / fetch_log）
│   ├── health.py          # 健康报告生成
│   └── main.py            # CLI 入口
└── data/
    ├── crawl_state.db     # SQLite：状态 + 文章库
    └── logs/
```

**站点配置（YAML）声明什么**：列表页 URL 模板与分页规律（如 `?page={n}`）、最大翻页数、列表项选择器（链接/标题/日期）、正文选择器、解析器类型（html_list / dahan_cms）、限速间隔、栏目标签。加一个站 = 加一份 YAML（+必要时一个新解析器）。

**配置校验**：加载时用 Pydantic 校验（URL 格式、选择器非空、限速下限），非法配置立即抛错（CLAUDE.md 规则 4）。

## 四、数据模型（crawl_state.db）

- `articles`：url(PK)、site、column、title、publish_date、content_text、content_hash、fetched_at、status。**存正文纯文本供后续提取用，原文不对外发布**（版权约束）。
- `fetch_log`：run_id、site、ts、listing_pages_fetched、new_articles、errors、duration——健康报告的数据源。
- 增量判定：url 已存在即跳过（seen 集合）；列表页从第 1 页翻到"全部命中已见"即停（+安全上限）。幂等：重跑不产生重复行，批量写库走事务（规则 3）。

## 五、礼貌爬取与错误处理（硬约束）

- 同站请求间隔 ≥ 3 秒；UA 用真实浏览器标识+项目联系方式后缀；只爬 robots 允许的路径（6 站已核）。
- 状态码策略照 CLAUDE.md 规则 5：404 跳过不重试、429 等 ≥30s 重试、403 记日志并停该站（防触发对方封禁升级）、5xx 指数退避。
- 单站失败不影响其他站（站间隔离，逐站 try/记录）；全程 logging 入文件，UTF-8 硬保证（s01a 的 GBK 教训）。

## 六、健康监控（批任务的产物，不是常驻服务）

每次运行结束生成 `health_report_{date}.md`：
- 每站：本次新增 N 篇 / 抓取错误数 / 列表页选择器命中率（命中 0 = 站点改版信号，高亮告警）；
- 连续 ≥14 天零新增的站高亮（可能改版或被封）；
- 运行耗时与重试统计。
异常时退出码非 0（Windows 任务计划可据此发通知；服务器时代接邮件/webhook）。

## 七、试点验收指标（跑约 1 个月后评估）

1. 每站周均新增文章量（信息量）；
2. 与 goalfore 同期文章的重叠率（人工抽样 + 标题模糊匹配，粗估即可）——回答"新源的真实增量价值"；
3. 解析成功率 / 站点稳定性（错误率、是否被封）；
4. 由此决定：第二批站点名单、下游 s01b 整合设计、是否需要 headless 能力。

## 八、开放问题（用户可改）

1. 运行频率：默认**每周一次**（与月度增量节奏匹配；6 站列表页翻页足以覆盖一周增量）。想更密可改每日，成本差别不大。
2. 模块名 `s01c_multi_crawler` 可改。
3. 首跑水位线：默认抓每站列表前 5 页建立基线（不算回爬历史，只是让增量判定有起点）。
