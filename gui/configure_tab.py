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
)
from PySide6.QtGui import QColor
from PySide6.QtCore import Qt

from config.settings import (
    CAN_CONFIGS,
    FILE_FILTER,
    SUZUKI_RADAR_CAN_IDS,
)

from parsers.hex_parser import (
    parse_hex_file,
    HexParseError,
)
from parsers.srec_parser import (
    parse_srec_file,
    SrecParseError,
)
from parsers.binary_parser import (
    parse_binary_file,
    BinaryParseError,
)


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

        # Clear and add placeholder
        self.ui.tableWidgetDatablocks.setRowCount(0)
        self._add_placeholder_row()

        # Connect click event
        self.ui.tableWidgetDatablocks.cellClicked.connect(
            self.on_datablock_cell_clicked
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

            # Parse file based on extension
            datablock = self._parse_firmware_file(
                file_path
            )

            if datablock is None:
                continue

            # Store parsed datablock
            self._loaded_datablocks.append(datablock)

            # Add row to table
            row = (
                self.ui.tableWidgetDatablocks.rowCount()
                - 1
            )
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
            checksum_str = (
                f"0x{datablock.checksum:08X}"
            )
            self.ui.tableWidgetDatablocks.setItem(
                row, 3,
                QTableWidgetItem(checksum_str)
            )

            # Column 4: Signature
            self.ui.tableWidgetDatablocks.setItem(
                row, 4, QTableWidgetItem("")
            )

        # Update details for the last added file
        if self._loaded_datablocks:
            self._update_details_table(
                self._loaded_datablocks[-1]
            )

        # Log
        self.log_information(
            f"Loaded {len(file_paths)} file(s)"
        )

    # ==================================================
    # Parse Firmware File
    # ==================================================

    def _parse_firmware_file(self, file_path):
        """
        Parse a firmware file based on its extension.

        Returns:
            Datablock object or None if parsing failed.
        """

        ext = os.path.splitext(file_path)[1].lower()

        try:

            if ext == ".hex":
                datablock = parse_hex_file(file_path)

            elif ext in (".s19", ".srec", ".s37"):
                datablock = parse_srec_file(file_path)

            elif ext == ".bin":
                datablock = parse_binary_file(file_path)

            else:
                # Try HEX parser as default
                datablock = parse_hex_file(file_path)

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

        # Signature
        table.setItem(
            2, 1, QTableWidgetItem("")
        )

        # Compression
        table.setItem(
            3, 1, QTableWidgetItem("None")
        )

        # Encryption
        table.setItem(
            4, 1, QTableWidgetItem("None")
        )

        # Start address (first segment)
        if datablock.segments:

            first_seg = datablock.segments[0]

            table.setItem(
                5, 1,
                QTableWidgetItem(
                    f"0x{first_seg.start_address:08X}"
                )
            )

            # Total memory size
            table.setItem(
                6, 1,
                QTableWidgetItem(
                    f"{datablock.total_size} bytes "
                    f"({datablock.total_size / 1024:.1f} KB)"
                )
            )

        # Delta download
        table.setItem(
            7, 1, QTableWidgetItem("Disabled")
        )

    # ==================================================
    # Communication Logic
    # ==================================================

    def setup_communication_logic(self):

        if not hasattr(self.ui, 'comboBoxLogicalLink'):
            return

        if not hasattr(self.ui, 'tableWidgetCommDetails'):
            return

        # Add Virtual ECU Simulator option to hardware combobox
        if hasattr(self.ui, 'comboBoxHardware'):
            # Check if already has the virtual option
            has_virtual = False
            for i in range(self.ui.comboBoxHardware.count()):
                if "Virtual" in self.ui.comboBoxHardware.itemText(i):
                    has_virtual = True
                    break
            if not has_virtual:
                self.ui.comboBoxHardware.insertItem(
                    0,
                    "Virtual ECU Simulator (No Hardware)"
                )
                self.ui.comboBoxHardware.setCurrentIndex(0)

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

        # Radar side selector (Suzuki Radar ECU: Left/Right)
        self.setup_radar_side_selector()

        # Load initial config
        self.on_logical_link_changed(
            self.ui.comboBoxLogicalLink.currentIndex()
        )

        # Security Access DLL selector
        self.setup_security_dll_selector()

    # ==================================================
    # Radar Side selector (Suzuki Radar: Left/Right)
    # ==================================================

    def setup_radar_side_selector(self):
        """
        Connects the Radar Side combo (Left/Right — defined
        in main_window.ui) to apply_radar_side_to_table().
        Each side has its own physical Tx/Rx CAN ID (see
        SUZUKI_RADAR_CAN_IDS). Selecting a side writes its
        IDs into the Physical Request / Response CAN ID
        rows of tableWidgetCommDetails — the same table
        get_can_config() reads from, so the user can still
        fine-tune the value by hand afterward if needed.

        Left is the default (first item in the combo).
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

    # ==================================================
    # Real CAN Config (read from GUI, for real hardware)
    # ==================================================

    def get_can_config(self):
        """
        Reads the actual CAN connection parameters to use
        for real hardware, from the Communication page:
        - Channel number, parsed from the hardware combo
          (e.g. "VN1640A - Channel 2" -> channel index 1).
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
            hw_text = self.ui.comboBoxHardware.currentText()
            match = re.search(r'Channel\s+(\d+)', hw_text)
            if match:
                # Channel 1 in the UI -> index 0 for python-can
                config["channel"] = int(match.group(1)) - 1

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
