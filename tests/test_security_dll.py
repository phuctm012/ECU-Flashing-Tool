# ==================================================
# Security DLL Loader Tests
# ==================================================
#
# Regression for a real report from a Windows machine running the
# PyInstaller .exe:
#
#     Connection failed: Failed to load security DLL: Failed to
#     load dynlib/dll 'C:/.../dist/SeedKey.dll'. Most likely this
#     dynlib/dll was not found when the application was frozen.
#
# That text is PyInstaller's ctypes hook re-raising the real
# OSError with its own fixed message — misleading here, because
# the Security DLL is an external file the operator picks at run
# time and is deliberately not bundled, and it was sitting right
# there on disk. The real cause was hidden one exception down.
#
# So these tests pin: the PE architecture detector (no pefile, no
# dumpbin — three header reads), the refusal to even try a DLL
# whose bitness can't work, and that the message naming the real
# cause survives PyInstaller-style exception wrapping.
#
# Fake PE files are built byte by byte: a real 32-bit Windows DLL
# can't be committed to the repo, and the detector only ever
# reads the header, so a 256-byte stub exercises exactly the code
# path a real DLL would.
# ==================================================

import os
import struct
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)

from communication import security_dll
from communication.security_dll import (
    PE_MACHINE_NAMES,
    SecurityDllError,
    bitness_mismatch,
    describe_pe_machine,
    host_bitness_name,
    load_security_dll,
    read_pe_machine,
)

MACHINE_I386 = 0x014C
MACHINE_AMD64 = 0x8664
MACHINE_ARM64 = 0xAA64


def _write_fake_pe(directory, name, machine, pe_offset=0x80):
    """
    A minimal PE: "MZ" at 0, e_lfanew at 0x3C, "PE\\0\\0" at that
    offset, then IMAGE_FILE_HEADER.Machine.
    """

    data = bytearray(b"\x00" * (pe_offset + 0x40))
    data[0:2] = b"MZ"
    struct.pack_into("<I", data, 0x3C, pe_offset)
    data[pe_offset:pe_offset + 4] = b"PE\x00\x00"
    struct.pack_into("<H", data, pe_offset + 4, machine)

    path = os.path.join(directory, name)
    with open(path, "wb") as f:
        f.write(bytes(data))
    return path


