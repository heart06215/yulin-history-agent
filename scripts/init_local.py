from pathlib import Path
import secrets

ROOT = Path(__file__).resolve().parents[1]
p = ROOT / ".env"
if not p.exists():
    p.write_text(
        (ROOT / ".env.example")
        .read_text()
        .replace("ADMIN_TOKEN=\n", "ADMIN_TOKEN=" + secrets.token_urlsafe(32) + "\n")
    )
    p.chmod(0o600)
    print(
        "Created .env with a private local admin token. Set GLM_API_KEY here when ready."
    )
else:
    print(".env exists; left unchanged.")
