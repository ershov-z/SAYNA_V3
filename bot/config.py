from __future__ import annotations

from functools import lru_cache
from typing import Any

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _parse_id_set(value: Any) -> set[int]:
    if value is None:
        return set()
    if isinstance(value, set):
        return {int(v) for v in value}
    if isinstance(value, list | tuple):
        return {int(v) for v in value}
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return set()
        return {int(v.strip()) for v in stripped.split(",") if v.strip()}
    raise ValueError(f"Cannot parse ID set from value: {value!r}")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore", enable_decoding=False)

    telegram_bot_token: str = Field(alias="TELEGRAM_BOT_TOKEN")
    chad_ai_api_key: str = Field(alias="CHAD_AI_API_KEY")
    chad_ai_base_url: str = Field(default="https://ask.chadgpt.ru/api/v1", alias="CHAD_AI_BASE_URL")
    chad_ai_model: str = Field(default="gpt-5.4-thinking", alias="CHAD_AI_MODEL")
    chad_intent_model: str = Field(default="gpt-5-nano", alias="CHAD_INTENT_MODEL")
    chad_intent_timeout_seconds: float = Field(default=6.0, alias="CHAD_INTENT_TIMEOUT_SECONDS")
    chad_decision_model: str = Field(default="gpt-5-nano", alias="CHAD_DECISION_MODEL")
    chad_decision_timeout_seconds: float = Field(default=1.8, alias="CHAD_DECISION_TIMEOUT_SECONDS")
    chad_decision_min_confidence: float = Field(default=0.35, alias="CHAD_DECISION_MIN_CONFIDENCE")
    chad_image_base_url: str = Field(default="https://ask.chadgpt.ru", alias="CHAD_IMAGE_BASE_URL")
    chad_image_model: str = Field(default="gpt-img-2", alias="CHAD_IMAGE_MODEL")
    chad_image_aspect_ratio: str = Field(default="1:1", alias="CHAD_IMAGE_ASPECT_RATIO")
    chad_image_timeout_seconds: float = Field(default=45.0, alias="CHAD_IMAGE_TIMEOUT_SECONDS")
    chad_image_check_interval_seconds: float = Field(default=2.0, alias="CHAD_IMAGE_CHECK_INTERVAL_SECONDS")
    chad_image_max_wait_seconds: float = Field(default=90.0, alias="CHAD_IMAGE_MAX_WAIT_SECONDS")
    chad_image_intent_model: str = Field(default="gpt-5-nano", alias="CHAD_IMAGE_INTENT_MODEL")
    chad_image_intent_timeout_seconds: float = Field(default=1.2, alias="CHAD_IMAGE_INTENT_TIMEOUT_SECONDS")
    chad_image_intent_threshold: int = Field(default=7, alias="CHAD_IMAGE_INTENT_THRESHOLD")
    chad_image_prompt_model: str = Field(default="gpt-5.4-thinking", alias="CHAD_IMAGE_PROMPT_MODEL")
    chad_image_prompt_timeout_seconds: float = Field(default=15.0, alias="CHAD_IMAGE_PROMPT_TIMEOUT_SECONDS")

    allowed_user_ids: set[int] = Field(default_factory=set, alias="ALLOWED_USER_IDS")
    allowed_chat_ids: set[int] = Field(default_factory=set, alias="ALLOWED_CHAT_IDS")

    timezone: str = Field(default="Europe/Moscow", alias="TZ")
    daily_progress_check_hour: int = Field(default=10, alias="DAILY_PROGRESS_CHECK_HOUR")
    daily_progress_check_minute: int = Field(default=0, alias="DAILY_PROGRESS_CHECK_MINUTE")
    daily_digest_hour: int = Field(default=23, alias="DAILY_DIGEST_HOUR")
    daily_digest_minute: int = Field(default=0, alias="DAILY_DIGEST_MINUTE")
    digest_chat_id: int = Field(default=0, alias="DIGEST_CHAT_ID")
    reminder_interval_minutes: int = Field(default=180, alias="REMINDER_INTERVAL_MINUTES")

    google_service_account_file: str = Field(default="credentials/google-service-account.json", alias="GOOGLE_SERVICE_ACCOUNT_FILE")
    google_sheet_id: str = Field(alias="GOOGLE_SHEET_ID")

    mempalace_transcript_dir: str = Field(default="data/transcripts", alias="MEMPALACE_TRANSCRIPT_DIR")
    mempalace_enabled: bool = Field(default=True, alias="MEMPALACE_ENABLED")
    mempalace_wing_prefix: str = Field(default="workshop", alias="MEMPALACE_WING_PREFIX")
    mempalace_palace_dir: str = Field(default="data/mempalace-palace", alias="MEMPALACE_PALACE_DIR")
    memory_rerank_enabled: bool = Field(default=False, alias="MEMORY_RERANK_ENABLED")
    memory_rerank_min_candidates: int = Field(default=4, alias="MEMORY_RERANK_MIN_CANDIDATES")
    memory_rerank_candidate_limit: int = Field(default=8, alias="MEMORY_RERANK_CANDIDATE_LIMIT")
    memory_rerank_final_limit: int = Field(default=3, alias="MEMORY_RERANK_FINAL_LIMIT")
    memory_rerank_timeout_seconds: float = Field(default=1.8, alias="MEMORY_RERANK_TIMEOUT_SECONDS")
    startup_selftest_enabled: bool = Field(default=False, alias="STARTUP_SELFTEST_ENABLED")
    startup_selftest_fail_fast: bool = Field(default=False, alias="STARTUP_SELFTEST_FAIL_FAST")
    startup_selftest_validator_model: str = Field(default="gpt-5.4-thinking", alias="STARTUP_SELFTEST_VALIDATOR_MODEL")
    startup_selftest_timeout_seconds: float = Field(default=18.0, alias="STARTUP_SELFTEST_TIMEOUT_SECONDS")

    group_cooldown_seconds: int = Field(default=45, alias="GROUP_COOLDOWN_SECONDS")
    group_context_probability: float = Field(default=0.25, alias="GROUP_CONTEXT_PROBABILITY")
    group_active_window_seconds: int = Field(default=420, alias="GROUP_ACTIVE_WINDOW_SECONDS")
    group_active_context_probability: float = Field(default=0.60, alias="GROUP_ACTIVE_CONTEXT_PROBABILITY")
    group_intent_score_threshold: int = Field(default=7, alias="GROUP_INTENT_SCORE_THRESHOLD")
    modular_orchestrator_enabled: bool = Field(default=False, alias="MODULAR_ORCHESTRATOR_ENABLED")
    max_reply_chars: int = Field(default=3200, alias="MAX_REPLY_CHARS")

    @model_validator(mode="before")
    @classmethod
    def _migrate_legacy_group_intent_timeout(cls, data: Any) -> Any:
        # Backward compatibility for older env files.
        if isinstance(data, dict):
            if "CHAD_INTENT_TIMEOUT_SECONDS" not in data and "GROUP_INTENT_TIMEOUT_SECONDS" in data:
                data = dict(data)
                data["CHAD_INTENT_TIMEOUT_SECONDS"] = data["GROUP_INTENT_TIMEOUT_SECONDS"]
        return data

    @field_validator("allowed_user_ids", mode="before")
    @classmethod
    def _validate_user_ids(cls, value: Any) -> set[int]:
        return _parse_id_set(value)

    @field_validator("allowed_chat_ids", mode="before")
    @classmethod
    def _validate_chat_ids(cls, value: Any) -> set[int]:
        return _parse_id_set(value)


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
