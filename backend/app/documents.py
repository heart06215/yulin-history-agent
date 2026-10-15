import io, threading
from pypdf import PdfReader
from PIL import Image
import pypdfium2 as pdfium
from .repository import asset_path

# PDFium is not thread safe. Both API image serving and the worker use this lock.
render_lock = threading.Lock()


def extract_text(page):
    if asset_path(page).suffix.lower() in (".jpg", ".jpeg", ".png"):
        return ""
    reader = PdfReader(asset_path(page))
    text = reader.pages[page["scan_index"] - 1].extract_text() or ""
    return text[:16000]


def render_page(page, max_width=1800):
    if asset_path(page).suffix.lower() in (".jpg", ".jpeg", ".png"):
        with Image.open(asset_path(page)) as original:
            img = original.convert("RGB")
            img.thumbnail((max_width, 2000))
            out = io.BytesIO()
            img.save(out, format="JPEG", quality=88)
            return out.getvalue()
    with render_lock:
        doc = pdfium.PdfDocument(str(asset_path(page)))
        try:
            pdfpage = doc[page["scan_index"] - 1]
            try:
                scale = min(
                    max_width / pdfpage.get_width(), 2000 / pdfpage.get_height()
                )
                bitmap = pdfpage.render(scale=scale)
                try:
                    img = bitmap.to_pil().convert("RGB")
                    out = io.BytesIO()
                    img.save(out, format="JPEG", quality=88)
                    return out.getvalue()
                finally:
                    bitmap.close()
            finally:
                pdfpage.close()
        finally:
            doc.close()
