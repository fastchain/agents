# run_orchestrator.py
#
# Set MM_URL, MM_TOKEN, and MM_TEAM environment variables before running,
# or pass them as constructor kwargs below.
import os

from bus import DurableBus
from agents.orchestrator import ResumableOrchestrator

bus = DurableBus(
    mm_url=os.environ.get("MM_URL", "http://localhost:8065"),
    mm_token=os.environ.get("MM_TOKEN", ""),
    mm_team=os.environ.get("MM_TEAM", "agents"),
)
orch = ResumableOrchestrator(bus=bus, agent_id="demo-orchestrator")
orch.register_specialist(
    tool_name="ask_demo_specialist",
    queue="demo_specialist",
    description="Handles demo transformations",
)

result = orch.run(
    session_id="demo-session-001",
    initial_task="Summarize this sentence: 'The quick brown fox jumps over the lazy dog.' Then transform the result to JSON format.",
)
print("\nFinal Result:\n", result)
