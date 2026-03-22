from typing import List

from crewai import Agent, Crew, Process, Task
from crewai.project import CrewBase, agent, crew, task
from s02c_cross_verification.tools.cross_verification_tool import CrossVerificationTool


@CrewBase
class CrossVerification():
    """BRI RE Database — External Cross-Verification Crew"""

    agents: List[Agent]
    tasks: List[Task]

    @agent
    def cross_verifier(self) -> Agent:
        return Agent(
            config=self.agents_config["cross_verifier"],
            tools=[CrossVerificationTool()],
            verbose=True,
        )

    @task
    def cross_verify_task(self) -> Task:
        return Task(
            config=self.tasks_config["cross_verify_task"],
        )

    @crew
    def crew(self) -> Crew:
        """Single-agent crew: GDELT query → vector match → LLM confirm → write back"""
        return Crew(
            agents=self.agents,
            tasks=self.tasks,
            process=Process.sequential,
            verbose=True,
        )
