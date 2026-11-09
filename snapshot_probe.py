"""Collect the 11 known mismatching references from the verified CI renderer."""
from pathlib import Path
from shutil import copyfile

from PIL import ImageChops, __version__ as pillow_version, features
import pytest

TEST_FILES = (
    "tests/drawcustom/text_test.py",
    "tests/drawcustom/text_multiline_test.py",
)
IMAGE_ROOT = Path("tests/drawcustom/test_images").resolve()
ARTIFACT_ROOT = Path("/references")
EXPECTED = {
    Path("text") / f"{name}.png"
    for name in (
        "small_font", "large_font", "text_wrapping", "text_wrapping_anchor",
        "text_special_chars", "text_percentage", "text_anchors", "text_truncate",
    )
} | {
    Path("text_multiline") / f"{name}.png"
    for name in (
        "text_multiline_delimiter", "multiline_empty_line",
        "text_multiline_delimiter_and_newline",
    )
}

class SnapshotProbe:
    generated = set()

    def pytest_collection_modifyitems(self, items):
        for item in items:
            item.module.images_equal = self.compare
            if hasattr(item.module, "save_image"):
                item.module.save_image = lambda *_: None

    def compare(self, actual, expected):
        path = Path(expected.filename).resolve().relative_to(IMAGE_ROOT)
        encoded = actual.fp.getvalue()
        diff = ImageChops.difference(actual, expected)
        if diff.getbbox() is None:
            return True
        if path not in EXPECTED:
            raise AssertionError(f"Unexpected snapshot mismatch: {path}")
        if actual.size != expected.size or actual.format != expected.format:
            raise AssertionError(f"Snapshot shape or encoding changed: {path}")
        output = ARTIFACT_ROOT / path
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(encoded)
        self.generated.add(path)
        return False

print(f"Pillow {pillow_version}, FreeType {features.version_module('freetype2')}, "
      f"JPEG {features.version_codec('jpg')}, Raqm {features.check_feature('raqm')}", flush=True)
probe = SnapshotProbe()
status = pytest.main(["-q", "--tb=no", *TEST_FILES], plugins=[probe])
if status != pytest.ExitCode.TESTS_FAILED or probe.generated != EXPECTED:
    raise SystemExit(f"Unexpected reference set: {probe.generated}")
for path in EXPECTED:
    copyfile(ARTIFACT_ROOT / path, IMAGE_ROOT / path)
