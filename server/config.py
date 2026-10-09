"""
server/config.py - Paths and settings for the web app.

Settings come from environment variables; a .env file in the project root is
loaded automatically (existing environment variables take precedence).
"""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Tuple

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env", override=False)


def _origins() -> Tuple[str, ...]:
    raw = os.getenv("TRAVEL_CORS_ORIGINS", "")
    return tuple(origin.strip().rstrip("/") for origin in raw.split(",") if origin.strip())


@dataclass(frozen=True)
class Settings:
    db_path: Path = Path(os.getenv("TRAVEL_APP_DB", PROJECT_ROOT / "data" / "travel_app.db"))
    tasks_dir: Path = PROJECT_ROOT / "benchmarks" / "tasks"
    mock_data_dir: Path = PROJECT_ROOT / "benchmarks" / "mock_data"
    web_dist_dir: Path = PROJECT_ROOT / "web" / "dist"
    model: str = os.getenv("TRAVEL_AGENT_MODEL", "claude-sonnet-4-6")
    mcp_transport: str = os.getenv("TRAVEL_MCP_TRANSPORT", "stdio")
    max_concurrent_runs: int = int(os.getenv("TRAVEL_MAX_CONCURRENT_RUNS", "2"))

    # Browser origins allowed to call the API (e.g. the Vercel frontend), comma separated.
    cors_origins: Tuple[str, ...] = field(default_factory=_origins)
    # Optional regex for additional origins, e.g. Vercel preview deployments.
    cors_origin_regex: str = os.getenv("TRAVEL_CORS_ORIGIN_REGEX", "")

    @property
    def api_key_configured(self) -> bool:
        return bool(os.getenv("ANTHROPIC_API_KEY"))
