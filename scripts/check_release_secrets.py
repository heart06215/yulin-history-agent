"""Check source and browser artifacts against local credentials without printing them."""

import argparse
import base64
import os
from pathlib import Path
from urllib.parse import quote

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
FIELDS = ("ADMIN_TOKEN", "GLM_API_KEY", "VISION_API_KEY", "TEXT_API_KEY")
SKIP = {".git", ".venv", "node_modules", "data", "exports", "__pycache__", ".agents", ".wrangler", ".pytest_cache", ".ruff_cache", ".vite"}
TEXT_TYPES = {".py", ".ts", ".tsx", ".js", ".mjs", ".cjs", ".json", ".css", ".html", ".md", ".toml", ".yaml", ".yml", ".txt", ".map", ".bat"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, action="append", default=[])
    args = parser.parse_args()
    private_values = set()
    for path in [ROOT / ".env", ROOT / ".env.production", *args.env_file]:
        if not path.is_file():
            continue
        values = dotenv_values(path, encoding="utf-8")
        private_values.update(value for field in FIELDS if (value := values.get(field)) and len(value) >= 8)
    private_values.update(value for field in FIELDS if (value := os.environ.get(field)) and len(value) >= 8)
    patterns = set()
    for value in private_values:
        patterns.update((value.encode(), quote(value, safe="").encode(), base64.b64encode(value.encode())))

    failures = set()
    checked = 0
    for directory, folders, files in os.walk(ROOT):
        folders[:] = [name for name in folders if name not in SKIP and not name.startswith(".local-") and name != "secrets"]
        for name in files:
            path = Path(directory) / name
            relative = path.relative_to(ROOT)
            public = "public" in relative.parts or relative.parts[:4] == ("apps", "web", "dist", "client")
            if public and (name.startswith(".env") or path.suffix.lower() in {".pem", ".key", ".p12", ".pfx"}):
                failures.add(str(relative))
            if name.startswith(".env"):
                if name.endswith(".example"):
                    values = dotenv_values(path, encoding="utf-8")
                    if any(values.get(field) for field in FIELDS):
                        failures.add(str(relative))
                continue
            if path.suffix.lower() not in TEXT_TYPES and not name.startswith("Dockerfile") and name != "Caddyfile":
                continue
            checked += 1
            if patterns and any(pattern in path.read_bytes() for pattern in patterns):
                failures.add(str(relative))
    if failures:
        print("FAIL: private credential or secret file found; file paths only:")
        for relative in sorted(failures):
            print(relative)
        return 1
    print(f"PASS: {checked} source/artifact files checked against {len(private_values)} configured private values; no matches.")
    if not private_values:
        print("No configured values found; rerun with the deployment --env-file before releasing.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
