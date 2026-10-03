# ==================================================
# Security Access DLL Loader
# ==================================================
#
# Loads the external Seed&Key DLL for SecurityAccess (0x27) and —
# the whole reason this is its own module — says *why* when the
# load fails.
#
# Hit for real on a Windows machine running the PyInstaller .exe:
#
#     Connection failed: Failed to load security DLL: Failed to
#     load dynlib/dll 'C:/.../dist/SeedKey.dll'. Most likely this
#     dynlib/dll was not found when the application was frozen.
#
# That sentence is PyInstaller's, not ours. Its ctypes hook
# (pyimod04_ctypes) replaces ctypes.CDLL in a frozen build: it
# first looks for a same-named file inside the bundle
# (sys._MEIPASS), and when the real load then fails for ANY
# reason it re-raises as PyInstallerImportError with that fixed
# "not found when the application was frozen" text. For a DLL the
# operator picks at runtime that message is actively misleading —
# the DLL is an external file, deliberately not bundled, and it
# usually *is* right there on disk. The real cause is hidden one
# exception down.
#
# The three causes seen in practice, in order of likelihood:
#
#  1. **Bitness mismatch.** Seed&Key DLLs are very often built
#     32-bit (they ship alongside CANoe/CANape tooling), while
#     SFlash runs on 64-bit Python. Windows then fails the load
#     with "%1 is not a valid Win32 application" (WinError 193).
#     This module reads the DLL's PE header and says so outright,
#     because no amount of re-picking the file will fix it.
#  2. **A missing dependent DLL.** ctypes.CDLL(<absolute path>)
#     does NOT put the DLL's own folder on the search path, so a
#     Seed&Key DLL that links against a sibling DLL (or a VC++
#     runtime) fails with WinError 126 even though the file the
#     operator picked exists. Hence the os.add_dll_directory()
#     below.
#  3. The file genuinely isn't there (moved, or a path typed by
#     hand).
#
# Kept out of uds_client.py so the GUI/CLI can run the same
# diagnosis *before* a flash starts (ConfigureTabMixin's
# security_access_start_error()) without constructing a
# UdsClient, and so the PE parsing is unit-testable without a
# real DLL.
# ==================================================

import os
import struct
import sys

# IMAGE_FILE_HEADER.Machine values (winnt.h). Only the ones that
# can plausibly turn up for a Windows Seed&Key DLL.
PE_MACHINE_NAMES = {
    0x014C: "32-bit (x86)",
    0x8664: "64-bit (x64)",
    0xAA64: "ARM64",
    0x0200: "Itanium",
}


class SecurityDllError(Exception):
    """Raised with an actionable message when a DLL won't load."""
    pass


# ==================================================
# Bitness
# ==================================================

def host_bitness_name():
    """"64-bit (x64)" / "32-bit (x86)" for the running Python."""

    bits = struct.calcsize("P") * 8
    return "64-bit (x64)" if bits == 64 else "32-bit (x86)"


def read_pe_machine(path):
    """
    Returns the DLL's PE machine code (e.g. 0x8664), or None when
    the file isn't a PE image at all or can't be read.

    Deliberately hand-parsed rather than shelling out to dumpbin
    or importing pefile: three reads of the header is enough, and
    it must work in a frozen .exe with no extra dependency.
    Layout: "MZ" at 0, e_lfanew (uint32 LE) at 0x3C, "PE\\0\\0" at
    e_lfanew, Machine (uint16 LE) right after it.
    """

    try:
        with open(path, "rb") as f:
            if f.read(2) != b"MZ":
                return None

            f.seek(0x3C)
            raw = f.read(4)
            if len(raw) != 4:
                return None
            pe_offset = struct.unpack("<I", raw)[0]

            f.seek(pe_offset)
            if f.read(4) != b"PE\x00\x00":
                return None

            raw = f.read(2)
            if len(raw) != 2:
                return None
            return struct.unpack("<H", raw)[0]

    except OSError:
        return None


def describe_pe_machine(path):
    """Human-readable architecture of a DLL, or None if unknown."""

    machine = read_pe_machine(path)
    if machine is None:
        return None
    return PE_MACHINE_NAMES.get(machine, f"unknown (0x{machine:04X})")


