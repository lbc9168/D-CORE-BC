# 网页可见项目中企身份、参与与融资证据只读审计

> **2026-08-22 口径修订：** 用户确认 Canadian Solar/阿特斯按本项目口径视为同一中企，CK Infrastructure（CKI）作为中国香港企业计入中企；银团贷款、银团放款、银行财团贷款归入 `Credit Financing`。本文第 3.1 节中关于 Canadian Solar 与 CKI 的原始法律主体讨论不再作为错误判定；更新后的逐项展示以 [171 条融资 HTML](./financing_audit_171_20260821.html) 为准。

**审计日期：** 2026-08-21；最终复核：2026-08-22
**数据截止：** 网页 `metadata.last_updated = 2026-07-30`
**模式：** 审计阶段只读；2026-08-22 按本文最终建议执行确定性修正，执行记录见第 7 节与 `docs/changes/2026-W34.md`

## 1. 结论摘要

本次按前端实际过滤规则复现了网页可见范围：主域 1,542 个项目、TX 312 个、MF 165 个，合计 **2,019 个项目**。对应 **3,721 条实体—事件关系**，全部可回溯到 `s01a raw_news`正文。

主要结论：

1. 确认存在“外国法律主体被当成中企”的映射问题，涉及 Canadian Solar Inc.、CK Infrastructure、DBS、POSCO、Deere 等。其中 `KOR-WD-0007` 和 `IDN-BM-0003` 可直接确认为项目级“没有中企证据却显示中方实体”。
2. 确认至少 8 组项目—事件存在“项目有中企，但显示成了另一家中企”的实体错配。这类不应删整个项目，而应更正具体实体关系。
3. `IDN-PV-0009` 的问题不只是实体：原文为米拉务 2×225MW 煤电项目，网页却将其建成 200kW 光伏项目，属于项目性质/分类错误。
4. 网页当前有 **171 个非 Unknown 融资项目**。最终逐项复核后，**78 条当前标签可保留，93 条应修正**。原“高风险”项已按用户确认规则收敛：有明确单一工具就降级为该工具，无明确工具则为 `Unknown`。
5. **36/36 条 Equity Financing 都没有项目资本金或股份认购等股权融资证据。** 其中 1 条另有明确银团贷款，建议 `Credit`；2 条为援助，建议 `Other`；其余 33 条建议 `Unknown`。
6. 56 条 Blended Financing 最终有 6 条支持；其余建议改为 36 条 `Credit`、1 条 `Bond`、2 条 `Other`、11 条 `Unknown`。

如严格执行“无明确证据即 Unknown”，171 条融资标签的建议结果为：

| 建议标签 | 数量 |
|---|---:|
| Credit Financing | 106 |
| Bond Financing | 2 |
| Blended Financing | 6 |
| Other | 8 |
| Unknown | 49 |
| Equity Financing | 0 |

## 2. 范围与方法

- 主域按 `DataContext.tsx::shouldExcludeByPolicy` 复现；TX/MF 按前端独立 domain switcher 的 2022+ 与国别排除口径复现。
- 将网页 `project_id` 反向追溯至 canonical project、event、source link、S01B 原始抽取行及 S01A 正文。
- 实体身份区分为：中国大陆法人、中国控制的境外子公司/平台、外国法律主体、未解决。
- 参与证据要求原文把该企业与该物理项目的中标、合同、EPC、供货、投资、运营等行为直接连接。背景提及、战略布局、表达兴趣、预资格/投标不等于已参与实施。
- 融资证据要求具体工具：贷款/信贷/授信/债务、债券、认购/增资/资本金投入、赠款/资助等。“融资支持”、“共同投资”、“持股”、“股权收购”不自动映射为具体融资工具。

## 3. 企业身份与“无中企却显示中企”

### 3.1 企业口径修订与确认的外国主体映射

