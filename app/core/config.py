from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://webintel:webintel@localhost:5432/webintel"

    preflight_max_sample_pages: int = 15
    preflight_http_timeout_seconds: float = 10.0
    preflight_browser_timeout_seconds: float = 20.0
    preflight_report_ttl_hours: int = 24

    crawler_user_agent: str = "WebIntelBot/0.1 (+https://example.com/bot)"

    block_private_networks: bool = True
    allowed_schemes: str = "http,https"

    minio_endpoint: str = "localhost:9000"
    minio_access_key: str = "webintel"
    minio_secret_key: str = "webintel12345"
    minio_bucket: str = "web-intel-raw"
    minio_secure: bool = False

    crawl_default_max_pages: int = 100
    crawl_default_max_depth: int = 3
    crawl_default_concurrency: int = 5
    crawl_max_response_bytes: int = 20_000_000
    crawl_worker_poll_timeout_seconds: float = 5.0
    # Explicit crawl budgets (final completion pass) -- a single URL must
    # never accidentally become an unbounded domain crawl / browser loop.
    crawl_max_browser_pages: int = 25
    crawl_max_browser_seconds_per_page: float = 20.0
    crawl_max_discovered_urls: int = 5000
    crawl_max_pagination_pages: int = 50
    crawl_max_scrolls: int = 5
    crawl_max_scroll_new_items: int = 200
    crawl_max_items_per_index: int = 50

    monitoring_removal_failure_threshold: int = 3
    scheduler_poll_interval_seconds: float = 30.0

    domain_learning_min_observations: int = 5
    # Prefer browser when historical quality-compare says browser wins often.
    domain_browser_superior_rate_threshold: float = 0.6
    domain_low_quality_http_threshold: float = 0.4

    # Hugging Face: used for first-time GLiNER (and optional IndicNER) model
    # download. After cache is warm under ~/.cache/huggingface, the same token
    # is still fine to leave set; local files are reused without re-download.
    hf_token: str | None = None

    @property
    def allowed_schemes_set(self) -> set[str]:
        return {s.strip().lower() for s in self.allowed_schemes.split(",") if s.strip()}


@lru_cache
def get_settings() -> Settings:
    return Settings()


def apply_hf_token_to_environ() -> None:
    """Push Settings.hf_token into process env so huggingface_hub /
    transformers nested loads (GLiNER backbone tokenizer) authenticate
    with the .env value instead of a stale CLI token file."""
    import os

    token = get_settings().hf_token
    if not token:
        return
    os.environ["HF_TOKEN"] = token
    os.environ["HUGGING_FACE_HUB_TOKEN"] = token
