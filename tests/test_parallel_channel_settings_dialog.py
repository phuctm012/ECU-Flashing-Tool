# ==================================================
# Parallel Channel Settings Dialog Tests
# ==================================================
#
# ParallelChannelSettingsDialog is a plain form dialog (no
# QThread/worker involved, unlike TestConnectionDialog/
# GitLabFetchDialog) — its validation/parsing logic is exercised
# directly, without ever calling exec() (which would block waiting
# for real input), same reasoning CLAUDE.md documents for
# _write_log_file() vs its dialog-opening counterpart.
# ==================================================

import os
import sys
import unittest

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)

from tests.qt_test_utils import get_app
from gui.parallel_channel_settings_dialog import (
    ParallelChannelSettingsDialog,
)


class TestParallelChannelSettingsDialog(unittest.TestCase):

    def setUp(self):
        self.app = get_app()

    def _make_dialog(
        self, tx_id=0x778, rx_id=0x788, functional_id=0x700,
        is_customized=False,
    ):
        return ParallelChannelSettingsDialog(
            None, "Channel 1", tx_id, rx_id, functional_id, is_customized,
        )

    def test_fields_prefilled_with_given_values(self):
        dialog = self._make_dialog(tx_id=0x7A0, rx_id=0x7A8, functional_id=0x710)
        self.assertEqual(dialog.txIdEdit.text(), "0x7A0")
        self.assertEqual(dialog.rxIdEdit.text(), "0x7A8")
        self.assertEqual(dialog.functionalIdEdit.text(), "0x710")

    def test_window_title_includes_channel_label(self):
        dialog = self._make_dialog()
        self.assertIn("Channel 1", dialog.windowTitle())

    def test_save_with_valid_hex_accepts_and_returns_values(self):
        dialog = self._make_dialog()
        dialog.txIdEdit.setText("7A0")
        dialog.rxIdEdit.setText("0x7A8")
        dialog.functionalIdEdit.setText("710")

        dialog._on_save_clicked()

        self.assertTrue(dialog.result())
        self.assertFalse(dialog.reset_requested())
        self.assertEqual(
            dialog.result_values(), (0x7A0, 0x7A8, 0x710)
        )

    def test_save_with_invalid_hex_does_not_accept(self):
        dialog = self._make_dialog()
        dialog.txIdEdit.setText("not-hex")

        dialog._on_save_clicked()

        self.assertFalse(dialog.result())
        self.assertIsNone(dialog.result_values())

    def test_reset_to_shared_accepts_with_reset_flag_set(self):
        dialog = self._make_dialog(is_customized=True)

        dialog._on_reset_clicked()

        self.assertTrue(dialog.result())
        self.assertTrue(dialog.reset_requested())
        self.assertIsNone(dialog.result_values())


if __name__ == "__main__":
    unittest.main()