| 实体 | 涉及项目 | 判断 | 建议 |
|---|---|---|---|
| Canadian Solar Inc. / 阿特斯 | 32 个：`BRA-PV-0024, AUS-PV-0011, CHL-HB-0007, MEX-PV-0003, JPN-PV-0010, USA-PV-0006, JPN-PV-0008, USA-ES-0006, AUS-ES-0009, GBR-ES-0016, GBR-ES-0015, ESP-PV-0012, BRA-PV-0014, BRA-PV-0013, JPN-ES-0004, JPN-PV-0006, USA-ES-0005, CAN-ES-0003, ESP-PV-0009, BRA-PV-0004, AUS-ES-0011, CHL-ES-0004, USA-HB-0002, USA-HB-0001, GBR-ES-0007, CHL-ES-0003, DEU-ES-0002, CAN-ES-0002, AUS-ES-0002, USA-ES-0002, USA-ES-0001, USA-MF-0005` | 用户确认 Canadian Solar 与阿特斯按本数据库研究口径视为同一中企体系。 | 保留为中企，不因加拿大上市/注册信息移除；仍尽量展示原文中的精确法律主体。 |
| CK Infrastructure Holdings Limited | `GBR-WD-0005, GBR-HB-0004, GBR-TX-0001` | 用户确认中国香港企业计入本数据库的中企口径。 | 保留为中企，并在可能时保留香港法律主体层级。 |
| DBS Bank (China/Hong Kong) | `GBR-ES-0021, UZB-WD-0009, LAO-PV-0006` | DBS 集团总部和上市地均在新加坡；中国/香港机构不改变其最终控制。 | 从“中企”映射中移除，仍可作为外国融资机构展示。 |
| POSCO | `KOR-WD-0007` | 韩国企业；原文为 Equinor、POSCO E&C、LS Cable，无中企。 | 确认项目级错误：从中企参与数据中移除/转人工复核。 |
| Deere & Company | `IDN-BM-0003` | 美国 Delaware 法人，总部在 Illinois；当前唯一实体为 Deere。 | 确认项目级错误：从中企参与数据中移除/转人工复核。 |

注：Canadian Solar/阿特斯与 CKI 的保留是本项目研究口径决定，不等于否认其境外注册、上市或法律主体层级；网页仍应尽量显示原文中的精确参与主体。

### 3.2 中国控制的境外子公司/平台

以下不应因英文名称或境外注册地判为纯外企，但应保留“境外法人 + 中国最终控制方”两层信息：

- United Energy Group Limited：中国控制的香港上市/境外平台，涉及 `IRQ-HB-0017, BGR-PV-0003, ROU-PV-0012`。
- Vensys Energy AG：德国法人，但属金风科技体系，涉及 `KOR-WD-0013, KOR-WD-0016`。
- Orpower TwentyTwo / Kaishan Renewable Energy Development：开山集团境外项目公司，涉及 `KEN-GT-0001`。
- SPIC Brasil：国家电投巴西子公司，涉及 `BRA-PV-0025`。
- Goldwind International Holdings (Hong Kong)：金风科技境外平台，涉及 `UZB-WD-0022, UZB-WD-0014, THA-WD-0001`。
- Jietai New Energy Investment (Hong Kong)：钧达股份香港子公司，涉及 `TUR-MF-0003`。
- XSD International Paper Sdn. Bhd.：马来西亚法人，为浙江新胜达体系境外公司，涉及 `MYS-BM-0005`。
- EICO / Energy and Infrastructure Company：中材国际控股子公司；`SAU-TX-0001` 原文又明确其与特变电工签署 Namria 380kV 变电站合同，应保留。

### 3.3 项目级无中企/未解决清单

