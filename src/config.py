"""Runtime configuration, read once from the environment.

Values that vary between the laptop and the server live here; anything that is a
decision rather than a deployment detail belongs in code, where it is reviewable.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Final

from analyzer.providers.base import CONTEXT_WINDOW

DEFAULT_OLLAMA_HOST: Final = "http://127.0.0.1:11434"


class ConfigError(RuntimeError):
    """A required setting is missing or unusable."""


@dataclass(frozen=True, slots=True)
class Settings:
    database_url: str
    ollama_host: str
    ollama_model: str
    ollama_num_ctx: int
    openrouter_api_key: str | None
    openrouter_model: str | None
    contact_email: str
    cookie_secure: bool
    trust_proxy_ip: bool

    @classmethod
    def from_env(cls) -> Settings:
        database_url = os.environ.get("DATABASE_URL")
        if not database_url:
            raise ConfigError("DATABASE_URL is not set; copy .env.example to .env")

        return cls(
            database_url=database_url,
            ollama_host=os.environ.get("OLLAMA_HOST", DEFAULT_OLLAMA_HOST),
            ollama_model=os.environ.get("OLLAMA_MODEL", ""),
            ollama_num_ctx=int(os.environ.get("OLLAMA_NUM_CTX", CONTEXT_WINDOW)),
            openrouter_api_key=os.environ.get("OPENROUTER_API_KEY") or None,
            openrouter_model=os.environ.get("OPENROUTER_MODEL") or None,
            contact_email=os.environ.get("CONTACT_EMAIL", ""),
            # Secure by default: an installation served over plain HTTP has to say so,
            # rather than a misconfigured deployment silently sending session cookies in
            # the clear. Only local development should ever set this to 0.
            cookie_secure=os.environ.get("COOKIE_SECURE", "1") != "0",
            # Off by default, and the default is the safe one this time round: the front
            # end proxies /api/* to this service, so every request arrives from one
            # container and an address read off the connection identifies nobody. Counting
            # failed sign-ins against it would be one shared budget — an outage, not a
            # limit. See `api.security.client_address`.
            trust_proxy_ip=os.environ.get("TRUST_PROXY_IP", "0") == "1",
        )

    @staticmethod
    def api_docs_enabled() -> bool:
        """Whether to mount /docs, /redoc and /openapi.json.

        Off by default, and read on its own rather than through :meth:`from_env`: FastAPI
        decides which documentation routes exist when the application object is built, which
        happens at import and therefore before anything has assembled the settings.

        The front end proxies every ``/api/*`` path straight through, so docs left on are an
        interactive console and a full route listing served to whoever finds the deployment —
        including the operator paths that answer 404 precisely so as not to confirm they are
        there. ``make api-types`` turns it on for its own run.
        """
        return os.environ.get("API_DOCS", "0") == "1"

    def require_openrouter_key(self) -> str:
        if not self.openrouter_api_key:
            raise ConfigError(
                "OPENROUTER_API_KEY is not set; the cloud baseline cannot run without it"
            )
        return self.openrouter_api_key


def load_dotenv(path: str = ".env") -> None:
    """Populate os.environ from a .env file, without overriding what is already set.

    Small enough to not warrant a dependency, and the precedence matters: a value
    exported in the shell must win over the file, so a one-off run can override config
    without editing it.
    """
    from pathlib import Path

    env_file = Path(path)
    if not env_file.is_file():
        return

    for line in env_file.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        os.environ.setdefault(key.strip(), value.strip())
