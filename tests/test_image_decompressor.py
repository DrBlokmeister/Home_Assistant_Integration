import importlib.util
import sys
import types
from pathlib import Path
from types import SimpleNamespace
import zlib


def load_decoder():
    """Import decode_esl_raw with minimal package scaffolding."""
    pkg = types.ModuleType("custom_components")
    sys.modules["custom_components"] = pkg
    open_pkg = types.ModuleType("custom_components.open_epaper_link")
    open_pkg.__path__ = []
    sys.modules["custom_components.open_epaper_link"] = open_pkg
    stub_tag_types = types.ModuleType(
        "custom_components.open_epaper_link.tag_types"
    )
    stub_tag_types.TagType = object
    sys.modules[
        "custom_components.open_epaper_link.tag_types"
    ] = stub_tag_types

    spec = importlib.util.spec_from_file_location(
        "custom_components.open_epaper_link.image_decompressor",
        Path(__file__).resolve().parent.parent
        / "custom_components/open_epaper_link/image_decompressor.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    # Clean up stubs so other tests can import the real package
    for name in [
        "custom_components.open_epaper_link.tag_types",
        "custom_components.open_epaper_link",
        "custom_components",
    ]:
        sys.modules.pop(name, None)
    return module.decode_esl_raw


def make_tag(bpp=1):
    return SimpleNamespace(
        name="test",
        width=8,
        height=8,
        bpp=bpp,
        rotatebuffer=0,
        color_table={"white": [255, 255, 255], "black": [0, 0, 0]},
    )


def encode_g5(block: bytes) -> bytes:
    out = bytearray()
    i = 0
    while i < len(block):
        chunk = block[i : i + 127]
        out.append(len(chunk) - 1)
        out.extend(chunk)
        i += len(chunk)
    return bytes(out)


def test_decode_esl_raw_full_zlib():
    decode_esl_raw = load_decoder()
    tag = make_tag(1)
    plane = bytes([0xAA] * 8)
    block = b"\x00\x00\x08\x00\x00\x00" + plane
    payload = zlib.compress(block)
    data = len(payload).to_bytes(4, "little") + payload
    result = decode_esl_raw(data, tag)
    assert result == plane


def test_decode_esl_raw_partial_zlib_rows():
    decode_esl_raw = load_decoder()
    tag = make_tag(1)
    plane = bytearray(8)
    plane[2:5] = b"\x11\x22\x33"
    block = b"\x02\x00\x03\x00\x00\x00" + b"\x11\x22\x33"
    payload = zlib.compress(block)
    data = len(payload).to_bytes(4, "little") + payload
    result = decode_esl_raw(data, tag)
    assert result == bytes(plane)


def test_decode_esl_raw_two_planes():
    decode_esl_raw = load_decoder()
    tag = make_tag(2)
    plane_a = bytes([0x00] * 8)
    plane_b = bytes([0xFF] * 8)
    block_a = b"\x00\x00\x08\x00\x00\x00" + plane_a
    block_b = b"\x00\x00\x08\x00\x00\x01" + plane_b
    data = (
        len(block_a).to_bytes(4, "little")
        + block_a
        + len(block_b).to_bytes(4, "little")
        + block_b
    )
    result = decode_esl_raw(data, tag)
    assert result == plane_a + plane_b


def test_decode_esl_raw_g5():
    decode_esl_raw = load_decoder()
    tag = make_tag(1)
    plane = bytes([0xAA] * 8)
    block = b"\x00\x00\x08\x00\x00\x00" + plane
    payload = encode_g5(block)
    data = len(payload).to_bytes(4, "little") + payload
    result = decode_esl_raw(data, tag)
    assert result == plane