| 项目 | 状态 | 原因 |
|---|---|---|
| `KOR-WD-0007` | `confirmed_error` | 只有 Equinor、POSCO E&C、LS Cable，无中企。 |
| `IDN-BM-0003` | `confirmed_error` | 当前唯一实体是美国 Deere & Company。 |
| `DEU-TX-0004` | `high_risk` | NeuConnect 投资者为 Meridiam、Allianz Capital Partners、关西电力、东京电力；页面实体为 `Unknown`，未见中企。 |
| `LAO-TX-0026` | `high_risk` | 原文只明确老挝国家输电网公司 EDL-T，页面实体为 `Unknown`，未给明确中企。 |
| `ZAF-TX-0001, ZAF-TX-0002` | `high_risk` | 国家电网/南方电网当时是意向方、投标/预资格参与方，不能展示为已实施项目。 |
| `IDN-TX-0012` | `mapping_missing` | 原文有“中国企业青山绿水”投资建设，不是无中企；当前是映射缺失。 |

## 4. “项目有中企，但显示错了中企”

| 项目 / 事件 | 当前错配 | 原文支持的实体 | 来源 |
|---|---|---|---|
| `ZAF-PV-0001-01` | 中国能建/国际建设 | 中国电建 | https://news.goalfore.cn/topstories/detail/92831.html |
| `ZAF-PV-0001-02` | 中国能建装备 | 中国电建贵州工程公司 EPC | https://news.goalfore.cn/topstories/detail/43669.html |
| `THA-HB-0001-01` | 东方电气/广重 | 广州工控旗下广重集团 | https://news.goalfore.cn/topstories/detail/92660.html |
| `COD-HB-0002-01` | 中国能建装备 | 十五冶对外工程武黄公司 | https://news.goalfore.cn/topstories/detail/92049.html |
| `ZAF-HB-0001-01` | 中国能建国际 | 中国电建水电十一局 | https://news.goalfore.cn/topstories/detail/91890.html |
| `VNM-ES-0001-01` | 中国能建装备 | 金风科技 + GG Power | https://news.goalfore.cn/topstories/detail/94469.html |
| `ARE-HB-0015-01` | 中电工程 | 中国电建 EPC | https://news.goalfore.cn/topstories/detail/93593.html |
| `HRV-PV-0003-03/-04` | 中国能建国际 | 北方国际 + 国家电投山东院 | https://news.goalfore.cn/topstories/detail/93312.html |

高风险但不建议直接删整个项目：

- `LKA-WD-0001-01`：页面显示三一，所挂新闻实为斯里兰卡 BESS 项目，曼纳尔风电只是背景且指向远景；未见三一直接参与证据。来源：https://news.goalfore.cn/topstories/detail/92922.html
- `ZWE-HB-0001-01`：迈越阳光直接签约；华西能源只以“战略布局/体系内协同”出现，不足以证明其是项目承包或投资主体。来源：https://news.goalfore.cn/topstories/detail/92767.html
- `SAU-HB-0024`：中国能建来源实际指向 Al Shuaibah PV，但 NEOM 储能项目本身有华为/山东电建三公司的中企参与证据；属于来源错链，不是无中企。

## 5. 马来西亚专项结果

- 当前前端可见 MYS 项目为 **41 个**。
- 彭亨州 2,000MW 光伏 / Citaglobal 误认为中企的记录已不在当前 41 个可见项目中。
- `MYS-BM-0005` 的 XSD International Paper 是马来西亚法人，但已确认为浙江新胜达体系境外公司，不是 Citaglobal 式的纯外企误认。
- `MYS-PV-0011, MYS-PV-0012, MYS-PV-0013, MYS-BM-0001` 的 Weaton Holdings/威顿控股项目：原文能证实项目合作，但仍缺可核验的中国注册主体、股权或最终控制人证据，建议标为 `unresolved`。
- `MYS-HY-0008` 泰豪国际、`MYS-PV-0005` 名洋能源从企业背景看属中国企业，但建议在发布级证据中补公司注册/官网证据。
- `MYS-PV-0030` 的当前来源是一条无法稳定重新打开的澎湃转载；暂未反证中国能建/江苏院身份，但发布前应补可访问原始来源。
- 41 项中仅 `MYS-HY-0001` 显示非 Unknown 融资标签；原始材料有明确银行/贷款证据，`Credit Financing` 可保留。

