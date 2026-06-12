# S01b 新闻筛选与结构化提取 — 逻辑流程图

> 源码：`src/s01b_project_extractor/tools/project_extractor_tool.py`（核心）、`main.py`（入口）、`guardrails/`（质量校验）

## 总览：三道筛子 + 一道质量门

S01b 把 s01a 爬来的新闻逐条「过筛 → AI 结构化 → 分三类入库」，crew 路径额外再过一道 guardrail 质量门。

```mermaid
flowchart TD
    Start([s01a: raw_news.db<br/>passed_keyword_filter=1]) --> Entry{入口}

    Entry -->|run_bulk_extract<br/>按 batch_id 并发| Tool
    Entry -->|crew kickoff<br/>单批| Tool

    subgraph Tool["ProjectExtractorTool._process_df（每批）"]
        direction TB
        L1[读取本批文章<br/>DataFrame] --> Dedup[加载已处理 URL<br/>projects.db + review.db]
        Dedup --> FillDate[publish_date 前向填充<br/>顺序依赖，并发前完成]
        FillDate --> Pool[ThreadPool 4 并发<br/>逐篇 _process_article]
    end

    Pool --> Article

    subgraph Article["_process_article（单篇）"]
        direction TB
        A0{URL 已处理?} -->|是| Askip[跳过] 
        A0 -->|否| A1{Python 关键词预筛<br/>含光伏/风电/储能/MW…?}
        A1 -->|不含| Adisc1[→ discarded<br/>'Python关键词预筛不匹配']
        A1 -->|含| A2[构建 prompt<br/>注入母子公司表/央企名录/枚举]
        A2 --> A3[调用 Gemini 2.5-flash-lite<br/>JSON 模式 temp=0.14<br/>失败重试3次指数退避]
        A3 --> A4{解析失败?}
        A4 -->|是| Adisc2[→ discarded<br/>'程序异常']
        A4 -->|否| A5{AI status}
        A5 -->|irrelevant| Adisc3[→ discarded<br/>含 AI reasoning]
        A5 -->|no_chinese_participation| Anc[→ no_chinese]
        A5 -->|valid_project| Aproj
    end

    subgraph Aproj["valid_project: 逐项目 × 逐中方实体 后处理"]
        direction TB
        P1[国家名 → ISO/BRI/LDC<br/>_get_country_meta] --> P2[母公司规范化<br/>空值→Unknown]
        P2 --> P3[是否央企 查表<br/>hierarchy→soe.md→0]
        P3 --> P4[中→英名 查缓存<br/>未命中调 Gemini 翻译并回写]
        P4 --> P5[数值字段拆 raw+REAL<br/>bri_parse.parse_number]
        P5 --> Pvalid[→ valid_rows]
    end

    Askip --> Write
    Adisc1 --> Write
    Adisc2 --> Write
    Adisc3 --> Write
    Anc --> Write
    Pvalid --> Write

    subgraph Write["事务写库"]
        direction TB
        W1[(projects.db<br/>extracted_projects<br/>有效项目)]
        W2[(review.db<br/>no_chinese_records<br/>+ discarded_records)]
        W3[All_AI_Results_*.json<br/>原始结果留档<br/>入库失败有兜底 dump]
    end

    Write -->|仅 crew 路径| Guard
    Write -->|bulk 路径| Done([完成])

    subgraph Guard["validate_extraction 质量门（最多重试3次）"]
        direction TB
        G2[Layer2 语义校验<br/>独立模型 DeepSeek/Gemini<br/>纠偏 stage/category/involvement<br/>仅 confidence≥0.8 自动应用] --> G1[Layer1 格式校验<br/>必填/枚举/中文名/ISO/日期/容量范围]
        G1 --> G3[Layer3 批次统计校验<br/>有效率/分类集中度/容量国家缺失]
        G3 --> Gd{有硬错误?}
        Gd -->|是| Gdel[删除不合格记录<br/>反馈错误 → Agent 重试]
        Gd -->|否| Gpass[通过 ✓]
    end

    Gdel -.重试.-> Tool
    Gpass --> Done
```

## 关键设计点

| 环节 | 机制 | 目的 |
|------|------|------|
| 增量去重 | 启动时载入 projects.db+review.db 已处理 URL，并发内 `with _lock` 原子占位 | 跨批/并发不重复调 API |
| Python 预筛 | 正文必须含能源关键词才进 AI | 省 Gemini 调用 |
| 日期前向填充 | 并发前顺序补空 publish_date | 闭包并发无法保证顺序 |
| AI 三分类 | `status` = valid / no_chinese / irrelevant | 一次调用同时完成筛选+结构化 |
| Python 后处理 | 央企判定、中英名、ISO/BRI/LDC、数值解析都在代码端 | 确定性，不靠 LLM |
| 质量门三层 | 语义(模型)→格式(纯Py)→统计(纯Py) | 仅 crew 路径生效；bulk 路径不过门 |

> ⚠️ 注意：`run_bulk_extract` 直接调 `ProjectExtractorTool._run`，**绕过了 crew 的 guardrail**。只有 `run()`/`crew().kickoff()` 路径才会触发 `validate_extraction` 质量门。
