# 企业名录联网补查 — 2026-09-17

本报告接续当天第一轮结构审阅，覆盖当时标记的66条记录（42个集团层级名称、23个法人未明条目、Recurrent登记项），另查7个相关名称变体。已逐条检索，不等于66条全部补齐，也不等于全库563个主体都重新核实。

## 结果与实际处理

| 原待核名称的当前处理 | 条数 | 含义 |
|---|---:|---|
| 可解析到已确认法人 | 26 | 同一法人采用同一ID；包括已存在条目的补证及新确认。湖南云箭两种名称的当前集团仍待核实。 |
| 保留集团层级 | 24 | 品牌、分支、部门或尚未确认的精确法人简称，保留参与原文，不强造法人ID。 |
| 须补原文或精确名称证据 | 16 | 11条通用/有条件简称，5条精确主体或控制关系仍缺证。 |

11条上下文名称包括上海电气10个裸简称及贵州工程公司。完整法人证据可成立，但不能由裸称自动选定。5条证据不足名称：中国能源建设集团福建公司、川投国际（香港）有限公司、中国通信服务集团沙特分公司、威顿控股马来西亚有限公司、威顿马来西亚能源有限公司。后者均隔离自动身份继承，原文充分的参与证据仍走项目独立判断。

## 主要纠正

- 中矿资源与中色矿业是不同法人，年报支持控股关系；中色矿业为境内非国有法人，删除错误央企标记。
- Recurrent平台属于Canadian Solar体系；LLC与B.V.各自独立，不能登记为CSI Solar子公司。沿用用户既有Canadian Solar研究纳入口径，明确加拿大注册地；研究范围判断不改写注册地。CSI Solar保留原统计子集团与ID，最终集团参考字段不伪装成已证直接母链。
- 中电国际、中建安装、西电国际、CMC等与集团母公司分开；有直接股东证据才写parent_entity_id。上海三菱电梯直接母公司为上海机电。
- 正泰新能源、中天科技补齐受控法人简称；惠生南通旧名只按官方曾用名证据映射，不与惠生工程合并。
- 储能/跟踪品牌、事业部、代表处及分公司不因属于中企集团而自动变成独立法人。Developer和融资/保险角色依旧需要每个项目原文。
- 上海集优涉及吸收合并、输配电涉及不同法人，不混用ID。湖南云箭保留历史集团记录，但当前解析不返回未经复核的旧集团ID；中国企业身份独立保留。

## 范围、备份与验证

当前主表：152组、470条子主体记录、563个唯一主体ID；新增36个ID，原有527个ID无丢失、无重分配。16条不同的直接母子关系已有证据。14条子记录仍未绑定法人，可能是组织/品牌或未解名称，不都意味着应补成公司。

修订仅在codex/entity-identity-repair工作树；两份名录修改前快照在output/entity-identity-repair/webcheck-before/。Registry仅Recurrent一条企业记录变化。相关测试884项通过，其中最终受影响路径定向58项通过，覆盖真实原文固定提取结果→S01B→SQLite→S02A。未调用付费模型。生产四库哈希及主工作区66项已有改动保持不变；未合并、发布、回填历史数据库。

## 以后每轮怎么维护

1. 流水线导出一份待补录清单，保存文章原名、日期、项目参与及角色证据。
2. 对清单进行联网补查：官网、年报、监管/交易所披露优先；每个法人、同名关系、集团/直接母关系分别附来源及适用时间。
3. 确认同一法人复用已有ID；新法人分配新ID；品牌只记录集团；有条件简称保留上下文要求；证据冲突或缺失继续待核实。
4. 校验两份名录及名称解析，生成选中记录的dry-run结果；写库须备份、事务及ID核对。历史已有主体/集团/角色更正进入下游复核，不自动全表覆盖。
5. Developer、EPC、融资和保险角色由项目原文证明；企业入册不生成角色。

当前联网研究由本次代理执行，补录脚本尚不会自行联网采信来源。名录也尚无完整历史股权有效期模型；来源时间和历史提示不能当作已实现按日期自动回溯。

## 逐条结果

“法人”指此原名当前可唯一解析；“集团”指只得到集团；“待语境/证据”不一定意味着候选法人不存在。来源访问限制和每项缺口详见四份机器记录。