## 6. 融资证据总表

| 当前标签 | supported | confirmed_error | high_risk | 合计 |
|---|---:|---:|---:|---:|
| Credit Financing | 69 | 7 | 0 | 76 |
| Bond Financing | 1 | 0 | 0 | 1 |
| Equity Financing | 0 | 36 | 0 | 36 |
| Other | 2 | 0 | 0 | 2 |
| Blended Financing | 6 | 50 | 0 | 56 |
| **合计** | **78** | **93** | **0** | **171** |

### 6.1 Equity Financing：36 条全部不支持当前标签

下表中 `Other` 是可明确识别的资助/援助；`AUS-WD-0002` 另有项目级银团融资协议，故改 `Credit`；其余按严格口径建议 `Unknown`。

| 项目 | 原文实际证据 | 建议 | 来源 |
|---|---|---|---|
| `AUS-WD-0002` | 股权收购不是股权融资；另有明确银团融资协议 | **Credit** | https://news.goalfore.cn/topstories/detail/12965.html |
| `IDN-HY-0015` | 收购股权、完成交割 | Unknown | https://news.goalfore.cn/topstories/detail/23365.html |
| `HRV-WD-0001` | 收购 76% 股权 | Unknown | https://news.goalfore.cn/topstories/detail/5725.html |
| `BGD-WD-0001` | 只称“作为投资方之一” | Unknown | https://news.goalfore.cn/topstories/detail/25434.html |
| `IDN-HB-0037` | BOO / 子公司持股 35% | Unknown | https://news.goalfore.cn/topstories/detail/28359.html |
| `TTO-PV-0001` | 欧盟/GCCA+ 资助 | **Other** | https://news.goalfore.cn/topstories/detail/30711.html |
| `KAZ-WD-0033` | 控股权交割 | Unknown | https://news.goalfore.cn/topstories/detail/35053.html |
| `MEX-HB-0003` | 并购 | Unknown | https://news.goalfore.cn/topstories/detail/35053.html |
| `ESP-PV-0007` | 股权转让 | Unknown | https://news.goalfore.cn/topstories/detail/37536.html |
| `AUS-ES-0012` | 收购 | Unknown | https://news.goalfore.cn/topstories/detail/38242.html |
| `PAK-HY-0008` | 无该项目股权融资证据 | Unknown | https://news.goalfore.cn/topstories/detail/39521.html |
| `SRB-WD-0007` | acquiring 51% of project | Unknown | https://news.goalfore.cn/topstories/detail/39588.html |
| `SRB-WD-0011` | 拟收购 51% 股权 | Unknown | https://news.goalfore.cn/topstories/detail/39631.html |
| `UZB-HB-0024` | 外国直接投资，无具体工具；同一仪式的组合层面提到资金和技术援助，未单独归属本项目包 | Unknown | https://news.goalfore.cn/topstories/detail/76209.html |
| `SRB-WD-0004` | 持股 51% + 出口信用保险 | Unknown | https://news.goalfore.cn/topstories/detail/48724.html |
| `PER-HY-0001` | 中拉产能合作基金，无工具性证据 | Unknown | https://news.goalfore.cn/topstories/detail/83478.html |
| `UZB-HB-0025` | 中国能建“投资” | Unknown | https://news.goalfore.cn/topstories/detail/76201.html |
| `ZAF-PV-0011` | 中非发展基金为投资方 | Unknown | https://news.goalfore.cn/topstories/detail/75059.html |
| `UZB-PV-0020` | 1.5 亿美元 FDI，无具体工具 | Unknown | https://news.goalfore.cn/topstories/detail/66157.html |
| `UZB-WD-0005` | 中国政府出资援建 | **Other** | https://news.goalfore.cn/topstories/detail/86111.html |
| `UZB-HB-0029` | “直接投资” | Unknown | https://news.goalfore.cn/topstories/detail/73329.html |
| `BIH-PV-0003` | 收购项目公司 80% 股权 | Unknown | https://news.goalfore.cn/topstories/detail/73629.html |
| `UZB-HB-0023` | 外国直接投资；同一仪式的组合层面提到资金和技术援助，未单独归属本项目包 | Unknown | https://news.goalfore.cn/topstories/detail/76209.html |
| `UZB-PV-0014` | 德国公司主导投资，非中企股权融资 | Unknown | https://news.goalfore.cn/topstories/detail/76213.html |
| `BIH-PV-0002` | 拟收购 80% 股份 | Unknown | https://news.goalfore.cn/topstories/detail/82987.html |
| `SRB-WD-0002` | 收购 | Unknown | https://news.goalfore.cn/topstories/detail/88947.html |
| `UZB-BM-0003` | “已投入”，无明确股权工具 | Unknown | https://news.goalfore.cn/topstories/detail/85527.html |
| `IDN-GT-0003` | 收购剩余 5% 股权 | Unknown | https://news.goalfore.cn/topstories/detail/85822.html |
| `ROU-PV-0005` | 无融资工具 | Unknown | https://news.goalfore.cn/topstories/detail/88343.html |
| `ROU-PV-0006` | 无融资工具 | Unknown | https://news.goalfore.cn/topstories/detail/88343.html |
| `ROU-HB-0001` | 100% 股权收购 | Unknown | https://news.goalfore.cn/topstories/detail/88810.html |
| `ROU-HB-0004` | 股权转让 | Unknown | https://news.goalfore.cn/topstories/detail/88947.html |
| `BRA-PV-0003` | 股权收购 | Unknown | https://news.goalfore.cn/topstories/detail/89708.html |
| `PHL-PV-0006` | ACCIONA 全资持有，中企为 EPC | Unknown | https://news.goalfore.cn/topstories/detail/91076.html |
| `MWI-HB-0001` | 只称股权合作 | Unknown | https://news.goalfore.cn/topstories/detail/92442.html |
| `KAZ-HB-0018` | 只称共同投资开发 | Unknown | https://news.goalfore.cn/topstories/detail/94697.html |

