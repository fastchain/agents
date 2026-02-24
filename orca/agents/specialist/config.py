import os

DEFAULT_MODEL = os.environ.get("SPECIALIST_MODEL", os.environ.get("DEFAULT_MODEL", "gpt-4"))
