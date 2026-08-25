# ==================================================
# UDS Client Tests
# ==================================================
#
# Uses VirtualCanInterface (no hardware needed) for the
# happy-path / ECU-interaction tests, and a small scripted
# fake CanInterface for deterministic NRC retry / pending
# tests that don't depend on the simulator's randomization.
# ==================================================

import os
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)

from communication.virtual_can import VirtualCanInterface
from communication.uds_client import (
    UdsClient,
    UdsError,
    UdsNegativeResponse,
    UdsTimeoutError,
)
from communication.ecu_simulator import EcuSimulator


def _make_client(tx_id=0x778, rx_id=0x788, functional_id=0x700):
    can = VirtualCanInterface(response_delay_ms=1)
    can.connect(tx_id=tx_id, rx_id=rx_id)
    trace = []
    client = UdsClient(
        can,
        trace_callback=lambda d, b: trace.append((d, bytes(b))),
        functional_id=functional_id,
        retry_delay=0.01,
    )
    return client, can, trace


class TestBasicUdsFlow(unittest.TestCase):

    def test_default_session(self):
        client, can, _ = _make_client()
        resp = client.diagnostic_session_control(0x01)
        self.assertEqual(resp[0], 0x50)

    def test_extended_session(self):
        client, can, _ = _make_client()
        resp = client.diagnostic_session_control(0x03)
        self.assertEqual(resp[0], 0x50)
        self.assertEqual(resp[1], 0x03)

    def test_security_access_dummy_algorithm(self):
        client, can, _ = _make_client()
        client.diagnostic_session_control(0x03)  # extended
        client.diagnostic_session_control(0x02)  # programming
        resp = client.security_access(
            level=1, key_function=EcuSimulator.compute_key
        )
        self.assertEqual(resp[0], 0x67)

    def test_read_data_by_identifier(self):
        client, can, _ = _make_client()
        data = client.read_data_by_identifier(0xF189)
        self.assertGreater(len(data), 0)

    def test_write_data_by_identifier_requires_security(self):
        client, can, _ = _make_client()
        # No security access performed -> must be denied
        with self.assertRaises(UdsNegativeResponse):
            client.write_data_by_identifier(0xF198, b'\x01\x02')

    def test_ecu_reset(self):
        client, can, _ = _make_client()
        resp = client.ecu_reset(0x01)
        self.assertEqual(resp[0], 0x51)

    def test_tester_present_suppressed_returns_none(self):
        client, can, _ = _make_client()
        result = client.tester_present(suppress_response=True)
        self.assertIsNone(result)


class TestFunctionalAddressing(unittest.TestCase):
    """
    Suzuki SLP1 sequence sends some requests to a functional
    (broadcast) address (0x700) instead of the ECU's
    physical request ID — see docs/*_Report_Trace.csv.
    """

    def test_functional_session_control_traced_as_func(self):
        client, can, trace = _make_client()
        client.diagnostic_session_control(0x03, functional=True)
        tx_events = [t for t in trace if t[0] == "TX(FUNC)"]
        self.assertEqual(len(tx_events), 1)
        self.assertEqual(tx_events[0][1], bytes([0x10, 0x03]))

    def test_physical_session_control_traced_as_tx(self):
        client, can, trace = _make_client()
        client.diagnostic_session_control(0x03, functional=False)
        tx_events = [t for t in trace if t[0] == "TX"]
        self.assertEqual(len(tx_events), 1)

    def test_control_dtc_setting_with_option_record(self):
        client, can, trace = _make_client()
        resp = client.control_dtc_setting(
            setting_type=0x02,
            option_record=bytes([0x00]),
            functional=True,
        )
        self.assertEqual(resp[0], 0xC5)
        tx_events = [t for t in trace if t[0] == "TX(FUNC)"]
        self.assertEqual(tx_events[0][1], bytes([0x85, 0x02, 0x00]))


