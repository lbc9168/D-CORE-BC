from typing import List

from crewai import Agent, Crew, Process, Task
from crewai.project import CrewBase, agent, crew, task
from s02a_duplicate_merger.tools.duplicate_merger_tool import DuplicateMergerTool


@CrewBase
class DuplicateMerger():
    """BRI RE Database — Deduplication & Storage Crew"""

    agents: List[Agent]
    tasks: List[Task]

    @agent
    def data_verifier(self) -> Agent:
        return Agent(
            config=self.agents_config["data_verifier"],
            tools=[DuplicateMergerTool()],
            verbose=True,
        )

    @task
    def merge_and_store_task(self) -> Task:
        return Task(
            config=self.tasks_config["merge_and_store_task"],
        )

    @crew
    def crew(self) -> Crew:
        """Sequential crew: load → dedup → store"""
        return Crew(
            agents=self.agents,
            tasks=self.tasks,
            process=Process.sequential,
            verbose=True,
        )
