from typing import List

from crewai import Agent, Crew, Process, Task
from crewai.agents.agent_builder.base_agent import BaseAgent
from crewai.project import CrewBase, agent, crew, task

from s01_data_collector_filter.tools.news_crawler_tool import NewsCrawlerTool
from s01_data_collector_filter.tools.project_extractor_tool import ProjectExtractorTool


@CrewBase
class DataCollectorFilter():
    """一带一路可再生能源数据库 — 数据采集与筛选 Crew"""

    agents: List[BaseAgent]
    tasks: List[Task]

    @agent
    def news_crawler(self) -> Agent:
        return Agent(
            config=self.agents_config['news_crawler'],  # type: ignore[index]
            tools=[NewsCrawlerTool()],
            verbose=True,
        )

    @agent
    def project_extractor(self) -> Agent:
        return Agent(
            config=self.agents_config['project_extractor'],  # type: ignore[index]
            tools=[ProjectExtractorTool()],
            verbose=True,
        )

    @task
    def crawl_news_task(self) -> Task:
        return Task(
            config=self.tasks_config['crawl_news_task'],  # type: ignore[index]
        )

    @task
    def extract_projects_task(self) -> Task:
        return Task(
            config=self.tasks_config['extract_projects_task'],  # type: ignore[index]
            context=[self.crawl_news_task()],  # 将爬虫输出文件路径传入提取任务上下文
        )

    @crew
    def crew(self) -> Crew:
        """创建顺序执行的数据采集与筛选 Crew"""
        return Crew(
            agents=self.agents,
            tasks=self.tasks,
            process=Process.sequential,
            verbose=True,
        )
