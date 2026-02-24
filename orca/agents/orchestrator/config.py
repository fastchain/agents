import os

DEFAULT_MODEL = os.environ.get("ORCHESTRATOR_MODEL", os.environ.get("DEFAULT_MODEL", "gpt-4"))