class TestReadPeMachine(unittest.TestCase):

    def test_reads_a_32_bit_header(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_fake_pe(tmp, "x86.dll", MACHINE_I386)
            self.assertEqual(read_pe_machine(path), MACHINE_I386)

    def test_reads_a_64_bit_header(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_fake_pe(tmp, "x64.dll", MACHINE_AMD64)
            self.assertEqual(read_pe_machine(path), MACHINE_AMD64)

    def test_follows_a_non_default_pe_offset(self):
        # e_lfanew varies between real linkers; the parser must
        # follow it rather than assume 0x80.
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_fake_pe(
                tmp, "far.dll", MACHINE_AMD64, pe_offset=0x108
            )
            self.assertEqual(read_pe_machine(path), MACHINE_AMD64)

    def test_a_non_pe_file_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "notpe.dll")
            with open(path, "wb") as f:
                f.write(b"this is not a PE image")
            self.assertIsNone(read_pe_machine(path))

    def test_a_truncated_header_returns_none_instead_of_raising(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "short.dll")
            with open(path, "wb") as f:
                f.write(b"MZ")
            self.assertIsNone(read_pe_machine(path))

    def test_a_missing_file_returns_none(self):
        self.assertIsNone(read_pe_machine("/nonexistent/x.dll"))

    def test_describe_names_known_machines(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_fake_pe(tmp, "x86.dll", MACHINE_I386)
            self.assertEqual(
                describe_pe_machine(path), PE_MACHINE_NAMES[MACHINE_I386]
            )

    def test_describe_reports_an_unknown_machine_as_hex(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_fake_pe(tmp, "weird.dll", 0x1234)
            self.assertIn("0x1234", describe_pe_machine(path))


class TestBitnessMismatch(unittest.TestCase):

    def test_detects_a_32_bit_dll_on_a_64_bit_host(self):
        # The actual cause in the field: Seed&Key DLLs ship beside
        # CANoe/CANape tooling and are very often 32-bit.
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_fake_pe(tmp, "SeedKey.dll", MACHINE_I386)
            with mock.patch.object(
                security_dll, "host_bitness_name",
                return_value="64-bit (x64)",
            ):
                message = bitness_mismatch(path)

        self.assertIsNotNone(message)
        self.assertIn("32-bit", message)
        self.assertIn("64-bit", message)

    def test_detects_a_64_bit_dll_on_a_32_bit_host(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_fake_pe(tmp, "SeedKey.dll", MACHINE_AMD64)
            with mock.patch.object(
                security_dll, "host_bitness_name",
                return_value="32-bit (x86)",
            ):
                message = bitness_mismatch(path)

        self.assertIsNotNone(message)

    def test_matching_bitness_is_silent(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_fake_pe(tmp, "SeedKey.dll", MACHINE_AMD64)
            with mock.patch.object(
                security_dll, "host_bitness_name",
                return_value="64-bit (x64)",
            ):
                self.assertIsNone(bitness_mismatch(path))

    def test_arm64_counts_as_64_bit(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_fake_pe(tmp, "arm.dll", MACHINE_ARM64)
            with mock.patch.object(
                security_dll, "host_bitness_name",
                return_value="64-bit (x64)",
            ):
                self.assertIsNone(bitness_mismatch(path))

    def test_a_non_pe_file_is_never_blocked(self):
        # A .so/.dylib on Linux/macOS, or anything unreadable:
        # this check must never stop a load it doesn't understand.
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "lib.so")
            with open(path, "wb") as f:
                f.write(b"\x7fELF not really")
            self.assertIsNone(bitness_mismatch(path))

    def test_host_bitness_name_is_one_of_the_two(self):
        self.assertIn(
            host_bitness_name(), ("32-bit (x86)", "64-bit (x64)")
        )


class TestLoadSecurityDll(unittest.TestCase):

    def test_missing_file_says_so_with_the_path(self):
        with self.assertRaises(SecurityDllError) as ctx:
            load_security_dll("/nonexistent/SeedKey.dll")

        message = str(ctx.exception)
        self.assertIn("not found", message)
        self.assertIn("SeedKey.dll", message)

    def test_missing_file_in_a_frozen_build_explains_it_is_external(self):
        # The exact confusion the field report showed: in a frozen
        # build the operator is told the DLL "was not found when
        # the application was frozen", implying a packaging bug.
        # It is an external file by design.
        with mock.patch.object(sys, "frozen", True, create=True):
            with self.assertRaises(SecurityDllError) as ctx:
                load_security_dll("/nonexistent/SeedKey.dll")

        self.assertIn("not bundled", str(ctx.exception))

    def test_empty_path_is_rejected(self):
        with self.assertRaises(SecurityDllError):
            load_security_dll("")

    def test_a_wrong_bitness_dll_is_refused_without_asking_the_os(self):
        # Refused up front so the message is the same whether or
        # not a WinError survives PyInstaller's re-raise.
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_fake_pe(tmp, "SeedKey.dll", MACHINE_I386)
            with mock.patch.object(
                security_dll, "host_bitness_name",
                return_value="64-bit (x64)",
            ):
                with mock.patch("ctypes.CDLL") as cdll:
                    with self.assertRaises(SecurityDllError) as ctx:
                        load_security_dll(path)

        cdll.assert_not_called()
        self.assertIn("32-bit", str(ctx.exception))

    def test_the_real_cause_survives_pyinstallers_wrapper(self):
        # PyInstaller raises its own error *from* the real OSError.
        # The message must name the real one, not the wrapper's
        # "not found when the application was frozen".
        real = OSError("[WinError 126] The specified module could "
                       "not be found")
        real.winerror = 126

        wrapper = OSError(
            "Failed to load dynlib/dll 'SeedKey.dll'. Most likely "
            "this dynlib/dll was not found when the application "
            "was frozen."
        )
        wrapper.__cause__ = real

        with tempfile.TemporaryDirectory() as tmp:
            path = _write_fake_pe(tmp, "SeedKey.dll", MACHINE_AMD64)
            with mock.patch.object(
                security_dll, "host_bitness_name",
                return_value="64-bit (x64)",
            ):
                with mock.patch("ctypes.CDLL", side_effect=wrapper):
                    with self.assertRaises(SecurityDllError) as ctx:
                        load_security_dll(path)

        message = str(ctx.exception)
        self.assertIn("WinError 126", message)
        self.assertIn("depends on", message)
        self.assertNotIn("when the application was frozen", message)

    def test_winerror_193_is_reported_as_an_architecture_problem(self):
        real = OSError("[WinError 193] %1 is not a valid Win32 "
                       "application")
        real.winerror = 193

        with tempfile.TemporaryDirectory() as tmp:
            # Non-PE on purpose: bitness_mismatch() stays silent,
            # so only the WinError can explain this one.
            path = os.path.join(tmp, "SeedKey.dll")
            with open(path, "wb") as f:
                f.write(b"not a PE at all")

            with mock.patch("ctypes.CDLL", side_effect=real):
                with self.assertRaises(SecurityDllError) as ctx:
                    load_security_dll(path)

        self.assertIn("architecture", str(ctx.exception))

    def test_any_other_failure_still_names_the_file_and_the_host(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_fake_pe(tmp, "SeedKey.dll", MACHINE_AMD64)
            with mock.patch.object(
                security_dll, "host_bitness_name",
                return_value="64-bit (x64)",
            ):
                with mock.patch(
                    "ctypes.CDLL",
                    side_effect=OSError("something odd"),
                ):
                    with self.assertRaises(SecurityDllError) as ctx:
                        load_security_dll(path)

        message = str(ctx.exception)
        self.assertIn("SeedKey.dll", message)
        self.assertIn("something odd", message)
        self.assertIn("does exist", message)

    def test_a_successful_load_returns_the_handle(self):
        handle = object()
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_fake_pe(tmp, "SeedKey.dll", MACHINE_AMD64)
            with mock.patch.object(
                security_dll, "host_bitness_name",
                return_value="64-bit (x64)",
            ):
                with mock.patch("ctypes.CDLL", return_value=handle):
                    self.assertIs(load_security_dll(path), handle)

    def test_the_dlls_own_folder_is_added_to_the_search_path(self):
        # ctypes.CDLL(<absolute path>) does not do this, so a
        # Seed&Key DLL linking against a sibling DLL fails with
        # WinError 126 even though the picked file exists.
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_fake_pe(tmp, "SeedKey.dll", MACHINE_AMD64)
            cookie = mock.MagicMock()

            with mock.patch.object(
                security_dll, "host_bitness_name",
                return_value="64-bit (x64)",
            ):
                with mock.patch.object(
                    os, "add_dll_directory",
                    return_value=cookie, create=True,
                ) as add_dir:
                    with mock.patch("ctypes.CDLL", return_value=object()):
                        load_security_dll(path)

        add_dir.assert_called_once()
        self.assertEqual(
            os.path.normpath(add_dir.call_args[0][0]),
            os.path.normpath(tmp),
        )
        # Released again — leaving search-path entries behind
        # would change how every later load resolves.
        cookie.close.assert_called_once()

    def test_a_failing_add_dll_directory_does_not_stop_the_load(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_fake_pe(tmp, "SeedKey.dll", MACHINE_AMD64)
            handle = object()

            with mock.patch.object(
                security_dll, "host_bitness_name",
                return_value="64-bit (x64)",
            ):
                with mock.patch.object(
                    os, "add_dll_directory",
                    side_effect=OSError("nope"), create=True,
                ):
                    with mock.patch("ctypes.CDLL", return_value=handle):
                        self.assertIs(load_security_dll(path), handle)


def _write_pe_with_tables(directory, name, machine=MACHINE_AMD64,
                          exports=(), imports=()):
    """
    A PE complete enough for the export/import walkers: one
    section, a real export directory with a name-pointer table,
    and an import descriptor array. Built by hand because the
    only alternative fixture would be a real (proprietary)
    vendor DLL in the repo.
    """

    SECTION_RVA = 0x1000
    RAW_BASE = 0x400
    blob = bytearray()          # section contents, RVA 0x1000

    def put(raw):
        offset = len(blob)
        blob.extend(raw)
        return SECTION_RVA + offset

    name_rvas = [put(n.encode() + b"\x00") for n in exports]
    import_name_rvas = [put(n.encode() + b"\x00") for n in imports]
    dll_name_rva = put(name.encode() + b"\x00")

    while len(blob) % 4:
        blob.append(0)

    names_array_rva = SECTION_RVA + len(blob)
    for rva in name_rvas:
        blob.extend(struct.pack("<I", rva))

    ordinals_rva = SECTION_RVA + len(blob)
    for i in range(len(exports)):
        blob.extend(struct.pack("<H", i))
    while len(blob) % 4:
        blob.append(0)

    functions_rva = SECTION_RVA + len(blob)
    for i in range(len(exports)):
        blob.extend(struct.pack("<I", SECTION_RVA))   # dummy code RVA
    export_dir_rva = SECTION_RVA + len(blob)
    blob.extend(struct.pack(
        "<IIHHIIIIIII",
        0, 0, 0, 0,
        dll_name_rva, 1,
        len(exports), len(exports),
        functions_rva, names_array_rva, ordinals_rva,
    ))

    import_dir_rva = SECTION_RVA + len(blob)
    for rva in import_name_rvas:
        blob.extend(struct.pack("<IIIII", 1, 0, 0, rva, 1))
    blob.extend(struct.pack("<IIIII", 0, 0, 0, 0, 0))   # terminator

    opt_size = 224                                     # PE32
    pe_offset = 0x80
    headers = bytearray(b"\x00" * RAW_BASE)
    headers[0:2] = b"MZ"
    struct.pack_into("<I", headers, 0x3C, pe_offset)
    headers[pe_offset:pe_offset + 4] = b"PE\x00\x00"

    coff = pe_offset + 4
    struct.pack_into("<H", headers, coff, machine)         # Machine
    struct.pack_into("<H", headers, coff + 2, 1)           # NumberOfSections
    struct.pack_into("<H", headers, coff + 16, opt_size)
    struct.pack_into("<H", headers, coff + 18, 0x2102)     # DLL

    opt = coff + 20
    struct.pack_into("<H", headers, opt, 0x10B)             # PE32
    struct.pack_into("<I", headers, opt + 92, 16)           # NumberOfRvaAndSizes
    data_dir = opt + 96
    struct.pack_into("<I", headers, data_dir, export_dir_rva)
    struct.pack_into("<I", headers, data_dir + 4, 40)
    struct.pack_into("<I", headers, data_dir + 8, import_dir_rva)

    section = opt + opt_size
    headers[section:section + 8] = b".text\x00\x00\x00"
    struct.pack_into("<I", headers, section + 8, len(blob))
    struct.pack_into("<I", headers, section + 12, SECTION_RVA)
    struct.pack_into("<I", headers, section + 16, len(blob))
    struct.pack_into("<I", headers, section + 20, RAW_BASE)

    path = os.path.join(directory, name)
    with open(path, "wb") as f:
        f.write(bytes(headers))
        f.write(bytes(blob))
    return path


class TestPeExportAndImportTables(unittest.TestCase):
    """
    Needed because the two questions an error message can't
    answer are "does it export the name SFlash looks for, spelled
    exactly that way" and "which dependent DLL is missing". A
    32-bit stdcall export built without a .def file is spelled
    _GenerateKeyEx@28 — loads fine, getattr() never finds it.
    """

    def test_reads_export_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_pe_with_tables(
                tmp, "SeedKey.dll",
                exports=("GenerateKeyEx", "GenerateKeyExOpt"),
            )
            self.assertEqual(
                security_dll.read_pe_exports(path),
                ["GenerateKeyEx", "GenerateKeyExOpt"],
            )

    def test_reads_a_decorated_stdcall_export_verbatim(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_pe_with_tables(
                tmp, "SeedKey.dll", exports=("_GenerateKeyEx@28",),
            )
            self.assertEqual(
                security_dll.read_pe_exports(path),
                ["_GenerateKeyEx@28"],
            )

    def test_reads_dependent_dll_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_pe_with_tables(
                tmp, "SeedKey.dll",
                exports=("GenerateKeyEx",),
                imports=("KERNEL32.dll", "MSVCP140.dll"),
            )
            self.assertEqual(
                security_dll.read_pe_imports(path),
                ["KERNEL32.dll", "MSVCP140.dll"],
            )

    def test_a_pe_without_tables_returns_empty_lists(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_fake_pe(tmp, "bare.dll", MACHINE_AMD64)
            self.assertEqual(security_dll.read_pe_exports(path), [])
            self.assertEqual(security_dll.read_pe_imports(path), [])

    def test_a_non_pe_file_returns_empty_lists(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "x.dll")
            with open(path, "wb") as f:
                f.write(b"not a PE")
            self.assertEqual(security_dll.read_pe_exports(path), [])
            self.assertEqual(security_dll.read_pe_imports(path), [])

    def test_a_missing_file_returns_empty_lists(self):
        self.assertEqual(security_dll.read_pe_exports("/nope.dll"), [])
        self.assertEqual(security_dll.read_pe_imports("/nope.dll"), [])


class TestSecurityDllCallingContract(unittest.TestCase):
    """
    Regression for the second half of the field report. A real
    Seed&Key DLL inspected for this project exports exactly one
    function, `GenerateKeyEx`, cdecl, reading its 7th argument at
    [ebp+0x20] — i.e. the Vector/ASAM byte-array contract. The
    loader used to treat that exact name as the 1-argument
    uint32 -> uint32 kind, so it would have called a 7-argument
    function with one argument: args 2..7, including the output
    key pointer, read as stack garbage. That is an access
    violation, not a wrong key.
    """

    def _client(self):
        from communication.uds_client import UdsClient
        from communication.virtual_can import VirtualCanInterface
        return UdsClient(VirtualCanInterface())

    def _fake_dll(self, *exports):
        # spec= matters: a bare MagicMock answers to every
        # attribute, so "does it export GenerateKeyExOpt" would
        # always be yes and auto-detection would never be tested.
        return mock.MagicMock(spec=list(exports))

    def _load(self, client, dll, **kwargs):
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_fake_pe(tmp, "SeedKey.dll", MACHINE_AMD64)
            with mock.patch.object(
                security_dll, "host_bitness_name",
                return_value="64-bit (x64)",
            ):
                with mock.patch("ctypes.CDLL", return_value=dll):
                    client.load_security_dll(path, **kwargs)

    def test_auto_picks_the_vector_contract_for_generatekeyex(self):
        client = self._client()
        dll = self._fake_dll("GenerateKeyEx")

        self._load(client, dll)

        self.assertTrue(client._security_dll_is_bytes)
        self.assertEqual(len(dll.GenerateKeyEx.argtypes), 7)

    def test_auto_picks_the_opt_contract_when_that_is_exported(self):
        client = self._client()
        dll = self._fake_dll("GenerateKeyEx", "GenerateKeyExOpt")

        self._load(client, dll)

        self.assertTrue(client._security_dll_is_bytes)
        # 8 args: the ODX variant adds const char* iOptions.
        self.assertEqual(len(dll.GenerateKeyExOpt.argtypes), 8)

    def test_uint32_contract_must_be_asked_for_explicitly(self):
        client = self._client()
        dll = self._fake_dll("GenerateKeyEx")

        self._load(client, dll, signature="uint32")

        self.assertFalse(client._security_dll_is_bytes)
        self.assertEqual(len(dll.GenerateKeyEx.argtypes), 1)

    def test_an_unknown_signature_is_rejected(self):
        from communication.uds_client import UdsError

        client = self._client()
        with self.assertRaises(UdsError) as ctx:
            client.load_security_dll("/any.dll", signature="bogus")
        self.assertIn("bogus", str(ctx.exception))

    def test_a_missing_export_is_named(self):
        from communication.uds_client import UdsError

        client = self._client()
        dll = self._fake_dll("SomethingElse")

        with self.assertRaises(UdsError) as ctx:
            self._load(client, dll)
        self.assertIn("GenerateKeyEx", str(ctx.exception))

    def test_the_vector_wrapper_returns_the_bytes_the_dll_wrote(self):
        import ctypes

        client = self._client()
        dll = self._fake_dll("GenerateKeyEx")

        def fake_generate(seed_arr, seed_len, level, variant,
                          key_arr, max_key, key_len_ref):
            for i in range(4):
                key_arr[i] = 0xA0 + i
            key_len_ref._obj.value = 4
            return 0

        dll.GenerateKeyEx.side_effect = fake_generate

        self._load(client, dll)
        key = client._security_dll_func(bytes([1, 2, 3, 4]), 1)

        self.assertEqual(key, bytes([0xA0, 0xA1, 0xA2, 0xA3]))

    def test_the_variant_string_reaches_the_dll(self):
        # iVariant is how a multi-ECU DLL picks its algorithm;
        # it used to be hardcoded empty.
        client = self._client()
        dll = self._fake_dll("GenerateKeyEx")
        seen = {}

        def fake_generate(seed_arr, seed_len, level, variant,
                          key_arr, max_key, key_len_ref):
            seen["variant"] = variant
            key_arr[0] = 0x11
            key_len_ref._obj.value = 1
            return 0

        dll.GenerateKeyEx.side_effect = fake_generate

        self._load(client, dll, variant="SUZ05")
        client._security_dll_func(bytes([1, 2, 3, 4]), 1)

        self.assertEqual(seen["variant"], b"SUZ05")

    def test_the_variant_defaults_to_empty(self):
        client = self._client()
        dll = self._fake_dll("GenerateKeyEx")
        seen = {}

        def fake_generate(seed_arr, seed_len, level, variant,
                          key_arr, max_key, key_len_ref):
            seen["variant"] = variant
            key_arr[0] = 0x11
            key_len_ref._obj.value = 1
            return 0

        dll.GenerateKeyEx.side_effect = fake_generate

        self._load(client, dll)
        client._security_dll_func(bytes([1, 2, 3, 4]), 1)

        self.assertEqual(seen["variant"], b"")

    def test_a_long_seed_is_passed_through_whole(self):
        # The field report that exposed all of this: the ECU
        # sends a 16-byte seed, and the old uint32 path refused
        # it with "expects a 4-byte seed". The Vector contract
        # takes any length.
        client = self._client()
        dll = self._fake_dll("GenerateKeyEx")
        seen = {}

        def fake_generate(seed_arr, seed_len, level, variant,
                          key_arr, max_key, key_len_ref):
            seen["len"] = seed_len
            seen["seed"] = bytes(seed_arr[:seed_len])
            for i in range(seed_len):
                key_arr[i] = seed_arr[i] ^ 0xFF
            key_len_ref._obj.value = seed_len
            return 0

        dll.GenerateKeyEx.side_effect = fake_generate

        seed = bytes(range(16))
        self._load(client, dll)
        key = client._security_dll_func(seed, 1)

        self.assertEqual(seen["len"], 16)
        self.assertEqual(seen["seed"], seed)
        self.assertEqual(key, bytes(b ^ 0xFF for b in seed))

    def test_a_nonzero_return_code_is_an_error(self):
        from communication.uds_client import UdsError

        client = self._client()
        dll = self._fake_dll("GenerateKeyEx")
        dll.GenerateKeyEx.side_effect = lambda *a: 7

        self._load(client, dll)
        with self.assertRaises(UdsError) as ctx:
            client._security_dll_func(bytes([1, 2, 3, 4]), 1)
        self.assertIn("error 7", str(ctx.exception))

    def test_an_impossible_key_length_is_an_error_not_garbage(self):
        # rc == 0 but nothing written is what calling the wrong
        # contract looks like when it doesn't crash outright —
        # better to say so than to send a 0-byte key to the ECU.
        from communication.uds_client import UdsError

        client = self._client()
        dll = self._fake_dll("GenerateKeyEx")
        dll.GenerateKeyEx.side_effect = lambda *a: 0

        self._load(client, dll)
        with self.assertRaises(UdsError) as ctx:
            client._security_dll_func(bytes([1, 2, 3, 4]), 1)
        self.assertIn("impossible key length", str(ctx.exception))


class TestCheckSecurityDllCommand(unittest.TestCase):
    """
    `cli.py check-security-dll <path>` — the read-only diagnosis
    an operator runs on the Windows machine to settle "is my DLL
    32-bit?" without starting a flash.
    """

    def _run(self, argv):
        import contextlib
        import io

        import cli

        out_buf = io.StringIO()
        err_buf = io.StringIO()
        with contextlib.redirect_stdout(out_buf), \
                contextlib.redirect_stderr(err_buf):
            code = cli.main(argv)
        return code, out_buf.getvalue() + err_buf.getvalue()

    def test_reports_a_wrong_bitness_dll_and_exits_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_fake_pe(tmp, "SeedKey.dll", MACHINE_I386)
            with mock.patch.object(
                security_dll, "host_bitness_name",
                return_value="64-bit (x64)",
            ):
                code, out = self._run(["check-security-dll", path])

        self.assertEqual(code, 1)
        self.assertIn("32-bit (x86)", out)
        self.assertIn("CANNOT LOAD", out)

    def test_reports_a_missing_file(self):
        code, out = self._run(
            ["check-security-dll", "/nonexistent/SeedKey.dll"]
        )
        self.assertEqual(code, 1)
        # Not matching the column padding — that is cosmetic and
        # has already drifted once.
        self.assertRegex(out, r"Exists:\s+False")
        self.assertIn("not found", out)

    def test_a_loadable_dll_with_the_new_entry_point(self):
        dll = mock.MagicMock()
        dll.GenerateKeyExOpt = object()

        with tempfile.TemporaryDirectory() as tmp:
            path = _write_fake_pe(tmp, "SeedKey.dll", MACHINE_AMD64)
            with mock.patch.object(
                security_dll, "host_bitness_name",
                return_value="64-bit (x64)",
            ):
                with mock.patch("ctypes.CDLL", return_value=dll):
                    code, out = self._run(
                        ["check-security-dll", path]
                    )

        self.assertEqual(code, 0)
        self.assertIn("LOADED OK", out)
        self.assertIn("GenerateKeyExOpt", out)

    def test_a_dll_that_loads_but_exports_nothing_useful_fails(self):
        # Loading is only half the question — SFlash still has to
        # find a key function in it.
        dll = mock.MagicMock(spec=[])

        with tempfile.TemporaryDirectory() as tmp:
            path = _write_fake_pe(tmp, "SeedKey.dll", MACHINE_AMD64)
            with mock.patch.object(
                security_dll, "host_bitness_name",
                return_value="64-bit (x64)",
            ):
                with mock.patch("ctypes.CDLL", return_value=dll):
                    code, out = self._run(
                        ["check-security-dll", path]
                    )

        self.assertEqual(code, 1)
        self.assertIn("neither", out)

    def test_never_touches_can(self):
        # A diagnosis command must be safe to run with the ECU
        # connected and powered.
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_fake_pe(tmp, "SeedKey.dll", MACHINE_I386)
            with mock.patch(
                "communication.virtual_can.VirtualCanInterface"
            ) as virtual, mock.patch(
                "communication.vector_can.VectorCanInterface"
            ) as vector:
                self._run(["check-security-dll", path])

        virtual.assert_not_called()
        vector.assert_not_called()


class TestUdsClientUsesTheDiagnosingLoader(unittest.TestCase):

    def test_load_security_dll_surfaces_the_diagnosis(self):
        from communication.uds_client import UdsClient, UdsError
        from communication.virtual_can import VirtualCanInterface

        client = UdsClient(VirtualCanInterface())

        with self.assertRaises(UdsError) as ctx:
            client.load_security_dll("/nonexistent/SeedKey.dll")

        self.assertIn("not found", str(ctx.exception))
        self.assertIn("SeedKey.dll", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
