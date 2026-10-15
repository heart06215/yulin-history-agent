"""Download bounded, provenance-preserving Commons gazetteer collections; no scraping bypass."""

import argparse, hashlib, json, os, time, urllib.parse, urllib.request, urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HEADERS = {
    "User-Agent": "YulinGazetteerResearch/0.1 (local educational archive; Wikimedia Commons API)"
}


def get(url):
    for attempt in range(3):
        try:
            with urllib.request.urlopen(
                urllib.request.Request(url, headers=HEADERS), timeout=90
            ) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code in (403, 429):
                raise
            if attempt == 2:
                raise
            time.sleep(2**attempt)
        except Exception:
            if attempt == 2:
                raise
            time.sleep(2**attempt)


def api(**kw):
    return json.loads(
        get(
            "https://commons.wikimedia.org/w/api.php?"
            + urllib.parse.urlencode({"format": "json", **kw})
        )
    )


def category(name):
    result = []
    extra = {}
    while True:
        r = api(
            action="query",
            list="categorymembers",
            cmtitle="Category:" + name,
            cmlimit=100,
            **extra,
        )
        result += r["query"]["categorymembers"]
        if "continue" not in r:
            return result
        extra = r["continue"]


def collect():
    candidates = []
    for category_name, prefix, edition_id, title, year in [
        ("榆林府志", "NLC403-312001065669", "yulin-1841", "榆林府志", 1841),
        ("米脂縣志", "NLC403-312001065687", "mizhi-1681", "米脂县志", 1681),
    ]:
        files = [
            x["title"]
            for x in category(category_name)
            if x["ns"] == 6 and prefix in x["title"] and x["title"].endswith(".pdf")
        ]
        records = []
        for i in range(0, len(files), 20):
            r = api(
                action="query",
                prop="imageinfo",
                titles="|".join(files[i : i + 20]),
                iiprop="url|size|sha1|extmetadata",
                iilimit=1,
            )
            for page in r["query"]["pages"].values():
                info = page["imageinfo"][0]
                meta = info.get("extmetadata", {})
                vol = page["title"].rsplit(" ", 1)[-1].removesuffix(".pdf")
                records.append(
                    {
                        "id": f"commons-{page['pageid']}",
                        "edition_id": edition_id,
                        "title": title,
                        "year": year,
                        "volume": vol,
                        "file_title": page["title"],
                        "source_url": info["descriptionurl"],
                        "download_url": info["url"],
                        "remote_sha1": info["sha1"],
                        "size_bytes": info["size"],
                        "rights_status": "source_declared_public_domain"
                        if "public domain" in json.dumps(meta).lower()
                        or "pd-" in json.dumps(meta).lower()
                        else "pending",
                        "rights_metadata": meta,
                    }
                )
        best = {}
        for r in records:
            if (
                r["volume"] not in best
                or r["size_bytes"] > best[r["volume"]]["size_bytes"]
            ):
                best[r["volume"]] = r
        candidates.extend(sorted(best.values(), key=lambda r: r["file_title"]))
    return candidates


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog-only", action="store_true")
    parser.add_argument("--limit", type=int, default=3)
    parser.add_argument("--edition")
    args = parser.parse_args()
    path = ROOT / "data/manifests/source-catalog.json"
    if path.exists():
        records = json.loads(path.read_text())
    else:
        records = collect()
        path.write_text(json.dumps(records, ensure_ascii=False, indent=2))
    print(
        "Catalog:",
        len(records),
        "PDF parts;",
        round(sum(r["size_bytes"] for r in records) / 1024**2, 1),
        "MiB",
        flush=True,
    )
    print(
        "Volumes:",
        {
            edition: sorted(r["volume"] for r in records if r["edition_id"] == edition)
            for edition in ["yulin-1841", "mizhi-1681"]
        },
        flush=True,
    )
    if args.catalog_only:
        return
    out = ROOT / "data/raw"
    out.mkdir(parents=True, exist_ok=True)

    def download(r):
        r = dict(r)
        p = out / (r["id"] + ".pdf")
        try:
            if not p.exists():
                raw = get(r["download_url"])
                if not raw.startswith(b"%PDF-"):
                    raise ValueError("Not a PDF")
                tmp = p.with_suffix(".part")
                tmp.write_bytes(raw)
                tmp.replace(p)
            raw = p.read_bytes()
            if len(raw) != r["size_bytes"]:
                raise ValueError("Length mismatch")
            if hashlib.sha1(raw).hexdigest() != r["remote_sha1"]:
                raise ValueError("SHA1 mismatch")
            r.update(
                local_path=str(p.relative_to(ROOT)),
                sha256=hashlib.sha256(raw).hexdigest(),
                downloaded_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                status="downloaded",
            )
            print("OK", r["title"], r["volume"], flush=True)
        except Exception as e:
            r.update(status="failed", error=str(e))
            print("FAIL", r["id"], str(e), flush=True)
        return r

    manifest = ROOT / "data/manifests/downloads.json"
    previous = json.loads(manifest.read_text()) if manifest.exists() else []
    by_id = {r["id"]: r for r in previous}
    for r in records:
        by_id.setdefault(r["id"], r)
    # Slower cadence by default: the source asked for less disruptive access.
    delay = float(os.environ.get("DOWNLOAD_DELAY") or 20)
    selected = [
        r
        for r in records
        if by_id[r["id"]].get("status") != "downloaded"
        and (not args.edition or r["edition_id"] == args.edition)
    ][: max(0, args.limit)]
    for r in selected:
        result = download(r)
        by_id[r["id"]] = result
        manifest.write_text(
            json.dumps(list(by_id.values()), ensure_ascii=False, indent=2)
        )
        if result.get("status") == "failed":
            print(
                "Paused after upstream failure. Keep files and retry only after the source permits it.",
                flush=True,
            )
            break
        time.sleep(delay)
    results = list(by_id.values())
    manifest.write_text(json.dumps(results, ensure_ascii=False, indent=2))
    print(
        "Complete:",
        sum(r.get("status") == "downloaded" for r in results),
        "/",
        len(results),
        flush=True,
    )
    if any(r.get("status") == "failed" for r in results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
