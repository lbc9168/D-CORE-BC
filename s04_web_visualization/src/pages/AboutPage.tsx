import { Info } from "lucide-react";
import { useData } from "@/contexts/DataContext";
import { useLang } from "@/contexts/LanguageContext";

const DEV_TEAM = [
  { name: "Bingcai Liu",    url: "https://www.linkedin.com/in/bingcailiu/" },
  { name: "Zhengyun Zhou",  url: "https://www.linkedin.com/in/zhengyun-zhou-0b32881b4/" },
  { name: "Wei Shen",       url: "https://www.linkedin.com/in/wei-shen-b49387162/" },
  { name: "Zhijian Yu",     url: "https://www.zhijianyu.top/" },
];
const SPECIAL_THANKS = ["Han Chen", "Zhao Li", "Xuan Xie"];
const INTERNS = [
  "Xi Chen", "Yiran Chen", "Ying Deng", "Zihan He", "Mengjia Hu", "Lizi Huang",
  "Jiayan Li", "Jiatong Li", "Yingyue Li", "Chenrui Lin", "Yicheng Liu", "Yueting Liu",
  "Yuzhen Ren", "Mengbin Wang", "Tongyu Wang", "Yingfei Wang", "Zeping Wu",
  "Xiaoyu Xian", "Yunyi Xie", "Shuaiyang Xu", "Junhao Yao", "Heyan Yuan", "Zilin Yuan",
  "Linuo Zhang", "Ruoyu Zhang", "Yuchen Zhang",
];
// 机构介绍为固定英文长文，硬编码于此（同 CITE_TEXT 处理方式）
const IIGF_TEXT =
  "The International Institute of Green Finance (IIGF) of Central University of Finance and Economics (CUFE) is an independent and non-profit think tank established in China in 2016, based on its predecessor Research Center for Climate and Energy Finance (RCCEF), established in 2011. IIGF actively engage with policymakers, financial institutions, and industry leaders to promote sustainable practices and advocate for policies that support green finance development in China and globally.";
const IIGF_URL = "https://iigf.cufe.edu.cn";
const IDS_TEXT =
  "The Institute of Development Studies (IDS) is a leading global institution for research, teaching, and learning focused on equitable and sustainable development. Established in 1966 as the UK's first national institute for development studies, IDS is consistently ranked first in the world for development studies in the QS World University Rankings (in partnership with the University of Sussex). The Institute employs a multi-disciplinary approach—incorporating expertise in economics, anthropology, and political science—to address global challenges such as climate change, social justice, and health systems.";
const IDS_URL = "https://www.ids.ac.uk";
const CITE_TEXT =
  "International Institution of Green Finance. (2026). Methodology Note: Database of Chinese Overseas Renewable Energy Investment. Available at: https://iigf.cufe.edu.cn/info/1014/11031.htm";
const CITE_URL = "https://iigf.cufe.edu.cn/info/1014/11031.htm";