| 原始待核名称 | 当前处理 | 已证法人/候选或组织 | 首要证据 |
|---|---|---|---|
| 中国能建 | 集团 | 见逐项记录 | [来源](https://www.ceec.net.cn/col/col52771/index.html) |
| 中国能源建设集团福建公司 | 待语境/证据 | 见逐项记录 | [来源](https://www.ceec.net.cn/col/col52771/index.html) |
| 中能建葛洲坝工程科技有限公司 | 法人 | 中能建葛洲坝工程科技有限公司 | [来源](https://www.cggc.ceec.net.cn/art/2025/11/30/art_7370_2528913.html) |
| 中国水电建设集团 | 集团 | 见逐项记录 | [来源](https://www.powerchina.cn/zjdj/gsjj/jtgs/index.html) |
| 中国水电（马来西亚）有限公司 | 法人 | 中国水电（马来西亚）有限公司 | [来源](https://static.sse.com.cn/disclosure/bond/announcement/company/c/new/2026-04-30/244038_20260430_GLJX.pdf) |
| 中国电建 | 集团 | 见逐项记录 | [来源](https://www.powerchina.cn/zjdj/gsjj/jtgs/index.html) |
| 中国电建阿根廷分公司 | 集团 | 中国电建阿根廷分公司 | [来源](https://www.powerchina-intl.com/show/9/2781.html) |
| 湖北工程公司工程建设公司 | 集团 | 见逐项记录 | [来源](https://www.powerchina.cn/attach/0/44c7801fd9a94736bca635c960ae0e51.pdf) |
| 贵州工程公司 | 待语境/证据 | 中国电建集团贵州工程有限公司 | [来源](https://jy.ncut.edu.cn/front/zph.jspa?tid=9918) |
| 中国电建集团尼日利亚代表处 | 集团 | 见逐项记录 | [来源](https://www.powerchina-intl.com/show/9/3587.html) |
| 中电国际 | 法人 | 中国电力国际有限公司 | [来源](https://static.cninfo.com.cn/finalpage/2023-04-28/1216697793.pdf) |
| 国家电投中电国际 | 法人 | 中国电力国际有限公司 | [来源](https://static.cninfo.com.cn/finalpage/2023-04-28/1216697793.pdf) |
| 中国大唐集团海外投资公司 | 集团 | 中国大唐集团海外投资有限公司 | [来源](https://www.china-cdto.com/dthwtz/gywm/jj/A067002010Gone1.html) |
| 国网宁波供电公司 | 集团 | 国网浙江省电力有限公司宁波供电公司 | [来源](https://zjb.nea.gov.cn/dtyw/xkgsgg/202412/t20241217_274025.html) |
| 东方电气 | 集团 | 见逐项记录 | [来源](https://www.dongfang.com/gyjt/fzlc.htm) |
| 国机工程集团 | 法人 | 中国机械工业工程集团有限公司 | [来源](https://www.sinomach.com.cn/xwzx/jtdt/2021_jtyw/202112/t20211229_326283.html) |
| 国机工程集团总部（中设集团） | 集团 | 见逐项记录 | [来源](https://www.sinomach.com.cn/synr/flash/index_47.html) |
| 苏美达能源 | 法人 | 江苏苏美达能源控股有限公司 | [来源](https://www.sinomach.com.cn/xwzx/zgsdt/2026_qydt/202601/t20260105_610213.html) |
| 电站集团 | 待语境/证据 | 上海电气电站集团 | [来源](https://www.shanghai-electric.com/group/upload/resources/file/2024/11/11/104695.pdf) |
| 核电集团 | 待语境/证据 | 上海电气核电集团有限公司 | [来源](https://www.shanghai.gov.cn/nw31406/20250708/80b7da48e47a41ec8426ed02eeb23d8c.html) |
| 电气风电 | 法人 | 上海电气风电集团股份有限公司 | [来源](https://static.cninfo.com.cn/finalpage/2025-03-25/1222880362.PDF) |
| 输配电集团 | 待语境/证据 | 上海电气输配电集团有限公司 / 上海电气集团输配电装备有限公司 | [来源](https://www.shqp.gov.cn/shqp/qpyw/20260305/1353394.html) |
| 自动化集团 | 待语境/证据 | 上海电气自动化集团有限公司 | [来源](https://www.shanghai-electric.com/group/upload/resources/file/2019/09/03/68775.pdf) |
| 轨道交通集团 | 待语境/证据 | 上海电气轨道交通集团 | [来源](https://www.shanghai-electric.com/group/upload/resources/file/2020/11/02/88015.pdf) |
| 环保集团 | 待语境/证据 | 上海电气环保集团 | [来源](https://www.gzw.sh.gov.cn/shgzw_xxgk_cyggcz/20190717/0054-92073.html) |
| 上海三菱 | 待语境/证据 | 上海三菱电梯有限公司 | [来源](https://www.mebs.co.jp/press/260511.html) |
| 上海集优 | 集团 | 上海集优机械股份有限公司 / 上海集优铭宇机械科技有限公司 | [来源](https://www.hkexnews.hk/listedco/listconews/sehk/2020/1015/2020101500948_c.pdf) |
| 上海机电 | 法人 | 上海机电股份有限公司 | [来源](https://static.cninfo.com.cn/finalpage/2026-03-21/1225021828.PDF) |
| 金融集团 | 待语境/证据 | 上海电气金融集团 | [来源](https://www.shanghai-electric.com/group/upload/resources/file/2019/09/03/68775.pdf) |
| 中央研究院 | 待语境/证据 | 上海电气集团股份有限公司中央研究院 | [来源](https://www.gzw.sh.gov.cn/shgzw_zxzx_gqdt/20240321/9290b6a5943a4fe6917e9635dcfcda38.html) |
| 电气新能源 | 待语境/证据 | 上海电气新能源发展有限公司 | [来源](https://www.hkexnews.hk/listedco/listconews/sehk/2022/1205/2022120501707_c.pdf) |
| 中国建筑国际集团有限公司 | 法人 | 中国建筑国际集团有限公司 | [来源](https://www.cscec.com.cn/) |
| 中建安装 | 法人 | 中建安装集团有限公司 | [来源](https://inco.cscec.com/gyzj/gsjj/) |
| 西电国际 | 法人 | 西安西电国际工程有限责任公司 | [来源](https://www.xd.com.cn/info/1031/13108.htm) |
| 中国机械进出口（集团）有限公司 | 法人 | 中国机械进出口（集团）有限公司 | [来源](https://www.cmc.com.cn/gywm/zzry/index.html) |
| 远景储能 | 集团 | 远景能源（储能业务） | [来源](https://www.envisioncn.com/en/energy.html) |
| 中国交建尼日利亚公司 | 集团 | 中国交建尼日利亚股份有限公司 | [来源](https://www.sse.com.cn/disclosure/bond/announcement/company/c/new/2024-04-30/122175_20240430_FFRG.pdf) |
| 浙江正泰新能源开发有限公司 | 法人 | 浙江正泰新能源开发有限公司 | [来源](https://energy.chint.com/about/) |
| 正泰新能源 | 法人 | 浙江正泰新能源开发有限公司 | [来源](https://energy.chint.com/about/) |
| 天合跟踪 | 集团 | TrinaTracker（天合跟踪） | [来源](https://www.trinasolar.com/cn/20201120-1954/) |
| e-STORAGE | 集团 | CSI Solar e-STORAGE业务 | [来源](https://investors.canadiansolar.com/news-releases/news-release-details/canadian-solar-announces-rebranding-its-csi-solar-energy-storage) |
| 阿特斯储能 | 集团 | CSI Solar储能业务（e-STORAGE候选品牌） | [来源](https://investors.canadiansolar.com/news-releases/news-release-details/canadian-solar-announces-rebranding-its-csi-solar-energy-storage) |
| 山西建投国际投资公司 | 集团 | 山西建投国际投资有限公司 | [来源](https://www.hkexnews.hk/listedco/listconews/sehk/2026/0114/2026011401160_c.pdf) |
| 山西建投国际投资有限公司 | 法人 | 山西建投国际投资有限公司 | [来源](https://www.hkexnews.hk/listedco/listconews/sehk/2026/0114/2026011401160_c.pdf) |
| 中国出口信用保险公司四川分公司 | 集团 | 中国出口信用保险公司四川分公司 | [来源](https://gd.sinosure.com.cn/gywm/gsjj/xxpl/xxgg/2026/07/220567.shtml) |
| 英辰新能源 | 集团 | 英利集团英辰新能源品牌 | [来源](https://cn.ycsolar.com/) |
| 中矿资源集团股份有限公司 | 法人 | 中矿资源集团股份有限公司 | [来源](https://static.cninfo.com.cn/finalpage/2026-04-10/1225089390.PDF) |
| 惠生工程 | 集团 | 惠生工程技术服务有限公司 | [来源](https://www.wison-engineering.com/tc/newsinfo/72.html) |
| 惠生清洁能源科技集团股份有限公司 | 法人 | 惠生清洁能源科技集团股份有限公司 | [来源](https://cn.wison-energies.com/upfiles/attachments/6385250358808146644231991.pdf) |
| 惠生（南通）重工有限公司 | 法人 | 惠生清洁能源科技集团股份有限公司 | [来源](https://cn.wison-energies.com/upfiles/attachments/6385250358808146644231991.pdf) |
| 中国石化石油工程建设公司 | 集团 | 中石化石油工程建设有限公司 | [来源](https://ssc.sinopec.com/sosc/investor/interim_report/Documents/20240326/doc_20240326_633502135198.pdf) |
| 中远海运工程物流有限公司 | 法人 | 中远海运工程物流有限公司 | [来源](https://hold.coscoshipping.com/cms_files/jcms1/web32/site/attach/0/d8ace682881049339555ac290c633802.pdf) |
| 中远海运物流供应链工程物流事业部 | 集团 | 见逐项记录 | [来源](https://www.zgyd1921.com/information/news/7be917b76a1f4d6abb484253a317af6a.html) |
| 川投国际 | 集团 | 川投国际有限公司 | [来源](https://www.invest.com.cn/Public/Upload/file/2023/1124/20231124093035141.pdf) |
| 川投国际（香港）有限公司 | 待语境/证据 | 川投国际有限公司 | [来源](https://static.sse.com.cn/disclosure/bond/announcement/company/c/new/2025-08-29/242270_20250829_YBY5.pdf) |
| 中天科技 | 法人 | 江苏中天科技股份有限公司 | [来源](https://static.cninfo.com.cn/finalpage/2025-08-26/1224564534.PDF) |
| 江苏中天科技股份有限公司 | 法人 | 江苏中天科技股份有限公司 | [来源](https://static.cninfo.com.cn/finalpage/2026-04-25/1225177318.PDF) |
| 山东泰开电力建设工程有限公司 | 法人 | 山东泰开电力建设工程有限公司 | [来源](https://www.tk-electric.com/Home.html) |
| 中集安瑞科控股有限公司 | 法人 | 中集安瑞科控股有限公司 | [来源](https://www.enricgroup.com/about) |
| 湖南云箭集团 | 法人 | 湖南云箭集团有限公司 | [来源](https://static.cninfo.com.cn/finalpage/2022-01-14/1212171055.PDF) |
| 湖南云箭集团有限公司 | 法人 | 湖南云箭集团有限公司 | [来源](https://static.cninfo.com.cn/finalpage/2022-01-14/1212171055.PDF) |
| 中国通信服务集团沙特分公司 | 待语境/证据 | 见逐项记录 | [来源](https://www.chinatax.gov.cn/chinatax/n810219/n810744/n1671176/n1671206/c2581441/5116188/files/3f04b15238b2459c9a128523e6a62238.pdf) |
| 天津泰达环保有限公司 | 法人 | 天津泰达环保有限公司 | [来源](https://www.tedahb.com/) |
| 威顿控股马来西亚有限公司 | 待语境/证据 | Weidun Holdings Malaysia Sdn. Bhd. | [来源](https://www.iweidun.com/case_list/show/id/523.html) |
| 威顿马来西亚能源有限公司 | 待语境/证据 | 见逐项记录 | [来源](https://www.iweidun.com/case_list/show/id/523.html) |
| Recurrent Energy | 集团 | 见逐项记录 | [来源](https://www.sec.gov/Archives/edgar/data/1375877/000110465926041976/csiq-20251231xex8d1.htm) |

证据明细：[上海电气](entity_webcheck_shanghai_2026-09-17.json)、[央国企](entity_webcheck_state_2026-09-17.json)、[其他企业](entity_webcheck_other_2026-09-17.json)、[中矿/Recurrent](entity_webcheck_root_2026-09-17.json)。汇总解析：[JSON](entity_webcheck_2026-09-17.json)。

部分企业官网全文访问受限，记录明确区分官方搜索索引和已打开原文，未将二手转载线索升级为独立法人/股权确证。