class TestRequestDownloadByteOrder(unittest.TestCase):
    """
    Regression test for a real bug: comparing this tool's
    UDS trace against a real ECU capture
    (docs/*_Report_Trace.csv) showed RequestDownload (0x34)
    was sent with dataFormatIdentifier and
    addressAndLengthFormatIdentifier SWAPPED relative to
    ISO 14229-1. Both uds_client.py (encode) and
    ecu_simulator.py (decode) were fixed together, so the
    Virtual ECU never caught the mismatch on its own —
    this test pins the correct wire format down explicitly.
    """

    def _unlock_and_erase(self, client):
        client.diagnostic_session_control(0x03)  # extended
        client.diagnostic_session_control(0x02)  # programming
        client.security_access(
            level=1, key_function=EcuSimulator.compute_key
        )
        client.routine_control(
            sub_function=0x01, routine_id=0xFF00
        )  # erase

    def test_byte_order_matches_iso14229_default_lengths(self):
        client, can, trace = _make_client()
        self._unlock_and_erase(client)

        client.request_download(
            memory_address=0x1000, memory_size=64
        )

        tx_frames = [d for direction, d in trace if direction == "TX"]
        req_download = next(d for d in tx_frames if d[0] == 0x34)

        # SID, dataFormatIdentifier(0x00), addrLenFormat(0x44)
        self.assertEqual(req_download[1], 0x00)
        self.assertEqual(req_download[2], 0x44)

    def test_byte_order_matches_iso14229_suzuki_5byte_address(self):
        client, can, trace = _make_client()
        self._unlock_and_erase(client)

        client.request_download(
            memory_address=0x001AA000,
            memory_size=10000,
            addr_length=5,
            size_length=4,
        )

        tx_frames = [d for direction, d in trace if direction == "TX"]
        req_download = next(d for d in tx_frames if d[0] == 0x34)

        # SID, dataFormatIdentifier(0x00), addrLenFormat(0x45)
        self.assertEqual(req_download[1], 0x00)
        self.assertEqual(req_download[2], 0x45)

        # 5-byte address, 4-byte size
        address = int.from_bytes(req_download[3:8], "big")
        size = int.from_bytes(req_download[8:12], "big")
        self.assertEqual(address, 0x001AA000)
        self.assertEqual(size, 10000)

    def test_download_firmware_end_to_end(self):
        client, can, _ = _make_client()
        self._unlock_and_erase(client)

        received = {"bytes": 0}

        def progress(sent, total):
            received["bytes"] = sent

        client.download_firmware(
            memory_address=0x1000,
            data=bytes([0xAB]) * 500,
            progress_callback=progress,
        )

        self.assertEqual(received["bytes"], 500)

    def test_download_firmware_passes_compression_and_encrypting(self):
        # download_firmware() must actually thread compression/
        # encrypting through to request_download() — it used to
        # accept no such parameters at all, silently always
        # sending dataFormatIdentifier=0x00 regardless of what
        # the caller wanted.
        client, can, trace = _make_client()
        self._unlock_and_erase(client)

        client.download_firmware(
            memory_address=0x1000,
            data=bytes([0xAB]) * 10,
            compression=0x3,
            encrypting=0x5,
        )

        tx_frames = [d for direction, d in trace if direction == "TX"]
        req_download = next(d for d in tx_frames if d[0] == 0x34)

        # dataFormatIdentifier = compressionMethod<<4 | encryptingMethod
        self.assertEqual(req_download[1], 0x35)


# --------------------------------------------------
# Deterministic NRC retry / ResponsePending tests via a
# scripted fake CAN interface (not dependent on the
# simulator's randomized error injection).
# --------------------------------------------------

class _ScriptedCanInterface:

    def __init__(self, responses):
        self._responses = list(responses)
        self.sent = []

    def send_isotp(self, data, target_id=None):
        self.sent.append(bytes(data))

    def receive_isotp(self, timeout=2.0):
        if not self._responses:
            return None
        return self._responses.pop(0)


