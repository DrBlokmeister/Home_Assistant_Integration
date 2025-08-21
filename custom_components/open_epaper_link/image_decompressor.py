"""Image decoder for OpenEPaperLink raw image format."""
from __future__ import annotations

import io
import logging
import struct
import zlib

from PIL import Image

from .tag_types import TagType

_LOGGER = logging.getLogger(__name__)


def decode_esl_raw(data: bytes, tag_type: TagType) -> bytes:
    """Decode an OpenEPaperLink raw file.

    The AP transfers image data as a sequence of blocks.  Each block is
    prefixed with a 4‑byte little-endian length and the payload may be
    compressed using either zlib or the custom "G5" scheme.  After
    decompression, every block begins with a 6 byte header encoded as
    ``<HHBB`` which specifies the starting line, the number of rows,
    format and flags.  Blocks may cover only a portion of the display and
    multiple blocks (and planes for 2bpp displays) must be assembled into
    a full frame.

    Args:
        data: Raw image data bytes from the AP
        tag_type: TagType object containing display specifications

    Returns:
        bytes: Decoded raw bitmap data ready for rendering
    """

    _LOGGER.debug("Input size: %d bytes", len(data))
    _LOGGER.debug("Tag type: %s", tag_type.name)
    _LOGGER.debug("Dimensions: %dx%d", tag_type.width, tag_type.height)
    _LOGGER.debug("BPP: %d", tag_type.bpp)
    _LOGGER.debug("Rotate buffer: %d", tag_type.rotatebuffer)

    width = tag_type.height if tag_type.rotatebuffer % 2 else tag_type.width
    height = tag_type.width if tag_type.rotatebuffer % 2 else tag_type.height

    if tag_type.bpp <= 2:
        bytes_per_row = (width + 7) // 8
        plane_buffers = [bytearray(bytes_per_row * height)]
        if tag_type.bpp == 2:
            plane_buffers.append(bytearray(bytes_per_row * height))
    else:
        bits_per_pixel = tag_type.bpp
        bytes_per_row = (width * bits_per_pixel + 7) // 8
        plane_buffers = [bytearray(bytes_per_row * height)]

    _LOGGER.debug("Effective dimensions: %dx%d", width, height)
    _LOGGER.debug("Bytes per row: %d", bytes_per_row)

    # Iterate over size-prefixed blocks
    offset = 0
    block_index = 0
    next_plane = 0
    while offset + 4 <= len(data):
        block_size = int.from_bytes(data[offset:offset + 4], "little")
        offset += 4
        remaining = len(data) - offset
        if block_size > remaining:
            _LOGGER.debug(
                "Block %d: size %d exceeds remaining %d, clamping",
                block_index,
                block_size,
                remaining,
            )
            block_size = remaining
        payload = data[offset:offset + block_size]
        offset += block_size
        _LOGGER.debug("Block %d: payload size %d bytes", block_index, block_size)

        codec = "raw"
        block = payload
        if (
            len(payload) >= 2
            and payload[0] == 0x78
            and payload[1] in (0x01, 0x9C, 0xDA)
        ):
            codec = "zlib"
            block = zlib.decompress(payload)
        else:
            if len(payload) < 6:
                _LOGGER.debug("Block %d: too small", block_index)
                block_index += 1
                continue
            y0, nrows, fmt, flags = struct.unpack("<HHBB", payload[:6])
            if fmt & 0x01:
                expected = nrows * bytes_per_row
                try:
                    rows = decode_g5(payload[6:], expected)
                    block = payload[:6] + rows
                    codec = "g5"
                except Exception:  # pragma: no cover - unsupported g5
                    block = payload
            else:
                block = payload

        _LOGGER.debug(
            "Block %d: codec %s, decompressed to %d bytes",
            block_index,
            codec,
            len(block),
        )

        if len(block) < 6:
            _LOGGER.debug("Block %d: too small after decompression", block_index)
            block_index += 1
            continue

        y0, nrows, fmt, flags = struct.unpack("<HHBB", block[:6])
        _LOGGER.debug(
            "Block %d: y0=%d nrows=%d fmt=%d flags=0x%02X",
            block_index,
            y0,
            nrows,
            fmt,
            flags,
        )

        plane = 0
        if tag_type.bpp == 2:
            if flags & 0x1 in (0, 1):
                plane = flags & 0x1
            else:
                plane = next_plane
            next_plane = 1 - plane

        start = min(y0, height) * bytes_per_row
        end_row = min(y0 + nrows, height)
        end = end_row * bytes_per_row
        data_bytes = block[6:6 + (end_row - y0) * bytes_per_row]
        expected = (end_row - y0) * bytes_per_row
        if len(data_bytes) < expected:
            _LOGGER.debug(
                "Block %d: payload shorter than expected (%d < %d), clamping",
                block_index,
                len(data_bytes),
                expected,
            )
            expected = len(data_bytes)
            end = start + expected

        _LOGGER.debug(
            "Block %d: placing %d bytes into plane %d at %d-%d",
            block_index,
            expected,
            plane,
            start,
            end,
        )

        plane_buffers[plane][start:end] = data_bytes[:expected]
        block_index += 1

    result = bytes(plane_buffers[0])
    if tag_type.bpp == 2:
        result += bytes(plane_buffers[1])
    return result


