"""Mark pages with chronological/positional metadata.

For each recognized page we record the volume position, the original-file page
number, the leaf label and side, the page type and a reading order inside the
volume. Values come from the verified volume notes and the content read from
the images; unknown values are left empty rather than invented.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.db import init, connect  # noqa: E402

# (page_id, reading_order, leaf_label, side, original_page_no, page_type)
# Reading order is the position of the page within its own volume/册.
MARKS = [
    # 榆林府志 卷三十八上/下（源文件标卷三十九）——经文志，第1页为册封面
    ("commons-125405748-p1", 1, "卷一之上", "封面", 1, "cover"),
    ("commons-125405748-p2", 2, "卷三十八上·一", "正面", 2, "text"),
    ("commons-125405748-p3", 3, "卷三十八·二", "正面", 3, "text"),
    ("commons-125405748-p4", 4, "卷三十八·三", "正面", 4, "text"),
    ("commons-125405748-p5", 5, "卷三十八·四", "正面", 5, "text"),
    ("commons-125405748-p6", 6, "卷三十八·五", "正面", 6, "text"),
    ("commons-125405748-p7", 7, "卷三十八下·一", "正面", 7, "text"),
    ("commons-125405748-p8", 8, "卷三十八下·二", "正面", 8, "text"),
    ("commons-125405748-p9", 9, "卷三十八下·封底", "封底", 9, "blank"),
    # 榆林府志 卷四十一（源文件标卷四十二）——艺文志议、屯政议
    ("commons-125405788-p1", 1, "卷四十一·一", "正面", 1, "text"),
    ("commons-125405788-p2", 2, "卷四十一·二", "正面", 2, "text"),
    ("commons-125405788-p3", 3, "卷四十一·三", "正面", 3, "text"),
    # 米脂县志 卷一 序文首叶
    ("mizhi-1681-v1-sample-p1", 1, "卷一·序首叶", "正面", 1, "text"),
]


def main():
    init()
    with connect() as db:
        for page_id, order, leaf, side, original_no, page_type in MARKS:
            cursor = db.execute(
                "UPDATE pages SET reading_order=?, leaf_label=?, side=?, "
                "original_page_no=?, page_type=?, mapping_basis=? WHERE id=?",
                (order, leaf, side, original_no, page_type, "assisted_reading", page_id),
            )
            print(page_id, "updated" if cursor.rowcount else "MISSING")
        # Record the two-file volume verification fields on the sources.
        db.execute(
            "UPDATE sources SET source_volume=?, verified_volume=? WHERE id=?",
            ("卷三十九", "卷三十八（原文件标卷三十九）", "commons-125405748"),
        )
        db.execute(
            "UPDATE sources SET source_volume=?, verified_volume=? WHERE id=?",
            ("卷四十二", "卷四十一（原文件标卷四十二）", "commons-125405788"),
        )
    print("done")


if __name__ == "__main__":
    main()
