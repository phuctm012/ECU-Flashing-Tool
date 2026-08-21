# ==================================================
# Intel HEX Parser
# ==================================================
#
# Parses Intel HEX files (.hex) into segments
# containing start address and binary data.
#
# Reference: https://en.wikipedia.org/wiki/Intel_HEX
# ==================================================

import os
import struct
import binascii


class Segment:
    """Represents a contiguous block of data at a specific address."""

    def __init__(self, start_address, data=None):

        self.start_address = start_address
        self.data = data or bytearray()

    @property
    def end_address(self):
        return self.start_address + len(self.data)

    @property
    def length(self):
        return len(self.data)

    def __repr__(self):
        return (
            f"Segment(0x{self.start_address:08X}, "
            f"{self.length} bytes)"
        )


class Datablock:
    """
    Represents a firmware file with its parsed segments.
    """

    def __init__(self, file_path):

        self.file_path = file_path
        self.file_name = os.path.basename(file_path)
        self.segments = []
        self.checksum = 0
        self.file_type = "DATA"

    @property
    def total_size(self):
        return sum(seg.length for seg in self.segments)

    @property
    def segment_count(self):
        return len(self.segments)

    def __repr__(self):
        return (
            f"Datablock({self.file_name}, "
            f"{self.segment_count} segments, "
            f"{self.total_size} bytes)"
        )


class HexParseError(Exception):
    """Raised when a HEX file cannot be parsed."""
    pass


def parse_hex_file(file_path, gap_threshold=256):
    """
    Parse an Intel HEX file into a Datablock.

    Intel HEX record format:
        :LLAAAATT[DD...]CC
        LL = byte count
        AAAA = address (16-bit)
        TT = record type
        DD = data bytes
        CC = checksum

    Record types:
        00 = Data
        01 = End Of File
        02 = Extended Segment Address
        03 = Start Segment Address
        04 = Extended Linear Address
        05 = Start Linear Address

    Args:
        file_path: Path to the .hex file.
        gap_threshold: Max gap (bytes) between records before
                       starting a new segment.

    Returns:
        Datablock object with parsed segments.

    Raises:
        HexParseError: If the file cannot be parsed.
    """

    if not os.path.exists(file_path):
        raise HexParseError(
            f"File not found: {file_path}"
        )

    datablock = Datablock(file_path)
    crc32 = 0

    # State
    extended_address = 0  # Upper 16 bits from type 04
    segment_address = 0   # From type 02
    current_segment = None

    try:
        with open(file_path, "r") as f:

            for line_num, line in enumerate(f, 1):

                line = line.strip()

                if not line:
                    continue

                if not line.startswith(":"):
                    raise HexParseError(
                        f"Line {line_num}: Missing ':' prefix"
                    )

                # Remove ':'
                hex_str = line[1:]

                if len(hex_str) < 10:
                    raise HexParseError(
                        f"Line {line_num}: Record too short"
                    )

                # Parse fields
                try:
                    raw_bytes = bytes.fromhex(hex_str)
                except ValueError:
                    raise HexParseError(
                        f"Line {line_num}: Invalid hex data"
                    )

                byte_count = raw_bytes[0]
                address = (raw_bytes[1] << 8) | raw_bytes[2]
                record_type = raw_bytes[3]
                data = raw_bytes[4:4 + byte_count]
                checksum_byte = raw_bytes[4 + byte_count]

                # Verify checksum
                calc_sum = sum(raw_bytes[:-1]) & 0xFF
                calc_checksum = (~calc_sum + 1) & 0xFF

                if calc_checksum != checksum_byte:
                    raise HexParseError(
                        f"Line {line_num}: Checksum error "
                        f"(expected 0x{calc_checksum:02X}, "
                        f"got 0x{checksum_byte:02X})"
                    )

                # Process record
                if record_type == 0x00:
                    # Data record
                    full_address = (
                        extended_address + 
                        segment_address + 
                        address
                    )

                    # Update CRC32
                    crc32 = binascii.crc32(data, crc32)

                    if current_segment is None:
                        # Start new segment
                        current_segment = Segment(
                            full_address,
                            bytearray(data)
                        )

                    elif (full_address ==
                          current_segment.end_address):
                        # Contiguous — extend current segment
                        current_segment.data.extend(data)

                    elif (full_address - 
                          current_segment.end_address <=
                          gap_threshold):
                        # Small gap — fill with 0xFF and extend
                        gap_size = (
                            full_address -
                            current_segment.end_address
                        )
                        current_segment.data.extend(
                            b'\xFF' * gap_size
                        )
                        current_segment.data.extend(data)

                    else:
                        # Large gap — save current, start new
                        datablock.segments.append(
                            current_segment
                        )
                        current_segment = Segment(
                            full_address,
                            bytearray(data)
                        )

                elif record_type == 0x01:
                    # End Of File
                    break

                elif record_type == 0x02:
                    # Extended Segment Address
                    segment_address = (
                        ((data[0] << 8) | data[1]) << 4
                    )

                elif record_type == 0x03:
                    # Start Segment Address (ignored)
                    pass

                elif record_type == 0x04:
                    # Extended Linear Address
                    extended_address = (
                        ((data[0] << 8) | data[1]) << 16
                    )

                elif record_type == 0x05:
                    # Start Linear Address (ignored)
                    pass

                else:
                    raise HexParseError(
                        f"Line {line_num}: Unknown record "
                        f"type 0x{record_type:02X}"
                    )

        # Save last segment
        if current_segment is not None:
            datablock.segments.append(current_segment)

        # Store checksum
        datablock.checksum = crc32 & 0xFFFFFFFF

    except IOError as e:
        raise HexParseError(
            f"Cannot read file: {e}"
        )

    return datablock
