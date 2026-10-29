/** TechnologyTagBadges.tsx —— 在现有标签行中渲染独立于项目形态子类的技术组合标签。 */

import type { Project } from "@/types";

interface TechnologyTagBadgesProps {
  project: Project;
  compact?: boolean;
  className?: string;
}

/** 优先展示英文标签，并以中文标签作为旧数据或缺翻译时的回退。 */
export default function TechnologyTagBadges({
  project,
  compact = false,
  className = "",
}: TechnologyTagBadgesProps) {
  const labels = project.technology_tags_en?.length
    ? project.technology_tags_en
    : (project.technology_tags ?? []);
  return labels.map((label, index) => (
    <span
      key={`${project.project_id}-technology-${index}-${label}`}
      className={`badge border border-violet-200 bg-violet-50 text-violet-700 ${className}`}
      style={{ fontSize: compact ? "11px" : undefined }}
    >
      {label}
    </span>
  ));
}
