import json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.main import app

path = ROOT / "contracts/openapi.json"
path.parent.mkdir(exist_ok=True)
path.write_text(json.dumps(app.openapi(), ensure_ascii=False, indent=2), encoding="utf-8")
print("Exported", path)
