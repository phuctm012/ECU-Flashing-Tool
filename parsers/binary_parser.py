# ==================================================
# Binary File Parser
# ==================================================
#
# Parses raw binary files (.bin) into a single segment
# starting at a user-specified address.
# ==================================================

import os
import binascii

from parsers.hex_parser import (
    Segment,
    Datablock,
    HexParseError,
)


class BinaryParseError(HexParseError):
    """Raised when a binary file cannot be parsed."""
    pass


def parse_binary_file(file_path, start_address=0x0000):
    """
    Parse a raw binary file into a Datablock.

    Args:
        file_path: Path to the .bin file.
        start_address: Start address for the binary data.

    Returns:
        Datablock object with a single segment.

    Raises:
        BinaryParseError: If the file cannot be parsed.
    """

    if not os.path.exists(file_path):
        raise BinaryParseError(
            f"File not found: {file_path}"
        )

    datablock = Datablock(file_path)

    try:
        with open(file_path, "rb") as f:
            data = f.read()

        if len(data) == 0:
            raise BinaryParseError(
                "File is empty"
            )

        segment = Segment(
            start_address,
            bytearray(data)
        )

        datablock.segments.append(segment)

        # CRC32 checksum
        datablock.checksum = (
            binascii.crc32(data) & 0xFFFFFFFF
        )

    except IOError as e:
        raise BinaryParseError(
            f"Cannot read file: {e}"
        )

    return datablock