def bitness_mismatch(path):
    """
    Returns an explanatory string when `path` is a PE image whose
    architecture can't be loaded by this process, else None.

    Used both by the loader (to explain a failure) and as a
    pre-flight check before a flash starts, so a mismatch is
    caught before any CAN traffic. Returns None for anything it
    can't read (a non-PE file, a .so/.dylib on Linux/macOS) —
    this must never block a load it doesn't understand.
    """

    machine = read_pe_machine(path)
    if machine is None:
        return None

    host = host_bitness_name()
    dll = PE_MACHINE_NAMES.get(machine, f"unknown (0x{machine:04X})")

    is_64_dll = machine in (0x8664, 0xAA64, 0x0200)
    is_64_host = host.startswith("64")

    if is_64_dll == is_64_host:
        return None

    # Deliberately does NOT suggest "run a 32-bit SFlash": Qt 6
    # ships no 32-bit Windows build, so PySide6 cannot be
    # installed on a 32-bit Python at all. Asking the DLL's
    # supplier for an x64 build is the only in-process fix.
    return (
        f"the DLL is {dll} but SFlash is running as {host} — "
        f"Windows cannot load a {dll} DLL into a {host} process, "
        f"and that is an OS rule, not an SFlash limitation. Ask "
        f"whoever supplies the DLL for a {host} build (suppliers "
        f"normally have both). Rebuilding SFlash as {dll} is not "
        f"an option: Qt 6/PySide6 has no {dll} Windows build."
    )


# ==================================================
# Export / import tables
# ==================================================
#
# Why these are here and not left to Dependency Walker: the two
# questions an operator cannot answer from the error message are
# "does my DLL actually export the function SFlash looks for,
# under that exact spelling" and "which DLL is it missing". A
# 32-bit stdcall export compiled without a .def file is spelled
# _GenerateKeyEx@28, which getattr() will never find even though
# the DLL loads fine — so printing the real names matters. Same
# three-read PE walk as read_pe_machine(), extended to follow
# RVAs through the section table.
# ==================================================

def _pe_sections(data):
    """[(virtual_address, size, raw_pointer, name)] or None."""

    if data[:2] != b"MZ":
        return None

    try:
        pe = struct.unpack_from("<I", data, 0x3C)[0]
        if data[pe:pe + 4] != b"PE\x00\x00":
            return None

        coff = pe + 4
        n_sections = struct.unpack_from("<H", data, coff + 2)[0]
        opt_size = struct.unpack_from("<H", data, coff + 16)[0]
        opt = coff + 20
        magic = struct.unpack_from("<H", data, opt)[0]

        sections = []
        for i in range(n_sections):
            o = opt + opt_size + i * 40
            sections.append((
                struct.unpack_from("<I", data, o + 12)[0],
                max(
                    struct.unpack_from("<I", data, o + 8)[0],
                    struct.unpack_from("<I", data, o + 16)[0],
                ),
                struct.unpack_from("<I", data, o + 20)[0],
                data[o:o + 8].rstrip(b"\x00").decode(
                    "latin-1", "replace"
                ),
            ))

        # Data directory: 96 bytes into the optional header for
        # PE32, 112 for PE32+ (the extra 16 are the wider
        # ImageBase/stack/heap fields).
        data_dir = opt + (96 if magic == 0x10B else 112)
        return sections, data_dir

    except (struct.error, IndexError, UnicodeDecodeError):
        return None


def _rva_to_offset(sections, rva):
    for virtual_address, size, raw_pointer, _ in sections:
        if virtual_address <= rva < virtual_address + size:
            return rva - virtual_address + raw_pointer
    return None


def _read_cstring(data, offset, limit=512):
    end = data.find(b"\x00", offset, offset + limit)
    if end < 0:
        return ""
    return data[offset:end].decode("latin-1", "replace")


def _read_pe(path):
    try:
        with open(path, "rb") as f:
            return f.read()
    except OSError:
        return None


def read_pe_exports(path):
    """
    Exported function names, in ordinal order. [] when the file
    has no export table or can't be parsed — never raises.
    """

    data = _read_pe(path)
    if data is None:
        return []

    parsed = _pe_sections(data)
    if parsed is None:
        return []
    sections, data_dir = parsed

    try:
        export_rva = struct.unpack_from("<I", data, data_dir)[0]
        if not export_rva:
            return []

        table = _rva_to_offset(sections, export_rva)
        if table is None:
            return []

        name_count = struct.unpack_from("<I", data, table + 24)[0]
        names_rva = struct.unpack_from("<I", data, table + 32)[0]
        names = _rva_to_offset(sections, names_rva)
        if names is None:
            return []

        found = []
        for i in range(min(name_count, 4096)):
            entry = struct.unpack_from("<I", data, names + i * 4)[0]
            offset = _rva_to_offset(sections, entry)
            if offset is None:
                continue
            found.append(_read_cstring(data, offset))
        return found

    except (struct.error, IndexError):
        return []