### 6.2 Credit Financing：7 条需处理

| 项目 | 状态 | 建议 | 原因 / 来源 |
|---|---|---|---|
| `CAF-PV-0001` | confirmed_error | Other | 世界银行官方为 IDA grant，而非贷款。https://www.worldbank.org/en/news/press-release/2023/11/17/with-the-support-of-the-world-bank-a-new-solar-park-in-the-central-african-republic-expands-access-to-clean-energy |
| `NER-HB-0003` | confirmed_error | Other | 只称伊斯兰发展银行资助/出资，无贷款工具。https://news.goalfore.cn/topstories/detail/59903.html |
| `TUR-HB-0006` | confirmed_error | Unknown | 只称协助筹集约 3 亿美元融资。https://news.goalfore.cn/topstories/detail/60563.html |
| `MDV-ES-0001` | confirmed_error | Unknown | 只称 ADB 提供融资支持。https://news.goalfore.cn/topstories/detail/81131.html |
| `NER-ES-0001` | confirmed_error | Unknown | 只称世界银行出资。https://news.goalfore.cn/topstories/detail/75480.html |
| `TZA-PV-0002` | confirmed_error | Unknown | 只称世界银行提供资金支持。https://news.goalfore.cn/topstories/detail/90452.html |
| `BGR-PV-0004` | confirmed_error | Unknown | 只称 IFC 提供融资支持。https://news.goalfore.cn/topstories/detail/71274.html |

其余 69 条 Credit Financing 能找到贷款、授信、银团、出口买方信贷等明确工具证据。

### 6.3 Blended Financing：最终 6 条支持

可保留：

