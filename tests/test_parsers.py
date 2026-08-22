# ==================================================
# Parser Tests (HEX, S-Record, Binary)
# ==================================================

import os
import sys
import tempfile
import unittest

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)

from parsers.hex_parser import parse_hex_file, HexParseError
from parsers.srec_parser import parse_srec_file, SrecParseError
from parsers.binary_parser import parse_binary_file, BinaryParseError

SAMPLE_HEX = os.path.join(os.path.dirname(__file__), "sample.hex")


def _srec_line(record_type, addr, addr_bytes, data):
    """Build one well-formed S-Record line (correct checksum)."""

    payload = addr.to_bytes(addr_bytes, "big") + data
    byte_count = len(payload) + 1
    raw = bytes([byte_count]) + payload
    checksum = (~sum(raw)) & 0xFF
    return f"S{record_type}{raw.hex().upper()}{checksum:02X}"


def _write_temp(suffix, lines):
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=suffix, delete=False
    ) as f:
        f.write("\n".join(lines) + "\n")
        return f.name


class TestHexParser(unittest.TestCase):

    def test_parses_sample_hex(self):
        db = parse_hex_file(SAMPLE_HEX)
        self.assertGreater(db.segment_count, 0)
        self.assertGreater(db.total_size, 0)

    def test_missing_file_raises(self):
        with self.assertRaises(HexParseError):
            parse_hex_file("/nonexistent/path.hex")


class TestSrecParser(unittest.TestCase):
    """
    Covers the .s3 (32-bit address S-Record) gap found and
    fixed after a user asked about flashing a real ECU with
    a .s3 file — the parser itself already handled S3
    records generically, but the extension wasn't routed to
    it (see gui/configure_tab.py's _parse_firmware_file).
    """

    def test_s1_16bit_address(self):
        lines = [
            _srec_line(0, 0, 2, b"HDR"),
            _srec_line(1, 0x1000, 2, bytes(range(8))),
            _srec_line(9, 0x1000, 2, b""),
        ]
        path = _write_temp(".s19", lines)
        try:
            db = parse_srec_file(path)
            self.assertEqual(db.segments[0].start_address, 0x1000)
            self.assertEqual(db.total_size, 8)
        finally:
            os.unlink(path)

    def test_s3_32bit_address(self):
        lines = [
            _srec_line(0, 0, 2, b"HDR"),
            _srec_line(3, 0x00100000, 4, bytes(range(16))),
            _srec_line(3, 0x00100010, 4, bytes(range(16, 32))),
            _srec_line(7, 0x00100000, 4, b""),
        ]
        path = _write_temp(".s3", lines)
        try:
            db = parse_srec_file(path)
            self.assertEqual(db.segment_count, 1)
            self.assertEqual(db.total_size, 32)
            self.assertEqual(
                db.segments[0].start_address, 0x00100000
            )
        finally:
            os.unlink(path)

    def test_gap_below_threshold_is_padded(self):
        lines = [
            _srec_line(3, 0x1000, 4, bytes([0x11] * 4)),
            _srec_line(3, 0x1010, 4, bytes([0x22] * 4)),  # 12-byte gap
            _srec_line(7, 0x1000, 4, b""),
        ]
        path = _write_temp(".s3", lines)
        try:
            db = parse_srec_file(path, gap_threshold=256)
            self.assertEqual(db.segment_count, 1)
            self.assertEqual(db.total_size, 4 + 12 + 4)
        finally:
            os.unlink(path)

    def test_gap_above_threshold_starts_new_segment(self):
        lines = [
            _srec_line(3, 0x1000, 4, bytes([0x11] * 4)),
            _srec_line(3, 0x2000, 4, bytes([0x22] * 4)),  # huge gap
            _srec_line(7, 0x1000, 4, b""),
        ]
        path = _write_temp(".s3", lines)
        try:
            db = parse_srec_file(path, gap_threshold=256)
            self.assertEqual(db.segment_count, 2)
        finally:
            os.unlink(path)

    def test_bad_checksum_raises(self):
        valid = _srec_line(1, 0x1000, 2, bytes([0x11, 0x22]))
        bad_byte = f"{(int(valid[-2:], 16) ^ 0xFF):02X}"
        corrupted = valid[:-2] + bad_byte

        path = _write_temp(".s19", [corrupted])
        try:
            with self.assertRaises(SrecParseError):
                parse_srec_file(path)
        finally:
            os.unlink(path)

    def test_unknown_record_type_raises(self):
        path = _write_temp(".s19", ["SX1300001122AA"])
        try:
            with self.assertRaises(SrecParseError):
                parse_srec_file(path)
        finally:
            os.unlink(path)

    def test_missing_file_raises(self):
        with self.assertRaises(SrecParseError):
            parse_srec_file("/nonexistent/path.s3")


class TestBinaryParser(unittest.TestCase):

    def test_parses_binary_with_base_address(self):
        with tempfile.NamedTemporaryFile(
            suffix=".bin", delete=False
        ) as f:
            f.write(bytes([0xAA] * 100))
            path = f.name

        try:
            db = parse_binary_file(path, start_address=0x8000)
            self.assertEqual(db.total_size, 100)
            self.assertEqual(
                db.segments[0].start_address, 0x8000
            )
        finally:
            os.unlink(path)

    def test_empty_file_raises(self):
        with tempfile.NamedTemporaryFile(
            suffix=".bin", delete=False
        ) as f:
            path = f.name

        try:
            with self.assertRaises(BinaryParseError):
                parse_binary_file(path)
        finally:
            os.unlink(path)

    def test_missing_file_raises(self):
        with self.assertRaises(BinaryParseError):
            parse_binary_file("/nonexistent/path.bin")


if __name__ == "__main__":
    unittest.main()
