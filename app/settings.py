from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PIPELINE_VERSION = "0.2.0"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql://clip:clip@localhost:5432/clip_scoring"
    # Off: the service refuses to start until `make migrate` has run. On: it migrates on startup.
    auto_run_migrations: bool = False
    data_dir: Path = Path("var")
    max_upload_mb: int = 200
    max_duration_s: float = 240.0

    whisper_model: str = "base.en"
    whisper_compute_type: str = "int8"

    # OpenAI-compatible endpoint: Ollama locally, Vercel AI Gateway for reported runs.
    llm_base_url: str = "http://localhost:11434/v1"
    llm_model: str = "gemma3:latest"
    llm_api_key: str = Field(
        default="ollama",
        validation_alias=AliasChoices("LLM_API_KEY", "AI_GATEWAY_API_KEY"),
    )
    llm_vision: bool = True
    # Frames sent to the judge: ~275 tokens each on gemma3, so a 4k-context local model fits about 6.
    llm_max_frames: int = 16
    llm_json_mode: bool = True
    llm_timeout_s: float = 180.0
    llm_seed: int = 7
    # Fallback cost estimate when the provider doesn't report cost in `usage`.
    llm_price_input_per_mtok: float = 0.0
    llm_price_output_per_mtok: float = 0.0

    run_worker: bool = True

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def work_dir(self) -> Path:
        return self.data_dir / "work"

    def ensure_dirs(self) -> None:
        for path in (self.data_dir, self.uploads_dir, self.work_dir):
            path.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()
