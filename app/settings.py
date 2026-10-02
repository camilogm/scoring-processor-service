from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PIPELINE_VERSION = "0.3.1"


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
    # For comparing models only: comma-separated models a caller may pick per upload (POST `model`).
    # Empty (the default) turns choice off. Keep it empty in production: whoever can upload would
    # choose what each clip costs.
    llm_model_choices: str = ""
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
    # Budget per analysis (one judge call). Going over it is logged, not enforced: the cost is
    # only known after the call.
    max_cost_per_clip_usd: float = 1.0

    run_worker: bool = True

    # HTTP Basic auth for every route but /health. Off when both are empty (local runs); set both
    # as secrets on any deployment reachable from the internet.
    basic_auth_user: str = ""
    basic_auth_password: str = ""

    @model_validator(mode="after")
    def _auth_is_all_or_nothing(self) -> "Settings":
        if bool(self.basic_auth_user) != bool(self.basic_auth_password):
            raise ValueError("Set both BASIC_AUTH_USER and BASIC_AUTH_PASSWORD, or neither.")
        return self

    @property
    def allowed_models(self) -> list[str]:
        """The default model first, then the extra choices; just the default when choice is off."""
        extra = [m.strip() for m in self.llm_model_choices.split(",") if m.strip()]
        return list(dict.fromkeys([self.llm_model, *extra]))

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
