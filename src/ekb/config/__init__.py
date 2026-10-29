import os
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="EKB_", env_file=".env", extra="ignore")

    database_url: SecretStr = SecretStr("postgresql+asyncpg://ekb:ekb@localhost:5432/ekb")
    data_dir: Path = Path("data")
    inference: Literal["rules", "openai", "agent-framework"] = "rules"
    model_endpoint: str = "http://localhost:5272/v1"
    model_id: str = ""
    model_api_key: SecretStr = SecretStr("")
    model_structured_output: bool = False
    model_context_tokens: int = Field(default=32768, ge=512)
    model_max_output_tokens: int = Field(default=2048, ge=128, le=32768)
    model_input_price: float | None = Field(default=None, ge=0)
    model_output_price: float | None = Field(default=None, ge=0)
    model_managed_identity: bool = False
    model_retries: int = Field(default=2, ge=0, le=5)
    scan_concurrency: int = Field(default=4, ge=1, le=32)
    parse_concurrency: int = Field(default=2, ge=1, le=16)
    model_concurrency: int = Field(default=2, ge=1, le=32)
    max_document_bytes: int = Field(default=50_000_000, ge=1024)
    ocr: bool = False
    segment_max_chars: int = Field(default=12000, ge=200, le=64000)
    artifact_backend: Literal["filesystem", "azure"] = "filesystem"
    blob_account_url: str = ""
    blob_container: str = "ekb"
    azure_monitor_connection_string: SecretStr = SecretStr("")
    debug_artifacts: bool = False
    log_max_bytes: int = Field(default=5_000_000, ge=1024)
    log_backups: int = Field(default=5, ge=1)
    worker_interval: float = Field(default=60, ge=1)
    manifest: Path = Path("sources/public.yaml")
    source_directory: Path | None = None

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        profile = os.environ.get("EKB_CONFIG_FILE", "config/local.yaml")
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            YamlConfigSettingsSource(settings_cls, yaml_file=profile),
            file_secret_settings,
        )