| 项目 | 证据 |
|---|---|
| `BRA-PV-0005` | 自有资本金 + BNDES/绿色贷款 |
| `ARG-PV-0013` | 中国进出口银行融资 + 胡胡伊省债券 |
| `AZE-PV-0005` | 业主自有资本金 + 银行/银团融资 |
| `GBR-WD-0004` | 22 家银行项目融资 + 红石能源、ESB 按股比后续出资承诺 |
| `BRA-PV-0028` | BNDES 融资 + 中国电建承担剩余项目出资 |
| `UZB-HB-0005` | 增资设立项目公司 + 自有资金/银行贷款 |

26 条可直接确认当前 Blended 不成立：

| 项目 | 建议 | 项目 | 建议 |
|---|---|---|---|
| `ARE-HB-0007` | Unknown | `IDN-PV-0014` | Credit |
| `PAK-HY-0004` | Credit | `PAK-HY-0003` | Credit |
| `AZE-PV-0008` | Credit | `MNG-HB-0002` | Credit |
| `UZB-WD-0027` | Credit | `KAZ-PV-0003` | Credit |
| `BIH-WD-0002` | Credit | `AUS-ES-0018` | Other |
| `SAU-PV-0022` | Credit | `USA-HB-0005` | Credit |
| `ZAF-PV-0016` | Credit | `BHR-HB-0007` | Credit |
| `BGD-PV-0007` | Credit | `USA-ES-0005` | Credit |
| `BRA-PV-0004` | Credit | `BGD-PV-0006` | Credit |
| `COG-PV-0001` | Credit | `KAZ-WD-0008` | Credit |
| `SAU-WD-0001` | Credit | `BGR-ES-0002` | Other |
| `OMN-HB-0003` | Credit | `ARE-PV-0001` | Credit |
| `SLB-HB-0001` | Credit | `KAZ-BM-0002` | Unknown |

27 条复核结论（不再保留 `high_risk` 中间状态）：

