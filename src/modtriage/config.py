"""Runtime configuration, read from environment variables (and an optional .env)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load_dotenv(path: Path) -> None:
    """Tiny .env loader so we don't need python-dotenv. Existing env wins."""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv(ROOT / ".env")


def _f(name: str, default: float) -> float:
    return float(os.environ.get(name, default))


def _i(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


def _b(name: str, default: bool) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Settings:
    # --- models -----------------------------------------------------------
    # "mock" runs the whole system offline with deterministic heuristic agents.
    provider: str = field(default_factory=lambda: os.environ.get("MODTRIAGE_PROVIDER", "auto"))
    anthropic_api_key: str = field(default_factory=lambda: os.environ.get("ANTHROPIC_API_KEY", ""))
    specialist_model: str = field(
        default_factory=lambda: os.environ.get("MODTRIAGE_SPECIALIST_MODEL", "claude-haiku-4-5")
    )
    arbiter_model: str = field(default_factory=lambda: os.environ.get("MODTRIAGE_ARBITER_MODEL", "claude-sonnet-5"))
    llm_timeout_s: float = field(default_factory=lambda: _f("MODTRIAGE_LLM_TIMEOUT_S", 30.0))
    metadata_agent_uses_llm: bool = field(default_factory=lambda: _b("MODTRIAGE_METADATA_LLM", False))

    # --- routing / voting ---------------------------------------------------
    mode: str = field(default_factory=lambda: os.environ.get("MODTRIAGE_MODE", "cascade"))
    gate_allow_below: float = field(default_factory=lambda: _f("MODTRIAGE_GATE_ALLOW_BELOW", 0.04))
    remove_threshold: float = field(default_factory=lambda: _f("MODTRIAGE_REMOVE_THRESHOLD", 0.45))
    allow_threshold: float = field(default_factory=lambda: _f("MODTRIAGE_ALLOW_THRESHOLD", 0.45))
    arbiter_min_confidence: float = field(default_factory=lambda: _f("MODTRIAGE_ARBITER_MIN_CONFIDENCE", 0.7))
    high_severity_escalate_conf: float = field(default_factory=lambda: _f("MODTRIAGE_HIGH_SEVERITY_ESCALATE_CONF", 0.5))

    # --- cost control -------------------------------------------------------
    run_budget_usd: float = field(default_factory=lambda: _f("MODTRIAGE_RUN_BUDGET_USD", 5.0))
    max_daily_llm_calls: int = field(default_factory=lambda: _i("MODTRIAGE_MAX_DAILY_LLM_CALLS", 2000))
    max_text_chars: int = field(default_factory=lambda: _i("MODTRIAGE_MAX_TEXT_CHARS", 4000))
    use_cache: bool = field(default_factory=lambda: _b("MODTRIAGE_USE_CACHE", True))

    # --- paths --------------------------------------------------------------
    policy_path: Path = field(
        default_factory=lambda: Path(os.environ.get("MODTRIAGE_POLICY", ROOT / "policy" / "community_policy.yaml"))
    )
    db_path: Path = field(default_factory=lambda: Path(os.environ.get("MODTRIAGE_DB", ROOT / "data" / "modtriage.db")))
    data_dir: Path = field(default_factory=lambda: Path(os.environ.get("MODTRIAGE_DATA", ROOT / "data")))
    gate_path: Path = field(
        default_factory=lambda: Path(os.environ.get("MODTRIAGE_GATE", ROOT / "data" / "models" / "gate.joblib"))
    )
    reports_dir: Path = field(default_factory=lambda: Path(os.environ.get("MODTRIAGE_REPORTS", ROOT / "reports")))

    @property
    def resolved_provider(self) -> str:
        if self.provider == "auto":
            return "anthropic" if self.anthropic_api_key else "mock"
        return self.provider


def get_settings(**overrides) -> Settings:
    s = Settings()
    for k, v in overrides.items():
        if not hasattr(s, k):
            raise AttributeError(f"unknown setting {k}")
        setattr(s, k, v)
    return s
