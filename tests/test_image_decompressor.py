import importlib.util
import sys
import types
from pathlib import Path
from types import SimpleNamespace

# Set up minimal package structure so relative imports resolve
pkg = types.ModuleType("custom_components")
sys.modules["custom_components"] = pkg
open_pkg = types.ModuleType("custom_components.open_epaper_link")
open_pkg.__path__ = []
sys.modules["custom_components.open_epaper_link"] = open_pkg
# Stub tag_types module required by image_decompressor
stub_tag_types = types.ModuleType("custom_components.open_epaper_link.tag_types")
stub_tag_types.TagType = object
sys.modules["custom_components.open_epaper_link.tag_types"] = stub_tag_types

spec = importlib.util.spec_from_file_location(
    "custom_components.open_epaper_link.image_decompressor",
    Path(__file__).resolve().parent.parent / "custom_components/open_epaper_link/image_decompressor.py",
)
image_decompressor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(image_decompressor)
decode_esl_raw = image_decompressor.decode_esl_raw


def test_decode_esl_raw_uncompressed_header_removed():
    tag_type = SimpleNamespace(
        name="test",
        width=8,
        height=8,
        bpp=1,
        rotatebuffer=0,
        color_table={'white': [255, 255, 255], 'black': [0, 0, 0]},
    )

    plane = bytes([0xAA] * 8)
    raw = (0).to_bytes(4, 'little') + b'\x00' * 6 + plane

    result = decode_esl_raw(raw, tag_type)
    assert result == plane
    assert len(result) == len(plane)
