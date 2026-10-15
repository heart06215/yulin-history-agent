"""Use Commons standard-size page previews, the alternative suggested by its rate-limit response."""

import hashlib, json, time, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    catalog = json.loads((ROOT / "data/manifests/source-catalog.json").read_text())
    ref = next(x for x in catalog if "55613" in x["file_title"])
    manifest = ROOT / "data/manifests/downloads.json"
    rows = json.loads(manifest.read_text())
    old = {r["id"]: r for r in rows}
    name = "NLC403-312001065687-55613_%E7%B1%B3%E8%84%82%E7%B8%A3%E8%AA%8C_%E6%B8%85%E5%BA%B7%E7%86%9920%E5%B9%B4%281681%29_%E5%8D%B7%E4%B8%80.pdf"
    for i in (1, 2, 3):
        ident = "mizhi-1681-v1-sample" if i == 1 else f"mizhi-1681-v1-sample-p{i}"
        if old.get(ident, {}).get("status") == "downloaded":
            continue
        url = f"https://thumb.wikimedia.org/wikipedia/commons/thumb/e/eb/{name}/page{i}-1920px-{name}.jpg"
        try:
            with urllib.request.urlopen(
                urllib.request.Request(
                    url, headers={"User-Agent": "YulinGazetteerResearch/0.1"}
                ),
                timeout=40,
            ) as r:
                raw = r.read()
            if not raw.startswith(b"\xff\xd8"):
                raise ValueError("Not JPEG")
            path = ROOT / f"data/raw/mizhi-1681-v1-page{i}.jpg"
            path.write_bytes(raw)
            record = {
                **ref,
                "id": ident,
                "volume": f"卷一 · 原文件扫描第{i}页样本",
                "local_path": str(path.relative_to(ROOT)),
                "download_url": url,
                "sha256": hashlib.sha256(raw).hexdigest(),
                "size_bytes": len(raw),
                "asset_kind": "image_sample",
                "original_scan_index": i,
                "status": "downloaded",
                "downloaded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "note": "标准尺寸页面预览图，非完整分卷；本材料内部页码1对应原PDF扫描第"
                + str(i)
                + "页。",
            }
            record.pop("remote_sha1", None)
            old[ident] = record
            manifest.write_text(
                json.dumps(list(old.values()), ensure_ascii=False, indent=2)
            )
            print("Downloaded Mizhi sample", i, flush=True)
            time.sleep(5)
        except Exception as e:
            print("Stopped:", str(e), flush=True)
            break


if __name__ == "__main__":
    main()