def decode_g5(data: bytes, expected: int) -> bytes:
    """Decode the run-length encoded "G5" stream used by OpenEPaperLink.

    Args:
        data: Compressed row data without the 6-byte header.
        expected: Expected number of output bytes. The decoder stops once
            this many bytes have been produced.

    Returns:
        bytes: Decompressed row data.
    """

    out = bytearray()
    i = 0
    while i < len(data) and len(out) < expected:
        cmd = data[i]
        i += 1
        if cmd & 0x80:  # repeat
            count = (cmd & 0x0F) + 1
            colour = 0xFF if cmd & 0x40 else 0x00
            out.extend([colour] * min(count, expected - len(out)))
        else:  # literal
            count = (cmd & 0x7F) + 1
            chunk = data[i : i + count]
            out.extend(chunk[: expected - len(out)])
            i += count
    return bytes(out)


def to_image(raw_data: bytes, tag_type: TagType) -> bytes:
    """Convert decoded ESL raw data to JPEG image.

    Transforms the decoded raw bitmap data into a standard JPEG image
    that can be displayed in Home Assistant or saved to disk.

    The conversion process:

    1. Decodes the raw data using decode_esl_raw
    2. Creates a new PIL Image with appropriate dimensions
    3. Processes pixels based on the tag's color depth and format
    4. Applies rotation according to the tag's buffer rotation setting
    5. Converts to JPEG format

    The color mapping depends on the tag type's color table,
    which defines the available colors for different bit values.

    Args:
        raw_data: Raw image data from the AP
        tag_type: TagType object with display specifications

    Returns:
        bytes: JPEG image data

    Raises:
        Exception: For image processing errors or invalid color format
    """
    data = decode_esl_raw(raw_data, tag_type)

    # For 90/270 degree rotated displays, swap width/height before processing
    native_width = tag_type.width
    native_height = tag_type.height
    if tag_type.rotatebuffer % 2:  # 90 or 270 degrees
        native_width, native_height = native_height, native_width

    _LOGGER.debug("\n=== Color Table Information ===")
    _LOGGER.debug(f"Color table contents: {tag_type.color_table}")

    # Create initial image
    img = Image.new('RGB', (native_width, native_height), 'white')
    pixels = img.load()

    # Convert color table to RGB tuples
    color_table = {k: tuple(v) for k, v in tag_type.color_table.items()}

    _LOGGER.debug(f"Available colors: {list(color_table.keys())}")

    # Process pixels based on color depth
    if tag_type.bpp <= 2:  # Traditional 1-2 bit plane-based format
        bytes_per_row = (native_width + 7) // 8
        bytes_per_plane = bytes_per_row * native_height

        # Split into planes for 2bpp mode
        black_plane = data[:bytes_per_plane]
        color_plane = (
            data[bytes_per_plane:bytes_per_plane * 2]
            if tag_type.bpp == 2
            else None
        )

        # Process pixels
        for y in range(native_height):
            row_offset = y * bytes_per_row
            for x in range(native_width):
                byte_offset = row_offset + (x // 8)
                bit_mask = 0x80 >> (x % 8)

                black = bool(black_plane[byte_offset] & bit_mask)
                color = (
                    bool(color_plane[byte_offset] & bit_mask)
                    if color_plane
                    else False
                )

                if black and color:
                    pixels[x, y] = color_table['black']  # Overlap
                elif black:
                    pixels[x, y] = color_table['black']
                elif color:
                    # Use first available color that's not black or white
                    color_key = next((k for k in color_table.keys()
                                      if k not in ['black', 'white']), 'white')
                    pixels[x, y] = color_table[color_key]
                else:
                    pixels[x, y] = color_table['white']

    else:  # 3-4 bit packed format
        bits_per_pixel = tag_type.bpp
        bit_mask = (1 << bits_per_pixel) - 1
        bytes_per_row = (native_width * bits_per_pixel + 7) // 8

        # Convert color table to list for indexed access
        colors_list = list(color_table.values())

        for y in range(native_height):
            for x in range(native_width):
                # Calculate byte and bit positions
                bit_position = (x * bits_per_pixel) % 8
                byte_offset = (y * bytes_per_row) + (x * bits_per_pixel) // 8

                if byte_offset < len(data):
                    # Extract the color index
                    if bit_position + bits_per_pixel <= 8:
                        # Color index is contained within a single byte
                        color_index = (
                            data[byte_offset]
                            >> (8 - bit_position - bits_per_pixel)
                        ) & bit_mask
                    else:
                        # Color index spans two bytes
                        first_byte = data[byte_offset] & (
                            (1 << (8 - bit_position)) - 1
                        )
                        bits_from_first = 8 - bit_position
                        bits_from_second = bits_per_pixel - bits_from_first
                        if byte_offset + 1 < len(data):
                            second_byte = data[byte_offset + 1] >> (
                                8 - bits_from_second
                            )
                            color_index = (
                                first_byte << bits_from_second
                            ) | second_byte
                        else:
                            color_index = first_byte << bits_from_second

                    # Set pixel color
                    if color_index < len(colors_list):
                        pixels[x, y] = colors_list[color_index]

    # Apply rotation
    if tag_type.rotatebuffer == 1:  # 90 degrees CCW
        img = img.transpose(Image.Transpose.ROTATE_270)
    elif tag_type.rotatebuffer == 2:  # 180 degrees
        img = img.transpose(Image.Transpose.ROTATE_180)
    elif tag_type.rotatebuffer == 3:  # 270 degrees CCW (90 CW)
        img = img.transpose(Image.Transpose.ROTATE_90)

    # Convert to JPEG
    output = io.BytesIO()
    img.save(output, format='JPEG', quality=95)
    output.seek(0)
    return output.read()
