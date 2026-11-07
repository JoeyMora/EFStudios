"""API keys and settings, read from the environment or a .env file."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def load_dotenv(path: str | Path = ".env") -> None:
    """Minimal .env loader: KEY=value lines; existing env vars win."""
    path = Path(path)
    if not path.is_file():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().removeprefix("export ").strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


@dataclass
class Settings:
    spotify_client_id: str = ""
    spotify_client_secret: str = ""
    youtube_api_key: str = ""
    brave_api_key: str = ""
    serpapi_api_key: str = ""

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            spotify_client_id=os.environ.get("SPOTIFY_CLIENT_ID", ""),
            spotify_client_secret=os.environ.get("SPOTIFY_CLIENT_SECRET", ""),
            youtube_api_key=os.environ.get("YOUTUBE_API_KEY", ""),
            brave_api_key=os.environ.get("BRAVE_API_KEY", ""),
            serpapi_api_key=os.environ.get("SERPAPI_API_KEY", ""),
        )

    @property
    def has_spotify(self) -> bool:
        return bool(self.spotify_client_id and self.spotify_client_secret)

    @property
    def has_web_search(self) -> bool:
        return bool(self.brave_api_key or self.serpapi_api_key)
