import pytest
from app.config import settings
from app.data_paths import data_file


@pytest.mark.parametrize("stored", ["raw/book.pdf", "raw\\book.pdf", "raw\\nested/../book.pdf"])
def test_existing_windows_and_posix_paths_resolve_to_same_material(tmp_path, monkeypatch, stored):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    material = tmp_path / "raw" / "book.pdf"
    material.parent.mkdir()
    material.write_bytes(b"private material")
    assert data_file(stored) == material.resolve()


@pytest.mark.parametrize("stored", ["", "../secret", "..\\secret", "/etc/passwd", "C:\\private\\book.pdf", "C:book.pdf", "\\\\server\\share\\book.pdf", "raw/missing.pdf"])
def test_material_path_cannot_escape_private_root(tmp_path, monkeypatch, stored):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    with pytest.raises(ValueError, match="ASSET_NOT_FOUND"):
        data_file(stored)
