# 企业主表结构与证据审阅记录 — 2026-09-17

> 本文件保留第一轮结构审阅快照。随后已对66项待核记录联网补查；中矿资源、Recurrent等早期未决结论已更新，当前结果以[联网补查报告](entity_webcheck_2026-09-17.md)为准。

工作区：`E:/Projects/BRI_RE_Database_v2/.codex/worktrees/entity-identity-repair`。
范围：151 个原有集团、442 条原有子主体记录、集团别名及75条developer登记记录的全表结构和语义审阅；对下列明确问题进行免费的公开一手来源核对。**这不等于所有企业及母子关系均已重新核实。** 未调用付费模型/API，未读写生产数据库，未回填历史记录。

## 结果与覆盖

- groups: 151
- subsidiary_records: 444
- group_aliases: 142
- sub_aliases: 5
- group_only_aliases: 42
- disputed_aliases: 1
- unresolved_subjects: 23
- unique_persistent_ids: 527
- direct_parent_records: 3
- groups_without_legacy_sources: 6
- registry_companies: 75

逐记录清单：[entity_master_audit_2026-09-17.json](entity_master_audit_2026-09-17.json)。清单记录每条原文名/法定名、ID、集团、直接母公司、别名状态和本次事实来源；fresh_checked_facts 为空表示仅保留旧表，不能声称本次已核实。旧 sources 的数量只表明有链接，不证明链接真实有效或支持该条所有事实。

## 已核实的具体事实和修改

| 对象 | 修改及边界 | 一手来源 |
|---|---|---|
| 宁德时代 | 增加宁德时代/CATL同主体简称；不自动扩展到任意名称含CATL的海外公司 | [公司简介](https://www.catl.com/about/profile/) |
| 中国能建 | 官网及历史集团材料同时使用该简称；只确认集团层级，不能断定新闻指集团法人还是上市股份公司，设group_only_alias | [上市主体简介](https://www.ceec.net.cn/col/col52617/index.html)、[集团材料](https://www.ceec.net.cn/module/download/down.jsp?colID=43953&i_ID=2525309) |
| 北方国际 | 加入北方国际/NORINCO International及正式英文名；实际主体为北方国际合作股份有限公司。其直接控股母公司记录为中国北方工业有限公司，保留兵器工业集团归组；关系依据截至2024年末，不外推所有其他子公司层级 | [公司2024年报第5–6页、股东部分](https://static.cninfo.com.cn/finalpage/2025-04-01/1222975365.PDF) |
| 海油工程 | 从中海油集团的同法人alias移为海洋石油工程股份有限公司子主体；直接母公司和集团均中海油 | [企业官网](https://www.cnoocengineering.com/) |
| 中信银行 | 从集团同法人alias移为中信银行股份有限公司；保留中信集团关系，直接母公司暂缺，不能根据集团关系跳过中间控股层 | [公司FAQ](https://www.citicbank.com/common/question/investor/)、[2025年报释义](https://www.citicbank.com/about/investor/financialaffairs/report/2025/202604/P020260422359345157414.pdf) |
| 中水对外 | 实际主体为中国水利电力对外有限公司；三峡→中交发生在2023年，当前主表按中交归组并保存交接来源，删除过时的三峡alias。**不据此改写2023年前项目的历史集团归属** | [三峡交接公告，发布2023-11-14](https://www.ctg.com.cn/sxjt/xwzx55/jtyw44/2024081104160029564/index.html)、[公司2024年披露](https://www.cwe.cn/xwzx/jtyw/202409/t20240907_266834.html)、[中交2023年报关联关系](https://www.ccccltd.cn/tzzgx/dqbg/yjbga/2023/202404/P020240402532333451265.pdf) |
| Recurrent Energy | 其母公司Canadian Solar Inc.不等于CSI Solar（阿特斯阳光电力集团股份有限公司）；移除错误的CSI Solar同法人/父子关系。**保留用户历史研究口径纳入Canadian Solar的决定**。旧registry的false不作为最终裁决，设置identity_status=needs_review，LLC作为review_names，不冒充已确认别名；不自动排为外企 | [公司官网](https://recurrentenergy.com/)、[Canadian Solar 2025年20-F](https://www.sec.gov/Archives/edgar/data/1375877/000110465926041976/csiq-20251231x20f.htm) |
| 中矿资源 | 官网确认名称中矿资源集团股份有限公司；没有核实其属于旧表“中色矿业集团有限公司”。把该alias标为disputed，不继承此集团或央企身份。旧集团本身及实际控制权仍待专门核实，未直接删改 | [公司介绍](https://sinomine.cn/zjzkn.html) |

## 未决问题的保留方式

- `group_only_aliases` 保留旧集团归属，但不把子公司、品牌、分公司、笼统集团简称指向集团法人ID。实例包括中电国际、国网宁波供电公司、e-STORAGE、远景储能、川投国际。该标记是语义审阅裁决，不是对全部历史集团关系重新事实核实。
- `legal_entity_status=needs_review` 用于缺法定名或旧表明显填了业务部门简称的子条目，例如上海电气“电站集团/中央研究院”、正泰新能源等。保留已登记集团，中企判断可沿集团；actual entity_id 为空，等待补录。
- 中色矿业/中矿资源关系为 disputed，比group-only更弱；不赋正式集团ID。Recurrent研究身份待核实，既不武断非中企，也不把旧错误母子关系当作确认。
- 中能建国际相关旧称、葛洲坝集团与股份公司、黄河水电前后名称、哈尔滨电气集团与股份公司、三峡国际不同旧名等存在主体或历史日期疑点。保留精确原名、对有歧义的尾缀只确认共同集团；不靠字典序挑选法人。历史工商变更/控股变动的日期模型尚未实现。
- 大部分旧子记录只有扁平集团关系，无直接母公司证据。直接母公司字段保持空，不把所有记录伪装为集团一级子公司。支持显式多级parent链并验证不存在悬空ID或环。

## 字段与兼容

- 保留 `groups` schema_version=2，新增identity_schema_version=1。每个已登记实际主体持久化entity_id；同法人多个名字共享ID，集团和子公司不同ID。ID一经生成后随改名保留，不能重新按名称生成覆盖旧ID。
- EntityResolution原四字段保持可用，后追加entity_id/entity_name/group_id/parent_entity_id/raw_name。旧implementing/subsidiary投影仍可能是集团+原文名，**新存储不能继续用它们代替actual实体键**。
- 仅规范中英文大小写、括号宽度及无歧义尾缀；不做substring/模糊匹配，不读翻译缓存认证身份。canonical_en 是既有受控名录的正式字段，不接受模型生成英文作为身份证据。
- S02B developer查表只返回actual法定主体，不用group-only映射提升为集团。它不提供项目developer证据，也不根据企业身份填空缺的developer。
- 生成器默认只校验现行主表，不再用旧输入覆盖后补裁决。`--bootstrap-output NEW_PATH` 仅允许写独立且尚不存在的历史初始化快照，不能当作当前主表。中电国际/三峡发展（马）后补记录与新ID因此不会被重建抹去。

## 验证边界

已完成解析器及查表定向测试；覆盖实际简称/正式名同ID、父子不同ID、group-only不误作法人、多级继承、直接母公司缺失、跨组歧义、同集团尾缀歧义、Recurrent待核实、明确外企、builder不覆盖和S02B不提升developer。
测试均使用临时数据库/纯内存或worktree数据；本次未对生产数据库运行解析回填。保留历史集团归属和多级关系的未决项，不把结构通过写成全表事实已验证。
