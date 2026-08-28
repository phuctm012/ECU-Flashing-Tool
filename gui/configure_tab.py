# ==================================================
# Configure Tab Logic
# ==================================================
#
# Handles all UI logic for the Configure tab:
# - Data page (Datablocks table, file selection, details)
# - Communication page (hardware, logical link, config)
# ==================================================

import os
import re

from PySide6.QtWidgets import (
    QTableWidgetItem,
    QFileDialog,
    QHeaderView,
    QMessageBox,
    QMenu,
    QLineEdit,
)
from PySide6.QtGui import QColor, QRegularExpressionValidator
from PySide6.QtCore import Qt, QRegularExpression

from config.settings import (
    CAN_CONFIGS,
    FILE_FILTER,
    SUZUKI_RADAR_CAN_IDS,
)

from parsers.auto_parser import parse_firmware_file
from parsers.hex_parser import HexParseError
from parsers.srec_parser import SrecParseError
from parsers.binary_parser import BinaryParseError


class ConfigureTabMixin:
    """
    Mixin class that adds Configure tab functionality
    to the MainWindow.
    """

    # ==================================================
    # Setup Configure Tab
    # ==================================================

    def setup_configure_tab(self):

        # Initialize loaded datablocks list
        self._loaded_datablocks = []

        # Security Access DLL (optional, real hardware only)
        self._security_dll_path = ""

        self.setup_datablocks_table()
        self.setup_communication_logic()

    # ==================================================
    # Datablocks Table
    # ==================================================

    def setup_datablocks_table(self):

        if not hasattr(self.ui, 'tableWidgetDatablocks'):
            return

        # Column widths
        header = self.ui.tableWidgetDatablocks.horizontalHeader()
        header.setSectionResizeMode(
            0, QHeaderView.ResizeToContents
        )
        header.setSectionResizeMode(
            1, QHeaderView.ResizeToContents
        )
        header.setSectionResizeMode(
            2, QHeaderView.Stretch
        )

        # Details table
        if hasattr(self.ui, 'tableWidgetDetails'):
            det_header = self.ui.tableWidgetDetails.horizontalHeader()
            det_header.setSectionResizeMode(
                0, QHeaderView.ResizeToContents
            )
            det_header.setSectionResizeMode(
                1, QHeaderView.Stretch
            )
            det_header.setSectionResizeMode(
                2, QHeaderView.ResizeToContents
            )
            self.ui.tableWidgetDetails.setMinimumHeight(280)

            # Compression Method / Encryption Method (rows 2/3)
            # are live single-hex-digit text fields embedded
            # directly in the Value cell — setCellWidget() is
            # Python-only (Designer's .ui format has no way to
            # express an embedded widget inside a specific table
            # cell), so this is built here rather than in
            # main_window.ui.
            self._setup_data_format_inputs()

        if hasattr(self.ui, 'buttonLoadFromGitLab'):
            self.ui.buttonLoadFromGitLab.clicked.connect(
                self.open_gitlab_fetch_dialog
            )

        # Clear and add placeholder
        self.ui.tableWidgetDatablocks.setRowCount(0)
        self._add_placeholder_row()

        # Connect click event
        self.ui.tableWidgetDatablocks.cellClicked.connect(
            self.on_datablock_cell_clicked
        )

        # Right-click context menu (Add/Disable/Remove)
        self.ui.tableWidgetDatablocks.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu
        )
        self.ui.tableWidgetDatablocks.customContextMenuRequested.connect(
            self._show_datablocks_context_menu
        )

    # ==================================================
    # Placeholder Row
    # ==================================================

    def _add_placeholder_row(self):

        row = self.ui.tableWidgetDatablocks.rowCount()
        self.ui.tableWidgetDatablocks.insertRow(row)

        item = QTableWidgetItem(
            "Please click here to add a Datablock"
        )
        item.setTextAlignment(Qt.AlignCenter)
        item.setForeground(QColor("gray"))
        item.setFlags(
            item.flags() & ~Qt.ItemIsEditable
        )

        self.ui.tableWidgetDatablocks.setItem(
            row, 0, item
        )

        # Span across all columns
        self.ui.tableWidgetDatablocks.setSpan(
            row, 0, 1, 5
        )

    # ==================================================
    # Cell Click Handler
    # ==================================================

    def on_datablock_cell_clicked(self, row, col):

        # Click on last row (placeholder) → add new
        total_rows = (
            self.ui.tableWidgetDatablocks.rowCount()
        )

        if row == total_rows - 1:
            self.add_new_datablock()

    # ==================================================
    # Right-Click Context Menu (Add/Disable/Remove)
    # ==================================================

    def _build_datablocks_context_menu(self, pos):
        """
        Add Datablock always shows (same file dialog as
        clicking the placeholder row). Disable/Remove only
        show when right-clicking an actual datablock row —
        row index i corresponds to self._loaded_datablocks[i]
        (see get_checked_datablocks()'s docstring for why that
        invariant holds), so rowAt() directly indexes it.

        Split from _show_datablocks_context_menu() (which
        pops it up via menu.exec(), a blocking call) so tests
        can build the menu and inspect its actions without
        opening a real modal event loop.
        """

        table = self.ui.tableWidgetDatablocks
        row = table.rowAt(pos.y())
        is_datablock_row = 0 <= row < len(self._loaded_datablocks)

        menu = QMenu(self)

        add_action = menu.addAction("Add Datablock")
        add_action.triggered.connect(self.add_new_datablock)

        if is_datablock_row:
            menu.addSeparator()

            disable_action = menu.addAction("Disable Datablock")
            disable_action.triggered.connect(
                lambda: self._disable_datablock_row(row)
            )

            remove_action = menu.addAction("Remove Datablock")
            remove_action.triggered.connect(
                lambda: self._remove_datablock_row(row)
            )

        return menu

    def _show_datablocks_context_menu(self, pos):

        menu = self._build_datablocks_context_menu(pos)
        menu.exec(self.ui.tableWidgetDatablocks.mapToGlobal(pos))

    def _disable_datablock_row(self, row):
        """
        Unticks the row's checkbox (column 0) — the same state
        get_checked_datablocks() already excludes from
        flashing/segments, so this reuses that existing
        mechanism instead of adding a separate "disabled" flag.
        """

        check_item = self.ui.tableWidgetDatablocks.item(row, 0)
        if check_item is None:
            return

        check_item.setCheckState(Qt.Unchecked)

        self.log_information(
            f"Disabled datablock: "
            f"{self._loaded_datablocks[row].file_name}"
        )

    def _remove_datablock_row(self, row):
        """
        Removes both the table row and the matching
        self._loaded_datablocks entry together, at the same
        index, so row i keeps lining up with datablock i for
        every later row (get_checked_datablocks(), Save
        Project, Export Issue, ...).
        """

        if not (0 <= row < len(self._loaded_datablocks)):
            return

        datablock = self._loaded_datablocks.pop(row)
        self.ui.tableWidgetDatablocks.removeRow(row)

        if self._loaded_datablocks:
            self._update_details_table(self._loaded_datablocks[-1])
        else:
            self._clear_details_table()

        self.log_information(
            f"Removed datablock: {datablock.file_name}"
        )

    # ==================================================
    # Add New Datablock (with real parsing)
    # ==================================================

    def add_new_datablock(self):
        """Open file dialog and parse selected files."""

        file_paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Select Firmware Files",
            "",
            FILE_FILTER,
        )

        if not file_paths:
            return

        for file_path in file_paths:
            self._load_firmware_file(file_path)

        # Update details for the last added file
        if self._loaded_datablocks:
            self._update_details_table(
                self._loaded_datablocks[-1]
            )

        # Log
        self.log_information(
            f"Loaded {len(file_paths)} file(s)"
        )

    def _load_firmware_file(self, file_path):
        """
        Parse a single firmware file and, on success, append it
        as a new row in tableWidgetDatablocks and record it in
        Recent Files (gui/menu_bar.py's _record_recent_file()).

        Shared by add_new_datablock() (looping over file-dialog
        picks) and gui/menu_bar.py's load_recent_file() (a single
        path from the File > Recent Files submenu) so both go
        through identical parsing/error handling — a missing or
        moved recent file surfaces the same "Parse Error" dialog
        _parse_firmware_file() already shows, no separate handling
        needed.

        Returns True if loaded, False if parsing failed.
        """

        datablock = self._parse_firmware_file(file_path)

        if datablock is None:
            return False

        # Store parsed datablock
        self._loaded_datablocks.append(datablock)

        # Add row to table
        row = self.ui.tableWidgetDatablocks.rowCount() - 1
        self.ui.tableWidgetDatablocks.insertRow(row)

        # Column 0: Checkbox
        check_item = QTableWidgetItem("")
        check_item.setCheckState(Qt.Checked)
        self.ui.tableWidgetDatablocks.setItem(
            row, 0, check_item
        )

        # Column 1: Type
        self.ui.tableWidgetDatablocks.setItem(
            row, 1,
            QTableWidgetItem(datablock.file_type)
        )

        # Column 2: Datablock (filename)
        self.ui.tableWidgetDatablocks.setItem(
            row, 2,
            QTableWidgetItem(datablock.file_name)
        )

        # Column 3: Checksum (CRC32)
        checksum_str = f"0x{datablock.checksum:08X}"
        self.ui.tableWidgetDatablocks.setItem(
            row, 3,
            QTableWidgetItem(checksum_str)
        )

        # Column 4: Signature
        self.ui.tableWidgetDatablocks.setItem(
            row, 4, QTableWidgetItem("")
        )

        self._record_recent_file(file_path)

        return True

    # ==================================================
    # Checked Datablocks (respects the checkbox column)
    # ==================================================

    def get_checked_datablocks(self):
        """
        Returns the subset of self._loaded_datablocks whose
        checkbox (column 0 of tableWidgetDatablocks) is
        checked — used by flash_button_clicked() to exclude
        unchecked files from the flash sequence/segments
        table instead of always flashing everything loaded.

        Relies on datablock i (in self._loaded_datablocks)
        being at table row i: add_new_datablock() only ever
        appends rows in the same order it appends datablocks,
        and _remove_datablock_row() (Datablocks table's
        right-click "Remove Datablock") always removes the
        table row and the list entry together at the same
        index, so the two stay in lockstep — rowCount() is
        always len(_loaded_datablocks) + 1 (the trailing "add a
        Datablock" placeholder row) once that's true.

        If self._loaded_datablocks was populated some other
        way (e.g. tests injecting a Datablock directly to skip
        the file dialog) that invariant doesn't hold — row 0
        would be the placeholder, not a real checkbox for
        datablock 0 — so this falls back to returning
        everything unfiltered rather than misreading the
        placeholder as "unchecked".
        """

        datablocks = getattr(self, '_loaded_datablocks', [])

        if not hasattr(self.ui, 'tableWidgetDatablocks'):
            return datablocks

        table = self.ui.tableWidgetDatablocks

        if table.rowCount() < len(datablocks) + 1:
            return datablocks

        checked = []

        for i, datablock in enumerate(datablocks):
            check_item = table.item(i, 0)
            if check_item is None:
                # Row missing for some reason — default to
                # included rather than silently dropping it.
                checked.append(datablock)
                continue
            if check_item.checkState() == Qt.Checked:
                checked.append(datablock)

        return checked

    # ==================================================
    # Parse Firmware File
    # ==================================================

    def _parse_firmware_file(self, file_path):
        """
        Parse a firmware file based on its extension.

        Returns:
            Datablock object or None if parsing failed.
        """

        try:

            datablock = parse_firmware_file(file_path)

            self.log_information(
                f"Parsed {datablock.file_name}: "
                f"{datablock.segment_count} segment(s), "
                f"{datablock.total_size} bytes total"
            )

            self.log_trace(
                f"File: {file_path}"
            )

            for i, seg in enumerate(datablock.segments):
                self.log_trace(
                    f"  Segment {i+1}: "
                    f"0x{seg.start_address:08X} - "
                    f"0x{seg.end_address:08X} "
                    f"({seg.length} bytes)"
                )

            return datablock

        except (HexParseError, SrecParseError,
                BinaryParseError) as e:

            QMessageBox.warning(
                self,
                "Parse Error",
                f"Failed to parse file:\n"
                f"{file_path}\n\n"
                f"Error: {e}"
            )

            self.log_information(
                f"Parse error: {e}"
            )

            return None

    # ==================================================
    # Update Details Table
    # ==================================================

    def _update_details_table(self, datablock):
        """Update the details table with datablock info."""

        if not hasattr(self.ui, 'tableWidgetDetails'):
            return

        table = self.ui.tableWidgetDetails

        # File path
        table.setItem(
            0, 1,
            QTableWidgetItem(datablock.file_path)
        )

        # Checksum
        table.setItem(
            1, 1,
            QTableWidgetItem(
                f"0x{datablock.checksum:08X}"
            )
        )

        # Compression Method / Encryption Method (rows 2/3) are
        # NOT touched here — they're live QLineEdit widgets
        # embedded via setCellWidget() (see
        # _setup_data_format_inputs()), a persistent global
        # RequestDownload setting rather than a per-datablock
        # property, so they keep whatever the user typed
        # instead of being re-rendered on every file load.

        # Start address (first segment)
        if datablock.segments:

            first_seg = datablock.segments[0]

            table.setItem(
                4, 1,
                QTableWidgetItem(
                    f"0x{first_seg.start_address:08X}"
                )
            )

            # Total memory size
            table.setItem(
                5, 1,
                QTableWidgetItem(
                    f"{datablock.total_size} bytes "
                    f"({datablock.total_size / 1024:.1f} KB)"
                )
            )

    def _clear_details_table(self):
        """Blanks the value column — used when the last
        remaining datablock is removed, so stale info from a
        deleted datablock doesn't linger on screen. Skips rows
        2/3 (Compression/Encryption Method) — those are a
        persistent global setting via embedded QLineEdit
        widgets, not per-datablock info to clear."""

        if not hasattr(self.ui, 'tableWidgetDetails'):
            return

        table = self.ui.tableWidgetDetails
        for row in range(table.rowCount()):
            if row in (2, 3):
                continue
            table.setItem(row, 1, QTableWidgetItem(""))

    # ==================================================
    # Communication Logic
    # ==================================================

    def setup_communication_logic(self):

        if not hasattr(self.ui, 'comboBoxLogicalLink'):
            return

        if not hasattr(self.ui, 'tableWidgetCommDetails'):
            return

        # Hardware combobox: Virtual ECU Simulator + any real
        # Vector hardware actually detected on this machine.
        self.populate_hardware_combo()

        if hasattr(self.ui, 'buttonRefreshHardware'):
            self.ui.buttonRefreshHardware.clicked.connect(
                self.populate_hardware_combo
            )

        if hasattr(self.ui, 'buttonTestConnectionHardware'):
            self.ui.buttonTestConnectionHardware.clicked.connect(
                self.test_connection_button_clicked
            )

        # A stale green/red from a previous run would otherwise
        # keep showing after the user picks different hardware —
        # clear it as soon as the selection changes so the color
        # never implies "already tested" for hardware that hasn't
        # been.
        if hasattr(self.ui, 'comboBoxHardware'):
            self.ui.comboBoxHardware.currentIndexChanged.connect(
                lambda _: self._reset_test_connection_button_status()
            )

        # Column widths
        comm_header = self.ui.tableWidgetCommDetails.horizontalHeader()
        comm_header.setSectionResizeMode(
            0, QHeaderView.ResizeToContents
        )
        comm_header.setStretchLastSection(True)

        if hasattr(self.ui, 'tableWidgetCustomConfig'):
            custom_header = self.ui.tableWidgetCustomConfig.horizontalHeader()
            custom_header.setSectionResizeMode(
                0, QHeaderView.ResizeToContents
            )
            custom_header.setStretchLastSection(True)
            # Height is fixed in main_window.ui to exactly
            # fit its 4 rows (no extra blank space below).

        # Connect ComboBox change
        self.ui.comboBoxLogicalLink.currentIndexChanged.connect(
            self.on_logical_link_changed
        )

        # Radar side selector (Suzuki Radar ECU: S0/S1)
        self.setup_radar_side_selector()

        # Load initial config
        self.on_logical_link_changed(
            self.ui.comboBoxLogicalLink.currentIndex()
        )

        # Security Access DLL selector
        self.setup_security_dll_selector()

        # Fingerprint (DID 0xF198 Tester Serial Number)
        self.setup_fingerprint_selector()

    # ==================================================
    # Hardware combobox (Virtual + real Vector channels)
    # ==================================================

    def populate_hardware_combo(self):
        """
        Fills comboBoxHardware with "Virtual ECU Simulator"
        plus one entry per real Vector channel actually
        detected on this machine right now (empty if
        python-can/the Vector driver isn't installed, or no
        hardware is plugged in — no fake placeholder channels).

        Re-run via the Refresh button to pick up hardware
        plugged in after the app started.

        If detection came back empty because something is
        actually broken on this machine (not just "nothing
        plugged in") — e.g. this particular build wasn't
        packaged with python-can, or the Vector XL Driver
        Library was never installed here — logs the real reason
        to the Information tab. Added after a real deployment:
        same .exe worked on one bench PC and came back empty on
        another with hardware plugged in on both, with no way to
        tell why without a console (a --windowed PyInstaller
        build has none).
        """

        if not hasattr(self.ui, 'comboBoxHardware'):
            return

        # Rebuilding below runs with signals blocked (no
        # currentIndexChanged fires), so the reset connected to
        # that signal in setup_communication_logic() wouldn't
        # otherwise catch a Refresh — do it explicitly here too.
        self._reset_test_connection_button_status()

        combo = self.ui.comboBoxHardware
        combo.blockSignals(True)

        combo.clear()
        combo.addItem(
            "Virtual ECU Simulator (No Hardware)", userData=None
        )

        from communication.vector_can import (
            detect_vector_channels_with_error,
        )

        channels, error = detect_vector_channels_with_error()

        for ch in channels:
            combo.addItem(ch["label"], userData=ch)

        combo.setCurrentIndex(0)
        combo.blockSignals(False)

        if error and hasattr(self, 'log_information'):
            self.log_information(
                f"No real Vector hardware detected: {error}"
            )

    # ==================================================
    # Test Connection button (Communication page)
    # ==================================================

    def test_connection_button_clicked(self):
        """
        Runs the same probe as Tools > Test Connection...
        (gui/menu_bar.py's open_test_connection_dialog()), then
        colors this button green/red based on the outcome so
        the result stays visible on the Communication page
        without reopening the dialog.
        """

        dialog = self.open_test_connection_dialog()

        if dialog is None or dialog.passed is None:
            # Declined the CAN conflict warning, or closed the
            # dialog before the probe finished — no result to
            # show, leave whatever color was already there.
            return

        self._set_test_connection_button_result(dialog.passed)

    def _set_test_connection_button_result(self, passed):

        if not hasattr(self.ui, 'buttonTestConnectionHardware'):
            return

        bg, fg = self._status_colors('done' if passed else 'error')

        self.ui.buttonTestConnectionHardware.setStyleSheet(
            f"background-color: {bg}; color: {fg};"
        )

    def _reset_test_connection_button_status(self):

        if not hasattr(self.ui, 'buttonTestConnectionHardware'):
            return

        self.ui.buttonTestConnectionHardware.setStyleSheet("")

    # ==================================================
    # Radar Side selector (Suzuki Radar: S0/S1)
    # ==================================================

    def setup_radar_side_selector(self):
        """
        Connects the Radar Side combo (S0/S1 — defined
        in main_window.ui) to apply_radar_side_to_table().
        Each side has its own physical Tx/Rx CAN ID (see
        SUZUKI_RADAR_CAN_IDS). Selecting a side writes its
        IDs into the Physical Request / Response CAN ID
        rows of tableWidgetCommDetails — the same table
        get_can_config() reads from, so the user can still
        fine-tune the value by hand afterward if needed.

        S0 is the default (first item in the combo).
        """

        if not hasattr(self.ui, 'comboBoxRadarSide'):
            return

        self.ui.comboBoxRadarSide.currentIndexChanged.connect(
            lambda _: self.apply_radar_side_to_table()
        )

    def apply_radar_side_to_table(self):
        """
        Writes the selected radar side's Tx/Rx CAN IDs into
        tableWidgetCommDetails (Physical Request / Response
        CAN ID rows).
        """

        if not hasattr(self.ui, 'comboBoxRadarSide'):
            return

        if not hasattr(self.ui, 'tableWidgetCommDetails'):
            return

        sides = list(SUZUKI_RADAR_CAN_IDS.keys())
        index = self.ui.comboBoxRadarSide.currentIndex()

        if index < 0 or index >= len(sides):
            return

        ids = SUZUKI_RADAR_CAN_IDS[sides[index]]

        table = self.ui.tableWidgetCommDetails

        for row in range(table.rowCount()):

            prop_item = table.item(row, 0)

            if prop_item is None:
                continue

            if prop_item.text().strip() == "Physical Request CAN ID":
                table.setItem(
                    row, 1, QTableWidgetItem(ids["tx_id"])
                )
            elif prop_item.text().strip() == "Response CAN ID":
                table.setItem(
                    row, 1, QTableWidgetItem(ids["rx_id"])
                )

    # ==================================================
    # Security Access DLL (SecurityAccess key calculation)
    # ==================================================

    def setup_security_dll_selector(self):
        """
        Connects the "Browse..." button next to the
        Security Access DLL field (defined in
        main_window.ui) to browse_security_dll(). Lets the
        user point SecurityAccess (0x27) key calculation at
        an external DLL (real hardware), instead of the
        built-in seed/key algorithm used by default.
        """

        if not hasattr(self.ui, 'buttonBrowseSecurityDll'):
            return

        self.ui.buttonBrowseSecurityDll.clicked.connect(
            self.browse_security_dll
        )

    def browse_security_dll(self):

        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Security Access DLL",
            "",
            "Dynamic Libraries (*.dll *.so *.dylib);;"
            "All Files (*)",
        )

        if not file_path:
            return

        self._security_dll_path = file_path
        self.ui.lineEditSecurityDll.setText(file_path)

        if hasattr(self, 'log_information'):
            self.log_information(
                f"Security DLL selected: "
                f"{os.path.basename(file_path)}"
            )

        if hasattr(self, 'save_profile'):
            self.save_profile()

    # ==================================================
    # Fingerprint (WriteDataByIdentifier DID 0xF198)
    # ==================================================

    def setup_fingerprint_selector(self):
        """
        Validates/wires lineEditTesterSerialNumber (Miscellaneous
        page, defined in main_window.ui) — the DID 0xF198
        (WriteDataByIdentifier) payload sent by the Suzuki SLP1
        sequence's "Write Tester Info" step (see
        core/flash_sequence.py's SUZUKI_SLP1_FLASH_SEQUENCE and
        build_suzuki_slp1_flash_sequence()'s tester_serial_number
        param). The Generic sequence has no equivalent step, so
        this field is ignored when it's selected.

        A QRegularExpressionValidator restricts input to a hex
        string up to 20 characters (10 bytes, matching this
        ECU's fixed DID length — a wrong length would very
        likely get NRC'd by a real ECU); lowercase is
        auto-uppercased as the user types, same as Compression/
        Encryption Method's fields (_on_hex_input_text_edited(),
        shared across all three).
        """

        if not hasattr(self.ui, 'lineEditTesterSerialNumber'):
            return

        self.ui.lineEditTesterSerialNumber.setMaxLength(20)
        self.ui.lineEditTesterSerialNumber.setValidator(
            QRegularExpressionValidator(
                QRegularExpression("[0-9A-Fa-f]{0,20}")
            )
        )
        self.ui.lineEditTesterSerialNumber.textEdited.connect(
            self._on_hex_input_text_edited
        )

    def get_tester_serial_number(self):
        """
        Returns the DID 0xF198 payload as bytes, from the
        current field text — read by gui/flash_tab.py before
        building the Suzuki SLP1 sequence. Falls back to the
        documented default (00 11 22 33 44 55 66 77 88 99) if
        the field is missing, empty, an odd number of hex
        characters, or (loaded from a hand-edited .sfproj/
        settings file, bypassing the interactive validator)
        not valid hex at all — rather than sending a malformed
        WriteDataByIdentifier to the ECU.
        """

        default = bytes.fromhex("00112233445566778899")

        if not hasattr(self.ui, 'lineEditTesterSerialNumber'):
            return default

        text = self.ui.lineEditTesterSerialNumber.text().strip()

        if not text or len(text) % 2 != 0:
            return default

        try:
            return bytes.fromhex(text)
        except ValueError:
            return default

    # ==================================================
    # Data Format (RequestDownload dataFormatIdentifier)
    # ==================================================

    def _setup_data_format_inputs(self):
        """
        Builds lineEditCompressionMethod/lineEditEncryptionMethod
        in Python and embeds them directly into
        tableWidgetDetails' Value column (rows 2/3 — Compression
        Method/Encryption Method) via setCellWidget(). Not
        expressible in main_window.ui: Designer's table-widget
        rows are static text/checkbox only, not arbitrary
        embedded widgets, so this is exactly the kind of
        logic-driven content CLAUDE.md's ".ui first" rule
        carves out for Python-side construction.

        A single hex digit (0-F) typed directly rather than a
        dropdown — the field IS the RequestDownload
        dataFormatIdentifier nibble value (0 = None, 1-F =
        manufacturer-specific), read by
        get_data_format_config(). A QRegularExpressionValidator
        restricts input to exactly one hex character; lowercase
        is auto-uppercased as the user types
        (_on_hex_input_text_edited()). This only changes what
        the tool tells the ECU the data format is — it does not
        actually compress/encrypt the firmware bytes being sent;
        the caller is responsible for the loaded file already
        being in that format if a non-zero value is used.

        Attached as self.ui.lineEditCompressionMethod/
        lineEditEncryptionMethod (the objectName a .ui
        declaration would have given them) so every other
        caller — get_data_format_config(),
        gui/settings_profile.py, gui/project_file.py, tests —
        can find them the same way as any other named widget.
        Persistence (save/load) is wired separately in
        gui/settings_profile.py, after its own load_profile()
        call — see that file's comments for why the ordering
        matters.
        """

        table = self.ui.tableWidgetDetails
        validator = QRegularExpressionValidator(
            QRegularExpression("[0-9A-Fa-f]")
        )

        compression_input = QLineEdit("0")
        compression_input.setObjectName("lineEditCompressionMethod")
        compression_input.setMaxLength(1)
        compression_input.setValidator(validator)
        compression_input.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        compression_input.textEdited.connect(
            self._on_hex_input_text_edited
        )
        table.setCellWidget(2, 1, compression_input)
        self.ui.lineEditCompressionMethod = compression_input

        encryption_input = QLineEdit("0")
        encryption_input.setObjectName("lineEditEncryptionMethod")
        encryption_input.setMaxLength(1)
        encryption_input.setValidator(validator)
        encryption_input.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        encryption_input.textEdited.connect(
            self._on_hex_input_text_edited
        )
        table.setCellWidget(3, 1, encryption_input)
        self.ui.lineEditEncryptionMethod = encryption_input

    def _on_hex_input_text_edited(self, text):
        """
        Auto-uppercase lowercase hex digits as the user types.
        Shared by every hex-typed field on the Configure tab —
        Compression/Encryption Method and Tester Serial Number.
        Connected to textEdited (fires only on user input, not
        setText() calls) so this can't recurse into itself or
        fight with load_profile()/_apply_project_data() setting
        the field programmatically.
        """

        if text != text.upper():
            self.sender().setText(text.upper())

    def get_data_format_config(self):
        """
        Returns {"compression": int, "encrypting": int} (each
        0-15) from the current field text — read by
        FlashWorker/cli.py to build RequestDownload's
        dataFormatIdentifier byte. Defaults to
        {"compression": 0, "encrypting": 0} (None/None) if the
        fields aren't present or are empty.
        """

        compression = 0
        encrypting = 0

        if hasattr(self.ui, 'lineEditCompressionMethod'):
            text = self.ui.lineEditCompressionMethod.text().strip()
            if text:
                compression = int(text, 16)

        if hasattr(self.ui, 'lineEditEncryptionMethod'):
            text = self.ui.lineEditEncryptionMethod.text().strip()
            if text:
                encrypting = int(text, 16)

        return {
            "compression": compression,
            "encrypting": encrypting,
        }

    # ==================================================
    # CAN Bus Conflict Detection (CANoe/CANalyzer/CANape)
    # ==================================================

    def detect_can_conflict_warning(self):
        """
        Best-effort check for a likely CAN bus conflict before
        starting a real (non-virtual) flash — users sometimes
        forget a Vector desktop tool (CANoe/CANalyzer/CANape)
        is still open with a measurement running, which can
        collide with this tool's own UDS session (e.g. two
        TesterPresent senders, or CANoe holding the channel).

        Combines two independent, best-effort signals:
        - detect_running_vector_tools(): is a known Vector
          tool process running at all (Windows only)?
        - detect_vector_channels()'s "is_on_bus" flag for the
          currently selected channel: does the driver report
          the channel as already active, regardless of which
          process opened it?

        Returns:
            A warning message string if either signal fires,
            otherwise None (nothing to warn about, or the
            checks aren't applicable/available on this
            platform).
        """

        from communication.vector_can import (
            detect_running_vector_tools,
            detect_vector_channels,
        )

        running_tools = detect_running_vector_tools()

        channel_index = None
        if hasattr(self.ui, 'comboBoxHardware'):
            data = self.ui.comboBoxHardware.currentData()
            if data is not None:
                channel_index = data.get("channel")

        busy_channel_label = None
        if channel_index is not None:
            for ch in detect_vector_channels():
                if ch["channel"] == channel_index and ch.get("is_on_bus"):
                    busy_channel_label = ch["label"]
                    break

        if not running_tools and not busy_channel_label:
            return None

        lines = [
            "A possible CAN bus conflict was detected before "
            "flashing:",
        ]

        if running_tools:
            lines.append(
                "  - Running Vector tool(s): "
                + ", ".join(name.upper() for name in running_tools)
            )

        if busy_channel_label:
            lines.append(
                f"  - Channel already active on the bus: "
                f"{busy_channel_label}"
            )

        lines.append(
            "\nIf CANoe/CANalyzer/CANape is running a "
            "measurement on the same channel, its own "
            "TesterPresent or diagnostic activity can "
            "collide with this tool's UDS session and cause "
            "the flash to fail unpredictably."
        )
        lines.append(
            "\nClose the other tool (or stop its measurement) "
            "before continuing, unless you're intentionally "
            "sharing the bus and know what you're doing."
        )

        return "\n".join(lines)

    # ==================================================
    # Real CAN Config (read from GUI, for real hardware)
    # ==================================================

    def get_can_config(self):
        """
        Reads the actual CAN connection parameters to use
        for real hardware, from the Communication page:
        - Channel number, from the hardware combo's
          userData (the real channel index reported by
          detect_vector_channels(); None/Virtual -> 0,
          unused for the Virtual ECU Simulator).
        - Physical Request/Response CAN ID and Baudrate,
          parsed from tableWidgetCommDetails — these cells
          are editable, so the user can override the
          placeholder defaults with the ECU's real IDs.
        - CAN FD on/off, from the logical link combo.

        Returns:
            dict with keys: channel, tx_id, rx_id, bitrate,
            fd, data_bitrate.
        """

        config = {
            "channel": 0,
            "tx_id": 0x778,
            "rx_id": 0x788,
            "bitrate": 500000,
            "fd": False,
            "data_bitrate": 2000000,
        }

        if hasattr(self.ui, 'comboBoxHardware'):
            data = self.ui.comboBoxHardware.currentData()
            if data is not None:
                config["channel"] = data.get(
                    "hw_channel", data.get("channel", 0)
                )
                serial = data.get("serial")
                if serial:
                    config["serial"] = serial
                label = data.get("label")
                if label:
                    config["label"] = label

        if hasattr(self.ui, 'comboBoxLogicalLink'):
            config["fd"] = (
                "FD" in self.ui.comboBoxLogicalLink.currentText()
            )

        if hasattr(self.ui, 'tableWidgetCommDetails'):

            table = self.ui.tableWidgetCommDetails

            for row in range(table.rowCount()):

                prop_item = table.item(row, 0)
                val_item = table.item(row, 1)

                if prop_item is None or val_item is None:
                    continue

                prop = prop_item.text().strip()
                val = val_item.text().strip()

                try:
                    if prop == "Physical Request CAN ID":
                        config["tx_id"] = int(val, 16)
                    elif prop == "Response CAN ID":
                        config["rx_id"] = int(val, 16)
                    elif prop == "Baudrate":
                        digits = re.sub(r'[^\d]', '', val)
                        if digits:
                            config["bitrate"] = int(digits)
                    elif prop == "Data Baudrate":
                        digits = re.sub(r'[^\d]', '', val)
                        if digits:
                            config["data_bitrate"] = int(digits)
                except ValueError:
                    pass

        return config

    # ==================================================
    # Logical Link Changed
    # ==================================================

    def on_logical_link_changed(self, index):

        text = self.ui.comboBoxLogicalLink.itemText(
            index
        )

        table = self.ui.tableWidgetCommDetails

        configs = CAN_CONFIGS.get(text, [])

        table.setRowCount(len(configs))

        for row, (prop, val) in enumerate(configs):

            table.setItem(
                row, 0, QTableWidgetItem(prop)
            )
            table.setItem(
                row, 1, QTableWidgetItem(val)
            )

        # Re-apply the selected Radar Side's CAN IDs — the
        # table was just repopulated with generic defaults.
        self.apply_radar_side_to_table()
