"""Self-host the official, Unicode-subset Noto Chinese web fonts and licenses."""

from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from pathlib import Path
import re
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from urllib.error import URLError
import time


ROOT = Path(__file__).resolve().parents[1]
DESTINATION = ROOT / "apps/web/public/fonts"
CSS_URL = "https://fonts.googleapis.com/css2?family=Noto+Sans+SC:wght@400..700&family=Noto+Serif+SC:wght@500..700&display=swap"
USER_AGENT = "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"


def fetch(url: str) -> bytes:
    for attempt in range(4):
        try:
            with urlopen(Request(url, headers={"User-Agent": USER_AGENT}), timeout=35) as response:
                return response.read()
        except (URLError, TimeoutError):
            if attempt == 3:
                raise
            time.sleep(attempt + 1)
    raise RuntimeError("Font request did not complete")


def main() -> None:
    DESTINATION.mkdir(parents=True, exist_ok=True)
    css = fetch(CSS_URL).decode("utf-8")
    urls = sorted(set(re.findall(r"url\((https://[^)]+)\)", css)))
    names = {url: f"noto-{sha256(url.encode()).hexdigest()[:16]}.woff2" for url in urls}

    def download(url: str) -> None:
        if urlsplit(url).netloc != "fonts.gstatic.com":
            raise ValueError("Unexpected font asset host")
        path = DESTINATION / names[url]
        if path.exists() and path.read_bytes()[:4] == b"wOF2":
            return
        content = fetch(url)
        if content[:4] != b"wOF2":
            raise ValueError("Expected WOFF2 font")
        path.write_bytes(content)

    with ThreadPoolExecutor(max_workers=6) as executor:
        list(executor.map(download, urls))
    for url, name in names.items():
        css = css.replace(url, f"/fonts/{name}")
    (ROOT / "apps/web/app/fonts.css").write_text("/* Official Noto SC fonts; self-hosted Unicode subsets, SIL OFL 1.1. */\n" + css, encoding="utf-8")
    for family in ("notosanssc", "notoserifsc"):
        (DESTINATION / f"{family}-OFL.txt").write_bytes(fetch(f"https://raw.githubusercontent.com/google/fonts/main/ofl/{family}/OFL.txt"))
    print(f"Self-hosted {len(urls)} WOFF2 subsets; total {sum(path.stat().st_size for path in DESTINATION.glob('*.woff2')) / 1024 / 1024:.1f} MB. Browser downloads only matching Unicode ranges.")


if __name__ == "__main__":
    main()
