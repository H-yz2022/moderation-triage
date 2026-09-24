import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from modtriage.config import get_settings  # noqa: E402

DEV = ROOT / "data" / "sample" / "dev_sample.jsonl"


def make_settings(**kw):
    tmp = Path(tempfile.mkdtemp())
    base = dict(
        db_path=tmp / "t.db",
        gate_path=tmp / "missing_gate.joblib",
        reports_dir=tmp / "reports",
        provider="mock",
        mode="cascade",
        run_budget_usd=5.0,
    )
    base.update(kw)
    return get_settings(**base)
