from typing import List

from crewai import Agent, Crew, Process, Task
from crewai.agents.agent_builder.base_agent import BaseAgent
from crewai.project import CrewBase, agent, crew, task

from s01a_news_crawler.tools.news_crawler_tool import NewsCrawlerTool


@CrewBase
class NewsCrawler():
    """一带一路可再生能源数据库 — 新闻采集 Crew"""

    agents: List[BaseAgent]
    tasks: List[Task]

    @agent
    def news_crawler(self) -> Agent:
        return Agent(
            config=self.agents_config['news_crawler'],  # type: ignore[index]
            tools=[NewsCrawlerTool()],
            verbose=True,
        )

    @task
    def crawl_news_task(self) -> Task:
        return Task(
            config=self.tasks_config['crawl_news_task'],  # type: ignore[index]
        )

    @crew
    def crew(self) -> Crew:
        """创建新闻采集 Crew"""
        return Crew(
            agents=self.agents,
            tasks=self.tasks,
            process=Process.sequential,
            verbose=True,
        )