| 项目 | 最终建议 | 核心证据或边界 | 来源 |
|---|---|---|---|
| `ARE-HB-0010` | Unknown | 来源是施工合同，无具体融资工具 | https://news.goalfore.cn/topstories/detail/38852.html |
| `KEN-GT-0001` | Credit | IFC 与非洲开发银行组成银团贷款 | https://news.goalfore.cn/topstories/detail/92197.html |
| `ARE-PV-0002` | Bond | 明确发行 8.7 亿美元绿色债券；不足以证明同一事件存在第二类工具 | https://news.goalfore.cn/topstories/detail/92027.html |
| `VNM-PV-0020` | Credit | 专项授信、银行通道融资及应收账款 ABS 均属债务侧工具 | https://news.goalfore.cn/topstories/detail/15235.html |
| `ARE-PV-0006` | Credit | 优先债务、夹层贷款、传统及伊斯兰贷款 | https://news.goalfore.cn/topstories/detail/32064.html |
| `GBR-WD-0004` | Blended | 项目融资银团 + 股东按股比后续出资承诺 | https://news.goalfore.cn/topstories/detail/72805.html |
| `VNM-WD-0020` | Credit | AIIB 贷款、商业银行贷款及首笔银团放款 | https://news.goalfore.cn/topstories/detail/25054.html |
| `SAU-PV-0056` | Credit | Equity Bridge Loan 仍属于贷款 | https://news.goalfore.cn/topstories/detail/34499.html |
| `BRA-PV-0025` | Unknown | co-invest 是组合层面合作，无本项目具体工具 | https://news.goalfore.cn/topstories/detail/29055.html |
| `BRA-PV-0030` | Unknown | 738MW 两项目组合收购，与当前项目/容量不一致 | https://news.goalfore.cn/topstories/detail/29101.html |
| `BRA-PV-0031` | Unknown | 同上 | https://news.goalfore.cn/topstories/detail/29101.html |
| `SAU-HB-0019` | Credit | 多家中外银行签署项目融资协议 | https://news.goalfore.cn/topstories/detail/33311.html |
| `UZB-WD-0009` | Credit | 过桥贷款明确；股权收购不另构成 Equity Financing | https://news.goalfore.cn/topstories/detail/77649.html |
| `SAU-HB-0024` | Credit | 无追索权项目融资属于债务侧工具 | https://news.goalfore.cn/topstories/detail/50090.html |
| `SAU-PV-0054` | Credit | 国家发展基金贷款 + 银行财团商业贷款 | https://news.goalfore.cn/topstories/detail/50090.html |
| `SAU-PV-0055` | Credit | Equity Bridge Loan；保险不另构成融资类别 | https://news.goalfore.cn/topstories/detail/50090.html |
| `SAU-PV-0053` | Credit | Equity Bridge Loan + 无追索项目融资，均属债务侧 | https://news.goalfore.cn/topstories/detail/52666.html |
| `BRA-PV-0014` | Unknown | 债券/贷款可能指向 Ciranda Cluster，项目边界不足 | https://news.goalfore.cn/topstories/detail/63323.html |
| `UZB-HB-0031` | Credit | EBRD A 级及 B 级均为贷款 | https://news.goalfore.cn/topstories/detail/68302.html |
| `AZE-WD-0002` | Unknown | 只称技术与资金由沙特与中国提供 | https://news.goalfore.cn/topstories/detail/91681.html |
| `BRA-PV-0028` | Blended | BNDES 融资 + 中国电建承担剩余项目出资 | https://news.goalfore.cn/topstories/detail/70970.html |
| `NPL-HY-0004` | Credit | 中国进出口银行人民币跨境贷款已拨付 | https://news.goalfore.cn/topstories/detail/86355.html |
| `UZB-HB-0005` | Blended | 增资设立项目公司 + 自有资金/银行贷款 | https://news.goalfore.cn/topstories/detail/73847.html |
| `AUS-ES-0005` | Unknown | 中企为供应商/协助融资，无具体工具 | https://news.goalfore.cn/topstories/detail/80634.html |
| `UZB-PV-0001` | Unknown | “投资建设”是泛称 | https://news.goalfore.cn/topstories/detail/82346.html |
| `ECU-HY-0001` | Credit | 中国进出口银行 17 亿美元贷款明确 | https://news.goalfore.cn/topstories/detail/87040.html |
| `ZWE-PV-0001` | Unknown | EP+F 只泛称融资，未给具体工具 | https://news.goalfore.cn/topstories/detail/84548.html |

### 6.4 Bond 与 Other

- `JPN-PV-0008`：原文明确绿色项目债券，Bond Financing 可保留。
- 当前 2 条 Other 均有资助/基金类明确证据，可保留。

### 6.5 银团融资、援助与乌兹别克斯坦重复复核（2026-08-22）

**银团融资。** 原建议为 `Unknown` 的记录中，4 条原文明示银团或银行财团：`KEN-GT-0001`、`GBR-WD-0004`、`VNM-WD-0020`、`SAU-PV-0054`。其中肯尼亚、越南、沙特三项只有债务侧工具，改 `Credit`；英国 Inch Cape 同时有项目融资银团和股东后续出资承诺，保留 `Blended`。这四条不再计入 `Unknown`。

**援助检索。** 69 条原建议 `Unknown` 中，有 2 条数据库记录在同一篇来源中命中“资金和技术援助”：`UZB-HB-0023`、`UZB-HB-0024`。按记录数为 2，按独立来源为 1。原文把该援助表述用于当日启动项目的整体，并未把具体援助工具分别归属到 2.3GW 与 2.5GW 两个项目包，因此本轮不直接改 `Other`，两条融资标签仍为 `Unknown`。`ECU-HY-0001` 出现的美国陆军工程兵团“技术援助”属于河流侵蚀治理背景，不是电站融资，未计入。

**重复项目复核。** `UZB-HB-0023` 与 `UZB-HB-0024` 为 `partial overlap`，不是 exact duplicate，不合并：

