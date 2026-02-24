# run_orchestrator.py
from bus import DurableBus
from agents.orchestrator import ResumableOrchestrator

bus = DurableBus(irc_host="localhost", irc_port=6667)
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
