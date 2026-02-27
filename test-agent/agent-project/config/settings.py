"""Agent configuration — model params, API keys, feature flags."""

import os
from dataclasses import dataclass, field


@dataclass
class Settings:
    # Model
    model: str = "claude-sonnet-4-5-20250929"
    max_tokens: int = 4096
    temperature: float = 0.0

    # API
    anthropic_api_key: str = field(
        default_factory=lambda: os.environ.get("ANTHROPIC_API_KEY", "")
    )

    # Feature flags
    enable_search: bool = True
    enable_database: bool = True
    enable_file_ops: bool = True
    enable_mcps: bool = False  # Off by default — requires MCP server config

    # Skills
    skills_dir: str = os.path.join(os.path.dirname(os.path.dirname(__file__)), "skills")

    # Database (for the database tool)
    database_path: str = ":memory:"


settings = Settings()