class TestNrcRetryLogic(unittest.TestCase):

    def test_retries_on_busy_repeat_request(self):
        responses = [
            bytes([0x7F, 0x22, 0x21]),          # NRC 0x21: busy, retryable
            bytes([0x62, 0xF1, 0x89, 0x01]),    # then positive
        ]
        can = _ScriptedCanInterface(responses)
        client = UdsClient(can, retry_delay=0.01)

        data = client.read_data_by_identifier(0xF189)

        self.assertEqual(data, bytes([0x01]))
        self.assertEqual(len(can.sent), 2)  # original + 1 retry

    def test_non_retryable_nrc_raises_immediately(self):
        responses = [bytes([0x7F, 0x22, 0x31])]  # requestOutOfRange
        can = _ScriptedCanInterface(responses)
        client = UdsClient(can, retry_delay=0.01)

        with self.assertRaises(UdsNegativeResponse):
            client.read_data_by_identifier(0xF189)

        self.assertEqual(len(can.sent), 1)  # no retry

    def test_gives_up_after_max_retries(self):
        responses = [bytes([0x7F, 0x22, 0x21])] * 10  # always busy
        can = _ScriptedCanInterface(responses)
        client = UdsClient(can, retry_delay=0.01, max_retries=2)

        with self.assertRaises(UdsNegativeResponse):
            client.read_data_by_identifier(0xF189)

        self.assertEqual(len(can.sent), 3)  # original + 2 retries

    def test_response_pending_keeps_waiting_for_final_response(self):
        responses = [
            bytes([0x7F, 0x31, 0x78]),               # ResponsePending
            bytes([0x7F, 0x31, 0x78]),               # still pending
            bytes([0x71, 0x01, 0xFF, 0x00, 0x00]),   # final positive
        ]
        can = _ScriptedCanInterface(responses)
        client = UdsClient(can, p2_star_timeout=1.0)

        resp = client.routine_control(
            sub_function=0x01, routine_id=0xFF00
        )

        self.assertEqual(resp[0], 0x71)
        self.assertEqual(len(can.sent), 1)  # single request, no retry needed

    def test_no_response_raises_timeout(self):
        can = _ScriptedCanInterface([])
        client = UdsClient(can, p2_timeout=0.05)

        with self.assertRaises(UdsTimeoutError):
            client.read_data_by_identifier(0xF189)


def _compile_shared_lib(c_source, out_dir):
    """
    Compiles a tiny C source into a real, loadable shared
    library (.dylib/.so) using the system compiler, for tests
    that need to exercise ctypes' actual C calling convention
    against a real DLL export — mocking _security_dll_func
    directly (as TestVariableLengthSeed does) can't catch a bug
    in load_security_dll()'s own signature-detection logic,
    only a real ctypes.CDLL() call across a real ABI boundary
    can. Returns the compiled library path, or None if no C
    compiler is available (test should skip in that case).
    """

    cc = shutil.which("cc") or shutil.which("gcc")
    if cc is None:
        return None

    ext = ".dylib" if sys.platform == "darwin" else ".so"
    src_path = os.path.join(out_dir, "fixture.c")
    lib_path = os.path.join(out_dir, "fixture" + ext)

    with open(src_path, "w") as f:
        f.write(c_source)

    flag = "-dynamiclib" if sys.platform == "darwin" else "-shared"
    result = subprocess.run(
        [cc, flag, "-fPIC", "-o", lib_path, src_path],
        capture_output=True,
    )
    if result.returncode != 0 or not os.path.exists(lib_path):
        return None

    return lib_path


_LEGACY_DLL_SOURCE = """
#include <stdint.h>
uint32_t GenerateKeyEx(uint32_t seed) {
    return seed ^ 0xDEADBEEFu;
}
"""

_BYTE_BUFFER_DLL_SOURCE = """
#include <stdint.h>
int GenerateKeyExOpt(
    const uint8_t *seed, uint32_t seed_len,
    uint32_t level, const char *variant,
    uint8_t *key, uint32_t max_key_len,
    uint32_t *out_key_len)
{
    uint32_t i;
    for (i = 0; i < seed_len && i < max_key_len; i++) {
        key[i] = seed[i] ^ 0xAA;
    }
    *out_key_len = i;
    return 0;
}
"""


