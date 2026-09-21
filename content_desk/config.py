"""Environment settings for the content desk server."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ContentDeskSettings(BaseSettings):
    """Load drama API credentials and local data paths from the environment."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    drama_api_base_url: str = Field(
        default="https://fictora-drama-generation-prod-drama.up.railway.app",
        validation_alias="FICTORA_DRAMA_GENERATION_API_BASE_URL",
    )
    drama_service_token: str = Field(default="", validation_alias="FICTORA_DRAMA_GENERATION_SERVICE_TOKEN")
    data_dir: Path = Field(default=Path("~/Downloads/content-desk-data"), validation_alias="CONTENT_DESK_DATA_DIR")
    host: str = Field(default="127.0.0.1", validation_alias="CONTENT_DESK_HOST")
    port: int = Field(default=5199, validation_alias="CONTENT_DESK_PORT")

    def resolved_data_dir(self) -> Path:
        """Return the expanded data directory, creating it when missing.

        Returns
        -------
        Path
            Absolute desk storage root.
        """

        path = self.data_dir.expanduser().resolve()
        path.mkdir(parents=True, exist_ok=True)
        return path

    def require_token(self) -> str:
        """Return a non-empty drama service token.

        Returns
        -------
        str
            Bearer token for the Drama Generation API.

        Raises
        ------
        RuntimeError
            When the token is unset.
        """

        token = self.drama_service_token.strip()
        if not token:
            raise RuntimeError("FICTORA_DRAMA_GENERATION_SERVICE_TOKEN is required")
        return token


def load_settings() -> ContentDeskSettings:
    """Load settings from the repository ``.env`` and process environment.

    Returns
    -------
    ContentDeskSettings
        Resolved configuration.
    """

    env_path = Path(__file__).resolve().parents[1] / ".env"
    if env_path.is_file():
        return ContentDeskSettings(_env_file=env_path)
    return ContentDeskSettings()
