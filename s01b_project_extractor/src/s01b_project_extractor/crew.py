from typing import List

from crewai import Agent, Crew, Process, Task
from crewai.agents.agent_builder.base_agent import BaseAgent
from crewai.project import CrewBase, agent, crew, task

from s01b_project_extractor.guardrails.extraction_guardrail import validate_extraction
from s01b_project_extractor.tools.project_extractor_tool import ProjectExtractorTool


@CrewBase
class ProjectExtractor():
    """一带一路可再生能源数据库 — 项目信息提取 Crew"""

    agents: List[BaseAgent]
    tasks: List[Task]

    @agent
    def project_extractor(self) -> Agent:
        return Agent(
            config=self.agents_config['project_extractor'],  # type: ignore[index]
            tools=[ProjectExtractorTool()],
            verbose=True,
        )

    @task
    def extract_projects_task(self) -> Task:
        return Task(
            config=self.tasks_config['extract_projects_task'],  # type: ignore[index]
            guardrail=validate_extraction,
            guardrail_max_retries=3,
        )

    @crew
    def crew(self) -> Crew:
        """创建项目信息提取 Crew"""
        return Crew(
            agents=self.agents,
            tasks=self.tasks,
            process=Process.sequential,
            verbose=True,
        )