class TestSecurityDllLoader(unittest.TestCase):

    def test_missing_dll_raises_uds_error(self):
        can = _ScriptedCanInterface([])
        client = UdsClient(can)

        with self.assertRaises(UdsError):
            client.load_security_dll("/nonexistent/path/to.dll")

    def test_legacy_generate_key_ex_export_not_treated_as_buffer(self):
        # Regression guard: a real DLL built to this project's
        # previously documented contract (plain
        # "UINT32 GenerateKeyEx(UINT32 seed)") exports a
        # function literally named GenerateKeyEx. Only the
        # distinct name "GenerateKeyExOpt" may opt into the new
        # byte-buffer calling convention — treating a
        # legacy-named export as byte-buffer calls it with the
        # wrong number/type of arguments (mismatched C calling
        # convention), which crashes the process, not just
        # returns a wrong answer.
        with tempfile.TemporaryDirectory() as tmp:
            lib_path = _compile_shared_lib(
                _LEGACY_DLL_SOURCE, tmp
            )
            if lib_path is None:
                self.skipTest("no C compiler available")

            can = _ScriptedCanInterface([])
            client = UdsClient(can)
            client.load_security_dll(lib_path)

            self.assertFalse(client._security_dll_is_bytes)

            seed = 0x12345678
            seed_bytes = struct.pack(">I", seed)
            key_bytes = client._compute_security_key(
                seed_bytes, level=1, key_function=None
            )
            expected = struct.pack(
                ">I", seed ^ 0xDEADBEEF
            )
            self.assertEqual(key_bytes, expected)

    def test_generate_key_ex_opt_export_uses_buffer_signature(self):
        with tempfile.TemporaryDirectory() as tmp:
            lib_path = _compile_shared_lib(
                _BYTE_BUFFER_DLL_SOURCE, tmp
            )
            if lib_path is None:
                self.skipTest("no C compiler available")

            can = _ScriptedCanInterface([])
            client = UdsClient(can)
            client.load_security_dll(lib_path)

            self.assertTrue(client._security_dll_is_bytes)

            seed_bytes = bytes(range(0x10, 0x18))  # 8 bytes
            key_bytes = client._compute_security_key(
                seed_bytes, level=1, key_function=None
            )
            expected = bytes(b ^ 0xAA for b in seed_bytes)
            self.assertEqual(key_bytes, expected)


class TestVariableLengthSeed(unittest.TestCase):

    def test_16_byte_seed_with_bytes_key_function(self):
        seed_16 = bytes(range(0x10, 0x20))
        fake_key = bytes(range(0x20, 0x30))
        responses = [
            bytes([0x67, 0x01]) + seed_16,
            bytes([0x67, 0x02]),
        ]
        can = _ScriptedCanInterface(responses)
        client = UdsClient(can, retry_delay=0.01)
        client._security_dll_func = (
            lambda sb, lvl: fake_key
        )
        client._security_dll_is_bytes = True

        resp = client.security_access(level=1)

        self.assertEqual(resp[0], 0x67)
        send_key_msg = can.sent[1]
        self.assertEqual(
            send_key_msg,
            bytes([0x27, 0x02]) + fake_key
        )

    def test_16_byte_seed_dummy_sends_16_byte_key(self):
        seed_16 = bytes(range(0x10, 0x20))
        responses = [
            bytes([0x67, 0x01]) + seed_16,
            bytes([0x67, 0x02]),
        ]
        can = _ScriptedCanInterface(responses)
        client = UdsClient(can, retry_delay=0.01)

        resp = client.security_access(level=1)

        self.assertEqual(resp[0], 0x67)
        send_key_msg = can.sent[1]
        self.assertEqual(send_key_msg[0], 0x27)
        self.assertEqual(send_key_msg[1], 0x02)
        self.assertEqual(len(send_key_msg), 2 + 16)

    def test_4_byte_seed_dummy_still_works(self):
        import struct
        seed_int = 0x12345678
        seed_bytes = struct.pack(">I", seed_int)
        expected_key = EcuSimulator.compute_key(
            seed_int
        )
        expected_key_bytes = struct.pack(
            ">I", expected_key
        )
        responses = [
            bytes([0x67, 0x01]) + seed_bytes,
            bytes([0x67, 0x02]),
        ]
        can = _ScriptedCanInterface(responses)
        client = UdsClient(can, retry_delay=0.01)

        resp = client.security_access(level=1)

        self.assertEqual(resp[0], 0x67)
        send_key_msg = can.sent[1]
        self.assertEqual(
            send_key_msg,
            bytes([0x27, 0x02]) + expected_key_bytes
        )

    def test_already_unlocked_variable_seed(self):
        responses = [
            bytes([0x67, 0x01, 0, 0, 0, 0, 0, 0]),
        ]
        can = _ScriptedCanInterface(responses)
        client = UdsClient(can, retry_delay=0.01)

        resp = client.security_access(level=1)

        self.assertEqual(resp[0], 0x67)
        self.assertEqual(len(can.sent), 1)


if __name__ == "__main__":
    unittest.main()