| 项目 | 原文边界 | 处理 |
|---|---|---|
| `UZB-HB-0024` | 布哈拉、纳沃伊、纳曼干和塔什干等地五座光伏/风电站，约 2.3GW；另有安集延、费尔干纳 300MW 储能 | 保留 canonical 与 `-01` 事件 |
| `UZB-HB-0023` | 费尔干纳、撒马尔罕、纳沃伊、塔什干州和塔什干市六座电站，2.5GW、35 亿美元 | 保留 canonical 与 `-01` 事件；当前“水电”类别缺乏原文支持，单列人工分类复核 |

两条共享同一总统启动仪式和部分地名，但项目数量、容量和地域组合不同。共同来源中的“中国能源、中国大唐”等合作伙伴也没有逐一对应到五座或六座具体电站，因此不能因为参与方相同而合并。`UZB-HB-0024-02` 的 Masdar 全国性框架与 `-01` 可能存在框架—落实关系，但不足以与 `0023` 合并，也不足以把中企参与证据延伸到该事件。

## 7. 修正优先级与执行结果（2026-08-22）

1. **P0：项目级无中企/分类错误——已执行确定项。**
   `KOR-WD-0007, IDN-BM-0003, IDN-PV-0009` 已在 canonical 与对应上游提取行标记排除；没有删除项目事实。`DEU-TX-0004, LAO-TX-0026, ZAF-TX-0001, ZAF-TX-0002` 仍保留人工复核，不自动排除。
2. **P0：法律实体国别/控制方映射——已执行可确定项。**
   按用户口径保留 Canadian Solar/阿特斯与 CKI；从 `chinese_entities` 移除 5 条 DBS 中国/香港机构关系，因为 DBS 最终属于新加坡银行集团。EICO、Vensys、SPIC Brasil、XSD 等中国控制的境外子公司未误删。
3. **P1：实体—项目错配——已执行可确定项。**
   删除 5 条已有正确实体替代的错误关系，另将 5 条关系改为原文支持的 PowerChina、广州工控/广重、中国有色/十五冶、金风科技、北方国际；上游错误提取行同步排除或改正。明确 EPC 证据仍写 `EPC总承包`，不会因缺少融资证据把 involvement type 降为 `Unknown`。
4. **P1：融资标签——已执行。**
   93 个确认错误项目、100 条精确 `project_events` 已按第 6 节修改；其中 79 个无歧义事件映射到上游 180 条 `extracted_projects` 并同步修改。21 个同源多项目/项目包事件没有对上游做 URL 级批改，以避免连带修改其他项目，但 canonical 事件已按 `event_id` 精确修正。171 条最终分布为 106 Credit、2 Bond、6 Blended、8 Other、49 Unknown。
5. **P2：马来西亚补证——未越权执行。**
   Weaton 四项、泰豪国际、名洋能源、`MYS-PV-0030` 仍保持人工复核；没有在证据不足时自动发布或删除。

执行脚本：`scripts/oneoff/fix_full_audit_20260822.py`。执行前分别备份 canonical 与上游提取库；修正后离线重建 s03/s04，171 条审计项目与网页融资标签逐项对比为 0 处不一致。

## 8. 限制

- 3,721 条关系均已做数据链回溯；但对约 824 个唯一实体的外部身份核验采用“受控登记/映射表 + 高风险外观实体逐项官方源复核”，不是对每个普通中文公司名都重复做商业登记调查。
- 初审中的 `high_risk` 表示当时发布证据不足，不表示真实世界一定不存在后续协议；融资项已按用户确认的严格发布规则收敛，实体身份的未解决项仍保持人工复核。
- 本报告不修改 Prompt；2026-08-22 已按第 7 节执行确定性数据修正。审计 HTML 保留“修正前当前标签 → 最终建议标签”的快照，网页数据则已经重建为修正后的结果。
