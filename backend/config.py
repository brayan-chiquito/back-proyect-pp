"""Carga de configuración desde variables de entorno (.env)."""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# El .env vive en la raíz del repo (un nivel arriba de backend/)
_ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


class Settings(BaseSettings):
    """Valores compartidos bridge + backend (un solo .env en la raíz)."""

    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    anthropic_api_key: str = ""
    ai_model: str = "claude-haiku-4-5-20251001"

    bridge_port: int = 3000
    bridge_url: str = "http://localhost:3000"
    backend_url: str = "http://localhost:8000"
    bridge_secret: str = ""

    excel_url: str = ""
    excel_cache_minutes: int = 30

    operator_pasteleria: str = ""
    operator_pijamas: str = ""
    operator_comida: str = ""

    session_timeout_minutes: int = 30
    abuse_block_minutes: int = 60
    escalation_price_limit: int = 500_000

    supabase_url: str = ""
    supabase_key: str = ""


def get_settings() -> Settings:
    """Instancia única perezosa para tests que monkeypatchean env."""
    return Settings()
