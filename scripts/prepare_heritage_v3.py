"""Split generated atlases into native web assets; preserve transparency."""
import json
from pathlib import Path
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
manifest = json.loads((ROOT / "docs/heritage-assets-v3.json").read_text(encoding="utf-8"))
output = ROOT / "apps/web/public/images"
for asset in manifest["assets"]:
    image = Image.open(asset["source_path"])
    columns, rows = (2, 1) if asset["key"] == "road-materials-v3" else (2, 4)
    for index, name in enumerate(asset["outputs"]):
        column, row = index % columns, index // columns
        bounds = (round(column * image.width / columns), round(row * image.height / rows), round((column + 1) * image.width / columns), round((row + 1) * image.height / rows))
        target = output / name
        target.parent.mkdir(parents=True, exist_ok=True)
        image.crop(bounds).save(target, "WEBP", quality=86, method=6)
        print(f"{name}: {target.stat().st_size:,} bytes")
