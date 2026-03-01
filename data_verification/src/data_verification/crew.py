from typing import List

from crewai import Agent, Crew, Process, Task
from crewai.agents.agent_builder.base_agent import BaseAgent
from crewai.project import CrewBase, agent, crew, task

from data_verification.tools.duplicate_merger_tool import DuplicateMergerTool


@CrewBase
class DataVerification():
    """一带一路可再生能源数据库 — 数据去重与入库 Crew"""

    agents: List[BaseAgent]
    tasks: List[Task]

    @agent
    def data_verifier(self) -> Agent:
        return Agent(
            config=self.agents_config["data_verifier"],  # type: ignore[index]
            tools=[DuplicateMergerTool()],
            verbose=True,
        )

    @task
    def merge_and_store_task(self) -> Task:
        return Task(
            config=self.tasks_config["merge_and_store_task"],  # type: ignore[index]
        )

    @crew
    def crew(self) -> Crew:
        """创建顺序执行的数据去重与入库 Crew"""
        return Crew(
            agents=self.agents,
            tasks=self.tasks,
            process=Process.sequential,
            verbose=True,
        )