/** About 页面：项目介绍、合作机构、团队、版权、免责声明与引用信息 */
export default function AboutPage() {
  const { lastUpdated } = useData();
  const { T } = useLang();

  return (
    <div className="flex-1 overflow-y-auto bg-slate-50">
      <div className="max-w-3xl mx-auto px-8 py-10 space-y-4">

        {/* Hero */}
        <div className="card p-8 flex flex-col items-center text-center gap-3">
          <Info size={40} className="text-brand-300" />
          <h2 className="text-2xl font-semibold text-slate-700">
            Database of Chinese Overseas Renewable Energy Projects
          </h2>
          <p className="text-sm text-slate-500 leading-relaxed max-w-xl">
            {T.about.description}
          </p>
        </div>

        {/* 关于 IIGF */}
        <section className="card p-6 space-y-3">
          <h3 className="text-base font-semibold text-slate-700 border-b border-slate-100 pb-2">
            About International Institute of Green Finance (IIGF)
          </h3>
          <p className="text-sm text-slate-500 leading-relaxed">{IIGF_TEXT}</p>
          <a
            href={IIGF_URL}
            target="_blank"
            rel="noopener noreferrer"
            className="text-sm text-brand-600 hover:underline break-all"
          >
            iigf.cufe.edu.cn
          </a>
        </section>

        {/* 关于 IDS */}
        <section className="card p-6 space-y-3">
          <h3 className="text-base font-semibold text-slate-700 border-b border-slate-100 pb-2">
            About Institute of Development Studies (IDS)
          </h3>
          <p className="text-sm text-slate-500 leading-relaxed">{IDS_TEXT}</p>
          <a
            href={IDS_URL}
            target="_blank"
            rel="noopener noreferrer"
            className="text-sm text-brand-600 hover:underline break-all"
          >
            www.ids.ac.uk
          </a>
        </section>

        {/* 开发团队 */}
        <section className="card p-6 space-y-3">
          <h3 className="text-base font-semibold text-slate-700 border-b border-slate-100 pb-2">
            {T.about.devTeam}
          </h3>
          <div className="flex flex-wrap gap-3">
            {DEV_TEAM.map(({ name, url }) => (
              <a
                key={name}
                href={url}
                target="_blank"
                rel="noopener noreferrer"
                className="px-3 py-1 bg-brand-50 text-brand-700 rounded-full text-sm font-medium hover:bg-brand-100 transition-colors"
              >
                {name}
              </a>
            ))}
          </div>
        </section>

        {/* 特别感谢 */}
        <section className="card p-6 space-y-3">
          <h3 className="text-base font-semibold text-slate-700 border-b border-slate-100 pb-2">
            {T.about.thanks}
          </h3>
          <div className="flex flex-wrap gap-3">
            {SPECIAL_THANKS.map((name) => (
              <span key={name} className="px-3 py-1 bg-slate-100 text-slate-600 rounded-full text-sm">
                {name}
              </span>
            ))}
          </div>
        </section>

        {/* 实习生 */}
        <section className="card p-6 space-y-3">
          <h3 className="text-base font-semibold text-slate-700 border-b border-slate-100 pb-2">
            {T.about.interns}
          </h3>
          <p className="text-xs text-slate-500">{T.about.internsNote}</p>
          <p className="text-sm text-slate-500 italic leading-loose">
            {INTERNS.join(", ")}
          </p>
        </section>

        {/* 联系我们 */}
        <section className="card p-6 space-y-3">
          <h3 className="text-base font-semibold text-slate-700 border-b border-slate-100 pb-2">
            {T.about.contact}
          </h3>
          <p className="text-sm text-slate-500 leading-relaxed">
            {T.about.contactText}{" "}
            <a
              href="mailto:bingcai.liu@outlook.com"
              className="text-brand-600 hover:underline"
            >
              bingcai.liu@outlook.com
            </a>
            {" or "}
            <a
              href="mailto:zhijian_yu@foxmail.com"
              className="text-brand-600 hover:underline"
            >
              zhijian_yu@foxmail.com
            </a>
          </p>
        </section>

        {/* 版权 */}
        <section className="card p-6 space-y-3">
          <h3 className="text-base font-semibold text-slate-700 border-b border-slate-100 pb-2">
            {T.about.license}
          </h3>
          <div className="flex items-start gap-3">
            <a
              href="https://creativecommons.org/licenses/by-nc/4.0/"
              target="_blank"
              rel="noopener noreferrer"
              aria-label="CC BY-NC 4.0"
              className="shrink-0 mt-0.5"
            >
              {/* CC BY-NC badge */}
              <svg xmlns="http://www.w3.org/2000/svg" width="88" height="31" viewBox="0 0 88 31">
                <rect width="88" height="31" rx="4" fill="#4B5563"/>
                <text x="6" y="20" fontSize="13" fontFamily="sans-serif" fill="#fff" fontWeight="bold">CC</text>
                <text x="30" y="20" fontSize="11" fontFamily="sans-serif" fill="#D1D5DB">BY-NC 4.0</text>
              </svg>
            </a>
            <p className="text-sm text-slate-500 leading-relaxed">{T.about.licenseText}</p>
          </div>
        </section>

        {/* 免责声明 */}
        <section className="card p-6 space-y-3">
          <h3 className="text-base font-semibold text-slate-700 border-b border-slate-100 pb-2">
            {T.about.disclaimer}
          </h3>
          <p className="text-sm text-slate-500 leading-relaxed">{T.about.disclaimerText}</p>
        </section>

        {/* 引用格式 */}
        <section className="card p-6 space-y-3">
          <h3 className="text-base font-semibold text-slate-700 border-b border-slate-100 pb-2">
            {T.about.cite}
          </h3>
          <blockquote className="bg-slate-50 border-l-4 border-brand-300 rounded px-4 py-3 text-sm text-slate-600 leading-relaxed whitespace-pre-wrap break-words">
            {CITE_TEXT.replace(CITE_URL, "")}{" "}
            <a
              href={CITE_URL}
              target="_blank"
              rel="noopener noreferrer"
              className="text-brand-600 hover:underline break-all"
            >
              {CITE_URL}
            </a>
          </blockquote>
        </section>

        {/* 数据更新时间 */}
        {lastUpdated && (
          <p className="text-center text-xs text-slate-400 pb-4">
            {T.about.lastUpdated}: {lastUpdated}
          </p>
        )}
      </div>
    </div>
  );
}