def read_pe_imports(path):
    """
    Names of the DLLs this image imports from — the WinError 126
    shortlist. [] when unparsable; never raises.
    """

    data = _read_pe(path)
    if data is None:
        return []

    parsed = _pe_sections(data)
    if parsed is None:
        return []
    sections, data_dir = parsed

    try:
        import_rva = struct.unpack_from("<I", data, data_dir + 8)[0]
        if not import_rva:
            return []

        entry = _rva_to_offset(sections, import_rva)
        if entry is None:
            return []

        found = []
        while len(found) < 256:
            lookup = struct.unpack_from("<I", data, entry)[0]
            name_rva = struct.unpack_from("<I", data, entry + 12)[0]
            if lookup == 0 and name_rva == 0:
                break
            offset = _rva_to_offset(sections, name_rva)
            if offset is None:
                break
            found.append(_read_cstring(data, offset))
            entry += 20
        return found

    except (struct.error, IndexError):
        return []


# ==================================================
# Loading
# ==================================================

def _root_cause(error):
    """
    The deepest chained exception's message. PyInstaller's ctypes
    hook re-raises the real OSError as PyInstallerImportError with
    its own fixed "not found when the application was frozen"
    text, so the actionable part (WinError 193/126) is only in
    __cause__/__context__.
    """

    seen = set()
    current = error

    while True:
        nxt = current.__cause__ or current.__context__
        if nxt is None or id(nxt) in seen:
            break
        seen.add(id(nxt))
        current = nxt

    return current


def _explain(path, error):
    """Builds the operator-facing message for a failed load."""

    root = _root_cause(error)
    detail = str(root) or str(error)
    win_error = getattr(root, "winerror", None) or getattr(
        error, "winerror", None
    )

    if not os.path.isfile(path):
        extra = ""
        if getattr(sys, "frozen", False):
            extra = (
                " (the Security DLL is an external file chosen at "
                "run time — it is not bundled into the .exe, so it "
                "has to exist at this exact path on this machine)"
            )
        return (
            f"Security DLL not found: {path}{extra}"
        )

    mismatch = bitness_mismatch(path)
    if mismatch:
        return (
            f"Cannot load Security DLL {os.path.basename(path)}: "
            f"{mismatch}"
        )

    if win_error == 126:
        return (
            f"Cannot load Security DLL {os.path.basename(path)}: "
            f"the file exists, but Windows could not find one of "
            f"the DLLs it depends on (WinError 126). Copy every "
            f"DLL it needs into {os.path.dirname(path) or '.'} "
            f"— including the Visual C++ runtime it was built "
            f"against — or install that runtime. Original error: "
            f"{detail}"
        )

    if win_error == 193:
        # Bitness, but the PE header was unreadable so
        # bitness_mismatch() stayed silent.
        return (
            f"Cannot load Security DLL {os.path.basename(path)}: "
            f"Windows rejected it as not a valid Win32 "
            f"application (WinError 193). This almost always "
            f"means an architecture mismatch — SFlash is running "
            f"as {host_bitness_name()}. Original error: {detail}"
        )

    architecture = describe_pe_machine(path)
    suffix = (
        f", DLL architecture {architecture}" if architecture else ""
    )

    return (
        f"Cannot load Security DLL {os.path.basename(path)}: "
        f"{detail}\n(the file does exist at {path}; SFlash is "
        f"running as {host_bitness_name()}{suffix})"
    )


def load_security_dll(dll_path):
    """
    Loads the DLL and returns the ctypes handle, or raises
    SecurityDllError with a message that names the actual cause.

    On Windows the DLL's own directory is added to the search
    path for the duration of the load, so a Seed&Key DLL that
    links against sibling DLLs resolves them — ctypes.CDLL with
    an absolute path does not do this by itself.
    """

    import ctypes

    if not dll_path:
        raise SecurityDllError("No Security DLL path given")

    if not os.path.isfile(dll_path):
        raise SecurityDllError(_explain(dll_path, OSError()))

    # Catch the hopeless case before asking Windows, so the
    # message is the same whether or not WinError survives
    # PyInstaller's re-raise.
    mismatch = bitness_mismatch(dll_path)
    if mismatch:
        raise SecurityDllError(
            f"Cannot load Security DLL "
            f"{os.path.basename(dll_path)}: {mismatch}"
        )

    directory = os.path.dirname(os.path.abspath(dll_path))
    add_dll_directory = getattr(os, "add_dll_directory", None)

    cookie = None
    if add_dll_directory is not None and os.path.isdir(directory):
        try:
            cookie = add_dll_directory(directory)
        except OSError:
            cookie = None

    try:
        return ctypes.CDLL(dll_path)
    except Exception as e:
        raise SecurityDllError(_explain(dll_path, e)) from e
    finally:
        if cookie is not None:
            try:
                cookie.close()
            except OSError:
                pass
