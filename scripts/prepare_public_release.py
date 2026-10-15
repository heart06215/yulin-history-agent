"""Export an allowlisted public source tree without touching local runtime data.

Run with the project's Python environment. Never uploads anything. The output
must be new, and is scanned against locally configured credentials before use.
"""

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
from urllib.parse import quote
import zipfile

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
TOP_FILES = {
    ".gitignore", ".dockerignore", ".env.example", ".env.production.example",
    "README.md", "pytest.ini", "启动.bat",
}
TREES = ("apps/web", "backend", "contracts", "deploy", "docs", "scripts")
SKIP_DIRS = {
    ".git", ".agents", ".codex", ".venv", "node_modules", "dist",
    "__pycache__", ".pytest_cache", ".ruff_cache", ".wrangler", ".next",
    ".vinext", ".vite", "screenshots", "secrets",
}
PRIVATE_SUFFIXES = {".log", ".pid", ".pyc", ".pem", ".key", ".p12", ".pfx", ".db", ".sqlite3", ".tsbuildinfo"}
TEXT_SUFFIXES = {".py", ".ts", ".tsx", ".js", ".mjs", ".json", ".css", ".md", ".txt", ".html", ".yaml", ".yml", ".toml", ".bat"}
SECRET_PATTERNS = (
    re.compile(rb"(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})"),
    re.compile(rb"\bsk-[A-Za-z0-9_-]{20,}"),
    re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(rb"https?://[^\s/@:]+:[^\s/@]+@"),
)


def source_files():
    for name in sorted(TOP_FILES):
        yield ROOT / name
    for tree in TREES:
        for folder, dirs, files in os.walk(ROOT / tree, followlinks=False):
            dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith(".local-") and not (Path(folder) / d).is_symlink())
            for name in sorted(files):
                path = Path(folder) / name
                if path.is_symlink() or path.suffix.lower() in PRIVATE_SUFFIXES:
                    continue
                if name.startswith(".env") and not name.endswith(".example"):
                    continue
                yield path
    for name in ("source-catalog.json", "verified-volume-notes.json"):
        yield ROOT / "data/manifests" / name


def credential_patterns():
    values = set()
    fields = ("ADMIN_TOKEN", "GLM_API_KEY", "VISION_API_KEY", "TEXT_API_KEY")
    for path in (ROOT / ".env", ROOT / ".env.production"):
        if path.is_file():
            config = dotenv_values(path, encoding="utf-8")
            values.update(v for key in fields if (v := config.get(key)) and len(v) >= 8)
    values.update(v for key in fields if (v := os.environ.get(key)) and len(v) >= 8)
    return {pattern for v in values for pattern in (v.encode(), quote(v, safe="").encode(), base64.b64encode(v.encode()))}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists():
        raise SystemExit("Output already exists; choose a new directory.")
    if ROOT not in output.parents or output.parts[len(ROOT.parts)] != ".local-exports":
        raise SystemExit("Output must be a new directory inside .local-exports/.")
    output.mkdir(parents=True)
    for source in source_files():
        target = output / source.relative_to(ROOT)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    # Operational download status is deliberately reset; imports start empty.
    (output / "data/manifests/downloads.json").write_text("[]\n", encoding="utf-8")
    patterns = credential_patterns()
    manifest = []
    failures = []
    for path in sorted(output.rglob("*")):
        if not path.is_file():
            continue
        data = path.read_bytes()
        relative = path.relative_to(output).as_posix()
        scan_data = data
        if relative == "backend/tests/test_deployment_security.py":
            # Explicit synthetic fixtures test rejection of credentials in URLs.
            fixture_url = rb"https://" + rb"user:secret@(?:history\.example\.com|provider\.example)"
            scan_data = re.sub(fixture_url, b"https://invalid.example", data)
        if any(p in data for p in patterns) or any(p.search(scan_data) for p in SECRET_PATTERNS):
            failures.append(relative)
        if path.name.startswith(".env"):
            cfg = dotenv_values(path, encoding="utf-8")
            if any(cfg.get(key) for key in ("ADMIN_TOKEN", "GLM_API_KEY", "VISION_API_KEY", "TEXT_API_KEY")):
                failures.append(relative)
        if path.suffix.lower() in TEXT_SUFFIXES:
            text = data.decode("utf-8-sig")
            if re.search(r"[A-Za-z]:[/\\](?:Users|code_project)[/\\]", text):
                failures.append(relative)
        manifest.append({"path": relative, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()})
    if failures:
        print("FAIL: not ready to publish (file paths only):")
        print("\n".join(sorted(set(failures))))
        return 1
    report = {"status": "passed", "files": manifest, "exclusions": ["real environment files and credentials", "runtime databases, raw scans, drafts and transcripts", "logs, backups, local tooling and screenshot captures", "dependencies and build output"]}
    (output.parent / (output.name + "-audit.json")).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    archive = output.parent / (output.name + ".zip")
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
        for entry in manifest:
            bundle.write(output / entry["path"], output.name + "/" + entry["path"])
    print(f"PASS: {len(manifest)} files, {sum(e['bytes'] for e in manifest):,} bytes; no local credential or private path matches.")
    print("Public source:", output)
    print("Archive:", archive)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
