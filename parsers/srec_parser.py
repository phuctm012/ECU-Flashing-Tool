# ==================================================
# Motorola S-Record Parser
# ==================================================
#
# Parses S-Record files (.s19, .srec, .s37) into
# segments containing start address and binary data.
#
# Reference: https://en.wikipedia.org/wiki/SREC_(file_format)
# ==================================================

import os
import binascii

from parsers.hex_parser import (
    Segment,
    Datablock,
    HexParseError,
)


class SrecParseError(HexParseError):
    """Raised when an S-Record file cannot be parsed."""
    pass


def parse_srec_file(file_path, gap_threshold=256):
    """
    Parse a Motorola S-Record file into a Datablock.

    S-Record format:
        Sn LL AAAA...AA DD...DD CC
        n  = record type (0-9)
        LL = byte count (address + data + checksum)
        AA = address (16/24/32 bit depending on type)
        DD = data bytes
        CC = checksum (1's complement of sum)

    Record types:
        S0 = Header
        S1 = Data (16-bit address)
        S2 = Data (24-bit address)
        S3 = Data (32-bit address)
        S5 = Record count (16-bit)
        S7 = End (32-bit start address)
        S8 = End (24-bit start address)
        S9 = End (16-bit start address)

    Args:
        file_path: Path to the .s19/.srec file.
        gap_threshold: Max gap between records before
                       starting a new segment.

    Returns:
        Datablock object with parsed segments.

    Raises:
        SrecParseError: If the file cannot be parsed.
    """

    if not os.path.exists(file_path):
        raise SrecParseError(
            f"File not found: {file_path}"
        )

    datablock = Datablock(file_path)
    crc32 = 0

    current_segment = None

    # Address byte count per record type
    addr_sizes = {
        "S0": 2, "S1": 2, "S2": 3, "S3": 4,
        "S5": 2, "S7": 4, "S8": 3, "S9": 2,
    }

    try:
        with open(file_path, "r") as f:

            for line_num, line in enumerate(f, 1):

                line = line.strip()

                if not line:
                    continue

                if len(line) < 4:
                    raise SrecParseError(
                        f"Line {line_num}: Record too short"
                    )

                record_type = line[0:2].upper()

                if record_type not in addr_sizes:
                    raise SrecParseError(
                        f"Line {line_num}: Unknown record "
                        f"type '{record_type}'"
                    )

                # Parse hex bytes
                hex_str = line[2:]

                try:
                    raw_bytes = bytes.fromhex(hex_str)
                except ValueError:
                    raise SrecParseError(
                        f"Line {line_num}: Invalid hex data"
                    )

                byte_count = raw_bytes[0]

                # Verify length
                if len(raw_bytes) != byte_count + 1:
                    raise SrecParseError(
                        f"Line {line_num}: Length mismatch "
                        f"(expected {byte_count + 1}, "
                        f"got {len(raw_bytes)})"
                    )

                # Verify checksum (1's complement)
                calc_sum = sum(raw_bytes[:-1]) & 0xFF
                checksum = (~calc_sum) & 0xFF

                if checksum != raw_bytes[-1]:
                    raise SrecParseError(
                        f"Line {line_num}: Checksum error"
                    )

                addr_size = addr_sizes[record_type]

                # Extract address
                address = 0
                for i in range(addr_size):
                    address = (
                        (address << 8) | raw_bytes[1 + i]
                    )

                # Extract data
                data_start = 1 + addr_size
                data_end = len(raw_bytes) - 1  # exclude checksum
                data = raw_bytes[data_start:data_end]

                # Process data records
                if record_type in ("S1", "S2", "S3"):

                    if len(data) == 0:
                        continue

                    # Update CRC32
                    crc32 = binascii.crc32(data, crc32)

                    if current_segment is None:
                        current_segment = Segment(
                            address,
                            bytearray(data)
                        )

                    elif address == current_segment.end_address:
                        # Contiguous
                        current_segment.data.extend(data)

                    elif (address - current_segment.end_address
                          <= gap_threshold):
                        # Small gap
                        gap_size = (
                            address -
                            current_segment.end_address
                        )
                        current_segment.data.extend(
                            b'\xFF' * gap_size
                        )
                        current_segment.data.extend(data)

                    else:
                        # Large gap
                        datablock.segments.append(
                            current_segment
                        )
                        current_segment = Segment(
                            address,
                            bytearray(data)
                        )

                elif record_type in ("S7", "S8", "S9"):
                    # End of file
                    break

        # Save last segment
        if current_segment is not None:
            datablock.segments.append(current_segment)

        # Store checksum
        datablock.checksum = crc32 & 0xFFFFFFFF

    except IOError as e:
        raise SrecParseError(
            f"Cannot read file: {e}"
        )

    return datablock
