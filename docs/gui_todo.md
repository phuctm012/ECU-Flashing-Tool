# GUI TODO — Component Chưa Hoạt Động & Tính Năng Đề Xuất

Ghi lại từ audit ngày 2026-08-22: đối chiếu từng widget có tên trong `gui/main_window.ui`
với usage thật trong `gui/*.py` để tìm các component không có tác dụng, hiển thị dữ
liệu giả, hoặc chưa được implement logic đứng sau. Cập nhật trạng thái ở đây khi xử lý.

## Danh sách

### 1. Combo "Checksum Method" — hoàn toàn chết (dead)

- **Trạng thái**: ✅ Đã xử lý (2026-08-22) — xóa hẳn khỏi `.ui` (hướng (a))
- **Đã làm**: xóa `comboBoxChecksum`, `labelChecksumMethod`, `horizontalLayout_checksumMethod`, và section header `labelAddSetup` ("Additional Setup" — chỉ tồn tại để bọc control này, không còn gì bên dưới nên xóa luôn) khỏi `gui/main_window.ui`; regenerate `gui/ui_main_window.py`. Xóa `CHECKSUM_METHODS` (dead constant) khỏi `config/settings.py`.
- **Vấn đề (trước khi xử lý)**: 2 lựa chọn "Pre-Calculation: via file selection" / "Calculate during flash", nhưng không có dòng code Python nào đọc giá trị của nó. Checksum thực tế luôn tính CRC32 lúc parse file, bất kể chọn gì ở combo này.

### 2. Checkbox cột đầu bảng Datablocks — không có tác dụng

- **Trạng thái**: ✅ Đã xử lý (2026-08-22) — đọc `checkState()` để lọc (hướng (b))
- **Đã làm**: thêm `ConfigureTabMixin.get_checked_datablocks()` ([gui/configure_tab.py](../gui/configure_tab.py)) — trả về subset `self._loaded_datablocks` mà checkbox cột 0 đang tick (dựa vào invariant: datablock thứ i luôn ở row i, vì không có chức năng xóa row). `flash_button_clicked()` giờ gọi hàm này thay vì đọc thẳng `self._loaded_datablocks`, rồi truyền list đã lọc xuyên suốt: `prepare_flash_ui(datablocks)` → `add_segments_from_datablocks(datablocks)` (Segments table) và `build_flash_sequence(datablocks)`/`build_suzuki_slp1_flash_sequence(datablocks)` (flash sequence thật) — cả hiển thị lẫn logic flash giờ nhất quán, bỏ tick 1 dòng sẽ loại nó khỏi cả hai.
- **Vấn đề (trước khi xử lý)**: mỗi file firmware nạp vào có 1 checkbox (mặc định tick), trông như để bật/tắt "có flash file này hay không". Trạng thái checkbox không bao giờ được đọc lại — bỏ tick 1 dòng không loại trừ nó khỏi flash sequence; mọi datablock đã nạp luôn bị flash hết.

### 3. Cột "Signature" trong bảng Datablocks — luôn rỗng

- **Trạng thái**: ⬜ Chưa xử lý
- **Vị trí**: [gui/configure_tab.py:205](../gui/configure_tab.py#L205)
- **Vấn đề**: set cứng `QTableWidgetItem("")` cho mọi file — tính năng verify chữ ký chưa được implement.
- **Hướng xử lý**: implement verify signature thật, hoặc xóa cột nếu không có kế hoạch làm.

### 4. Bảng Details — 4 dòng luôn hardcode, không phản ánh file thật

- **Trạng thái**: ⬜ Chưa xử lý
- **Vị trí**: [gui/configure_tab.py:300-336](../gui/configure_tab.py#L300-L336) (`_update_details_table()`)
- **Vấn đề**: "Signature" (rỗng), "Compression" ("None"), "Encryption" ("None"), "Delta download" ("Disabled") — set y hệt cho mọi file, không có logic phân tích thật đứng sau.
- **Hướng xử lý**: implement phân tích thật (nếu các tính năng này thực sự cần), hoặc xóa các dòng này khỏi bảng Details để tránh gây hiểu lầm.

### 5. Bảng "Custom Configuration" (tab Custom Actions) — decoration, sửa được nhưng vô nghĩa

- **Trạng thái**: ⬜ Chưa xử lý
- **Vị trí**: [gui/main_window.ui:663](../gui/main_window.ui#L663) — `tableWidgetCustomConfig`
- **Vấn đề**: hiển thị 4 dòng trông như config thật (Erase Timeout: 120 sec, Programming delay: 2 sec, Post reset delay: 1 sec, STmin override: 50 msec). Bảng không bị khóa read-only (khác `traceTable`) nên user double-click sửa số được — nhưng không có code nào trong `core/flash_controller.py`/`uds_client.py` đọc giá trị từ bảng này; sửa gì cũng không ảnh hưởng lúc flash thật.
- **Hướng xử lý**: (a) implement thật — đọc 4 giá trị này và truyền vào `FlashWorker`/`UdsClient` (vd. override timeout Erase Memory, delay giữa các bước, STmin ISO-TP), hoặc (b) khóa read-only + ghi rõ đây chỉ là hiển thị tham khảo nếu chưa có kế hoạch implement.

### 6. Segments table hiện dữ liệu demo giả khi chưa nạp file nào

- **Trạng thái**: ✅ Đã xử lý (2026-08-22) — làm cả 2 hướng (không chỉ chọn 1)
- **Đã làm**: `add_segments_from_datablocks()` ([gui/flash_tab.py](../gui/flash_tab.py)) bỏ hẳn nhánh `else` fallback demo — không có datablock thì bảng Segments để trống, không còn 5 dòng giả `0x1000`/`0x2000`/... `flash_button_clicked()` thêm guard: nếu `get_checked_datablocks()` rỗng (chưa nạp file nào, hoặc đã nạp nhưng bỏ tick hết), hiện `QMessageBox.warning("No Firmware Loaded", ...)` rồi `return` ngay — không bắt đầu flash, không đụng `prepare_flash_ui()`/thread nào cả. Làm cả 2 vì bổ trợ nhau: guard chặn không cho chạy flash vô nghĩa (chỉ có session/security/reset, không Download step nào), còn bảng trống là hành vi đúng của `add_segments_from_datablocks()` tự thân nếu ai gọi trực tiếp với list rỗng.
- **Vấn đề (trước khi xử lý)**: nếu bấm Flash mà chưa nạp datablock nào, bảng Segments fallback sang 5 dòng demo cứng (địa chỉ `0x1000`, `0x2000`...) không liên quan gì tới lần flash thực tế (lúc đó không có Download step nào cả) — dễ gây hiểu lầm là đang flash dữ liệu thật.

## Tính Năng Đề Xuất Thêm (So Với vFlash)

Mục tiêu sử dụng: thay thế vFlash để flash firmware ECU thật. vFlash (Vector) là công cụ thương mại rất rộng (ODX-F/PDX container, DoIP, multi-project database, licensing...) — **parity 100% là phạm vi quá lớn**, nên các mục dưới đây chỉ là những gap cụ thể được xác nhận cần thiết (2026-08-22, qua trao đổi trực tiếp) cho quy trình flash ECU thật hiện tại, không phải toàn bộ danh sách khác biệt với vFlash. Các ứng viên khác đã cân nhắc nhưng **chưa xác nhận cần** (chưa thêm vào đây): Operator ID / traceability theo người vận hành, theo dõi Programming Counter, đọc + so sánh SW/HW Version trước khi flash để cảnh báo downgrade, auto-detect ECU trên bus, hỗ trợ DoIP — hỏi lại nếu sau này thấy cần.

### 7. Lưu/Nạp Lại Cấu Hình (Profile)

- **Trạng thái**: ✅ Đã xử lý (2026-08-22)
- **Đã làm**: thêm `SettingsProfileMixin` ([gui/settings_profile.py](../gui/settings_profile.py)) — dùng `QSettings(QSettings.IniFormat, QSettings.UserScope, APP_AUTHOR, APP_NAME)` (file `.ini` portable, **không** dùng Registry — xem "Quyết định kỹ thuật" bên dưới). Lưu/nạp: Hardware channel (`comboBoxHardware`), Radar Side (`comboBoxRadarSide`), Flash Sequence (`comboBoxFlashSequence`), Security DLL path (`lineEditSecurityDll`/`self._security_dll_path`). `MainWindow` kế thừa thêm mixin này, gọi `setup_settings_profile()` ngay sau `setup_configure_tab()` (để các combo đã có item thật trước khi nạp lại lựa chọn đã lưu). Save tự động mỗi khi user đổi 1 trong các combo trên (không đợi lúc đóng app — sống sót qua crash/force-quit), cộng thêm gọi `save_profile()` ngay trong `browse_security_dll()`.
- **Vấn đề (trước khi xử lý)**: không có `QSettings` hay file config nào trong codebase — mỗi lần mở app phải chọn lại Hardware/channel, Radar Side, Security DLL path, Flash Sequence từ đầu.

**Quyết định kỹ thuật đáng chú ý**: `QSettings(organization, application)` (constructor 2 tham số) **không** dùng format do `QSettings.setDefaultFormat()`/`setPath()` chỉ định — nó luôn trỏ thẳng tới store native của OS (Registry trên Windows, `NSUserDefaults`/plist trên macOS) bất kể gọi `setDefaultFormat(IniFormat)` trước đó (đã verify thực nghiệm, không phải suy đoán từ docs). Vì vậy code dùng constructor 4 tham số tường minh `QSettings(QSettings.IniFormat, QSettings.UserScope, org, app)` — vừa portable/dễ debug hơn Registry, vừa cho phép test redirect qua `QSettings.setPath()` (xem mục Đã kiểm tra).

- **Vị trí liên quan**: `gui/settings_profile.py` (mới), `gui/main_window.py` (wiring), `gui/configure_tab.py` (`browse_security_dll()`).

### 8. Xuất Báo Cáo Phiên Flash (PDF/HTML)

- **Trạng thái**: ✅ Đã xử lý (2026-08-22) — nút bấm thủ công lúc đầu, sau đó đổi sang **chỉ menu Tools > Export Report...** (bỏ nút trên tab Flash — xem Phase 4.39, user thấy trùng lặp vì đã export được từ menu bar rồi)
- **Đã làm**: thêm `ReportExportMixin` ([gui/report_export.py](../gui/report_export.py)). `Tools → Export Report...` (menu bar, `gui/menu_bar.py`) → `QFileDialog.getSaveFileName()` (tên mặc định `flash_report_YYYYmmdd_HHMMSS.html`) → `_write_report_file()` sinh 1 file HTML tự chứa (không phụ thuộc gì ngoài stdlib), snapshot **toàn bộ trạng thái hiện tại trên màn hình** — không giới hạn phải bấm ngay sau khi flash xong: Summary (Hardware/Radar Side/Flash Sequence/Security DLL/kết quả từ `statsLabel`), Datablocks (đọc trực tiếp `tableWidgetDatablocks`, đánh dấu rõ Included/Excluded theo đúng checkbox — nhất quán với logic lọc ở mục #2), Steps (từ `stepsTable`, giữ nguyên màu nền xanh/đỏ đã tô sẵn), Trace (từ `traceTable`, đúng 6 cột như CSV export), toàn bộ Information Log dạng `<pre>`.
- **Vấn đề (trước khi xử lý)**: chỉ lưu được log thô (Information → `.txt`, Trace → `.csv`) — không có 1 báo cáo tổng hợp trình bày rõ ràng để làm bằng chứng đã flash đúng, như report của vFlash.
- **Quyết định kỹ thuật**: theo đúng pattern `_write_log_file()`/`_write_trace_table_csv()` đã có — tách `_write_report_file()` (pure, chỉ đọc widget + ghi file, test được không cần `QFileDialog` thật) khỏi `export_report()` (wrapper mở dialog). HTML dùng `html.escape()` cho mọi text nội suy vào template (tránh trường hợp filename/ECU string chứa ký tự đặc biệt phá layout HTML). Không hook thêm signal mới ở `FlashWorker` — toàn bộ dữ liệu report tái dùng trực tiếp từ các widget đã có sẵn trên UI, giữ blast radius nhỏ nhất.
- **Vị trí liên quan**: `gui/report_export.py` (mới), `gui/main_window.ui`/`gui/main_window.py` (wiring nút).

### 9. Tổng Kết PASS/FAIL Rõ Ràng Sau Verify Memory

- **Trạng thái**: ⬜ Chưa implement (đề xuất tính năng mới, không phải bug)
- **Vấn đề**: [core/flash_controller.py:576-601](../core/flash_controller.py#L576-L601) (`_execute_routine()`) — bước "Verify Memory" chỉ gọi `RoutineControl` (0x31) và nếu ECU không trả NRC thì coi là xong, emit đúng 1 dòng chung chung `"Routine 0xFF00 completed"` — không có dòng kết luận rõ ràng kiểu "Verify: PASS"/"FAIL" như vFlash luôn hiển thị sau bước verify.
- **Đề xuất**: trong `_execute_routine()`, khi `step.name == "Verify Memory"` (hoặc thêm 1 params flag `is_verify_step`), emit riêng 1 message rõ ràng hơn, vd. `"✓ Verify Memory: PASS"` khi routine trả về không lỗi — không cần thêm UDS service mới, chỉ cần phân biệt case trong message đã emit sẵn qua `information_message`. Verify thất bại (NRC) đã tự động abort qua cơ chế exception có sẵn, chỉ cần đảm bảo message log lúc đó cũng nêu rõ là "Verify Memory FAILED" thay vì message lỗi UDS chung chung.
- **Vị trí liên quan**: `core/flash_controller.py` (`_execute_routine()`).

## Cải Thiện Giao Diện (UI Polish)

Ghi lại từ audit ngày 2026-08-23, sau câu hỏi *"UI của tôi cần cải thiện, thêm animation, thay đổi màu sắc gì cho người dùng không?"*. Hiện app chỉ có vài label dùng `styleSheet` inline rời rạc (header xám `#E0E0E0`, tiêu đề xanh `#2b579a`) — phần còn lại (button, table, tab, progress bar) dùng nguyên theme mặc định Qt Fusion của OS, không có theme nhất quán toàn app, không có phản hồi hover/pressed, không animation nào. Khuyến nghị chung: ưu tiên polish chuyên nghiệp (theme nhất quán, phản hồi tương tác, màu sắc hài hoà theo tông xanh của icon app) hơn animation kiểu app tiêu dùng — đây là công cụ kỹ thuật dùng trong môi trường sản xuất.

### 10. Chưa Có Theme/QSS Nhất Quán Toàn App

- **Trạng thái**: ✅ Đã xử lý (2026-08-23) — `resources/style.qss` + `gui/style.py`, wired vào `main.py`, bundle vào `build.bat`
- **Đã làm**: `gui/style.py::load_stylesheet()` đọc `resources/style.qss` (path resolution dev/frozen giống hệt `gui/menu_bar.py`'s `_GUIDELINE_PATH` — kiểm tra `sys._MEIPASS`), không bao giờ raise (trả `""` nếu file thiếu, app fallback về Qt Fusion mặc định như trước). `main.py` gọi `app.setStyleSheet(load_stylesheet())` ngay sau khi tạo `QApplication`, trước khi tạo `MainWindow()`. `build.bat` thêm `--add-data "resources\style.qss;resources"` để bundle vào `.exe`.
- **Vấn đề (trước khi xử lý)**: button/table/tab/progress bar đều dùng style mặc định của OS (Qt Fusion) — nhìn khác biệt hoàn toàn với vài label có `styleSheet` inline (header xám, tiêu đề xanh `#2b579a`). Không đồng bộ, không có "bản sắc" riêng của app.
- **Quyết định bảng màu — đã duyệt qua preview thật**: đưa ra 4 phương án (Engineering Blue / Diagnostic Amber / Signal Teal / Racing Green), demo trực tiếp trên app thật (không phải mockup) qua 2 artifact: [before/after 1 bảng màu](https://claude.ai/code/artifact/d3abd295-a9cd-4068-b795-c0d0b7854efd) và [so sánh cả 4 bảng màu](https://claude.ai/code/artifact/b1326b02-1806-4a3a-a836-0b3a71e064d3) (đánh dấu ✓ Đã chọn ngay trên artifact). User **chọn Engineering Blue** — trùng tông thương hiệu Vector, cảm giác tin cậy/chuyên nghiệp. Bảng màu cụ thể (dùng nguyên trong `resources/style.qss`):
  - `accent` (chữ/border chính): `#2b579a`
  - `accent-soft` (fill button/progress bar/hover): `#4a7fd6`
  - `accent-bg` (nền hover/header nhạt): `#eef3fa`
  - `bg` (nền tổng thể): `#f4f6f9`
- **Vị trí liên quan**: `resources/style.qss` (mới), `gui/style.py` (mới), `main.py` (wiring), `build.bat` (`--add-data`).

### 11. Không Có Phản Hồi Hover/Pressed Trên Button

- **Trạng thái**: ✅ Đã xử lý (2026-08-23) — bundled vào cùng `resources/style.qss` ở mục #10, không cần file/logic riêng
- **Đã làm**: `QPushButton:hover` (sáng nhẹ + viền đổi màu accent) và `QPushButton:pressed` (tối nhẹ) đã có sẵn trong `resources/style.qss` — không cần `QPropertyAnimation` cho transition mượt, QSS pseudo-state đổi tức thì là đủ rõ ràng.
- **Vấn đề (trước khi xử lý)**: `flashButton`, `buttonRefreshHardware`, `buttonBrowseSecurityDll`, ... đều dùng button mặc định OS — di chuột qua hoặc bấm giữ không có phản hồi thị giác rõ ràng (khác hẳn UI hiện đại, vd. VS Code/web app đều có hover/pressed state).
- **Vị trí liên quan**: `resources/style.qss` (mục #10).

### 12. Icon App Mới Chưa Được Set Làm Window Icon Lúc Chạy

- **Trạng thái**: ✅ Đã xử lý (2026-08-23)
- **Đã làm**: `gui/style.py` thêm `ICON_PATH` (cùng pattern `_MEIPASS`-aware như `STYLE_PATH`, trỏ tới `resources/icons/flash_bolt_blue.ico`). `MainWindow.__init__()` gọi `self.setWindowIcon(QIcon(ICON_PATH))` ngay sau `setupUi()`. `build.bat` thêm `--add-data "resources\icons;resources\icons"`. Test: `tests/test_style.py::TestIconPath` (file `.ico` thực sự tồn tại) + `tests/test_gui_smoke.py::TestMainWindowConstruction::test_window_icon_is_set` (`windowIcon()` không null trên `MainWindow` thật).
- **Vấn đề (trước khi xử lý)**: Phase 4.38 đã làm icon `.ico` cho `.exe` (PyInstaller `--icon`) — nhưng đó chỉ là icon của **file .exe**, không phải icon cửa sổ **lúc app đang chạy** (title bar, taskbar khi mở app). `gui/main_window.py` chưa gọi `self.setWindowIcon(...)`.
- **Vị trí liên quan**: `gui/style.py`, `gui/main_window.py` (`__init__`), `build.bat`.

### 13. Progress Bar Nhảy Cứng Theo Từng Bước, Không Mượt

- **Trạng thái**: ✅ Đã xử lý (2026-08-23)
- **Đã làm**: `setup_flash_tab()` tạo `self._progress_animation = QPropertyAnimation(progressBar, b"value")` (duration 200ms, `QEasingCurve.OutCubic`), lưu làm instance attribute để không bị garbage-collected giữa chừng. `on_progress_changed(value)` không còn `setValue()` trực tiếp — stop animation cũ, set `startValue`=giá trị hiện tại, `endValue`=giá trị mới, rồi `start()`. `prepare_flash_ui()` cũng gọi `.stop()` trước khi reset progress bar về 0 cho lần flash mới, tránh animation cũ "hồi sinh" và ghi đè giá trị reset. Test: `tests/test_gui_smoke.py::test_progress_change_targets_animation_not_instant_jump` (kiểm tra `endValue()` được set đúng, không assert `progressBar.value()` trực tiếp vì animate bất đồng bộ).
- **Vấn đề (trước khi xử lý)**: [gui/flash_tab.py](../gui/flash_tab.py) (`on_progress_changed()`) — `progressBar.setValue(value)` nhảy tức thì theo % mỗi bước hoàn thành (0→8→16→...→100), không có chuyển động mượt giữa các mốc.
- **Vị trí liên quan**: `gui/flash_tab.py` (`setup_flash_tab()`, `on_progress_changed()`, `prepare_flash_ui()`).

### 14. Màu Trạng Thái (Steps/Segments Table) Lệch Tông So Với Brand Color Của App

- **Trạng thái**: ✅ Đã xử lý (2026-08-23)
- **Đã làm**: `config/settings.py` thêm 3 hằng số dùng chung — `STATUS_COLOR_RUNNING = "#FCE9B5"`, `STATUS_COLOR_DONE = "#D3E9D6"`, `STATUS_COLOR_ERROR = "#F3D0D3"` — tông pastel desaturated hài hoà với `accent-bg` (`#eef3fa`)/`border` (`#d7dde5`) của Engineering Blue theme, thay cho bộ màu "Material Design" cũ không liên quan gì tới theme. Toàn bộ `QColor("#FFFACD"/"#C8E6C9"/"#FFCDD2")` trong `gui/flash_tab.py` đã thay bằng 3 hằng số này (giữ nguyên ý nghĩa vàng=đang chạy/xanh=xong/đỏ=lỗi). `gui/report_export.py` không cần sửa gì thêm — nó đọc lại màu qua `.background().color()` nên tự động ăn theo màu mới.
- **Sửa bổ sung (2026-08-23, sau khi user test thực tế dark mode)**: 3 màu pastel trên chỉ set `background()`, không set `foreground()` — ở theme sáng vô hại (chữ mặc định đen), nhưng ở Dark Mode (mục #15) chữ mặc định gần trắng (`#e6e9ee`) trên nền pastel sáng gần như không đọc được (đúng như ảnh chụp thật user gửi). Thêm hằng số `STATUS_TEXT_COLOR = "#1a1a1a"` (cố định, không đổi theo theme — các màu pastel này vốn đã "phá" theme để làm nổi bật trạng thái), gọi `item.setForeground(QColor(STATUS_TEXT_COLOR))` ở mọi chỗ set 1 trong 3 màu trên (`add_step()`, `on_flash_finished()`, `on_flash_aborted()`, `on_segment_progress()`, `update_segments()`). Đồng thời `update_segments()`'s trạng thái "Waiting" trước đó set cứng `QColor(Qt.white)` — cũng vô hại ở theme sáng nhưng cùng lỗi tương tự ở theme tối — đổi thành `QColor(Qt.transparent)` để hàng chưa tới lượt flash tự "ăn theo" nền của bảng theo đúng theme hiện tại, thay vì ép trắng cứng.
- **Sửa bổ sung lần 2 (2026-08-23, phản hồi thẩm mỹ từ user sau khi đọc được rõ chữ)**: dù đọc được, 3 khối màu pastel sáng (vàng/xanh/đỏ) nhìn "loè loẹt" như sticker dán đè lên nền xanh đen của Dark Mode — user xác nhận cần đổi. Thêm bộ màu riêng cho Dark Mode — `STATUS_COLOR_RUNNING_DARK = "#4a3d1f"`, `STATUS_COLOR_DONE_DARK = "#1f3a2c"`, `STATUS_COLOR_ERROR_DARK = "#3d2226"` (nền tối có tint màu, cùng tông độ sáng với nền app `#1e2228`/`#262b33`) + `STATUS_TEXT_COLOR_DARK = "#f0f3f7"` (chữ sáng) — theo đúng convention các dev tool nền tối (VS Code, GitHub dark) dùng cho diff/status highlight, thay vì đảo hẳn sang khối màu sáng đặc. `gui/flash_tab.py` thêm helper `_status_colors(kind)` chọn đúng cặp (nền, chữ) dựa trên `self._dark_mode_active` — theo dõi **live** theo theme hiện tại (được `gui/menu_bar.py`'s `action_toggle_dark_mode()` cập nhật mỗi lần bấm toggle), không phải chỉ đọc 1 lần lúc khởi động. Theme sáng giữ nguyên bộ màu pastel cũ, không đổi gì.
- **Vấn đề (trước khi xử lý)**: `gui/flash_tab.py` tô màu row bằng hex cứng — vàng nhạt `#FFFACD` (đang chạy), xanh lá `#C8E6C9` (xong), đỏ nhạt `#FFCDD2` (lỗi/abort) — các màu pastel này không liên quan gì tới tông xanh dương của icon app/tiêu đề section (`#2b579a`), nhìn hơi lệch "bộ nhận diện".
- **Vị trí liên quan**: `config/settings.py`, `gui/flash_tab.py`, `gui/menu_bar.py`.

### 15. Chưa Có Dark Mode

- **Trạng thái**: ✅ Đã xử lý (2026-08-23)
- **Đã làm**: `resources/style_dark.qss` — bản dark mirror 1:1 mọi selector của `style.qss` (cùng tông xanh dương Engineering Blue, sáng hơn để đủ tương phản trên nền tối: accent `#5b8fd9`, `flashButton` `#3f6cb0`, nền `#1e2228`/`#262b33`). `gui/style.py`: `load_stylesheet(dark=False)` nhận thêm tham số, `is_dark_mode_enabled()` đọc trực tiếp qua `QSettings` (cùng org/app/IniFormat như `gui/settings_profile.py`) — độc lập với `MainWindow` vì `main.py` cần biết theme **trước khi** tạo cửa sổ đầu tiên (tránh nháy sáng-rồi-tối). `main.py` gọi `app.setStyleSheet(load_stylesheet(dark=is_dark_mode_enabled()))`. Thêm menu **View > Dark Mode** (checkable `actionDarkMode` trong `gui/main_window.ui`) — `gui/menu_bar.py`'s `action_toggle_dark_mode()` áp dụng lại stylesheet lên `QApplication.instance()` và lưu vào `self._settings` (cùng file profile với Hardware/Radar Side ở mục #7) mỗi lần bấm. `build.bat` thêm `--add-data` cho `style_dark.qss`.
  - **Bug phát hiện khi làm dark mode**: `informationText` (`QTextEdit`) chưa từng có selector `QTextEdit` nào trong `.qss` — vô hại ở theme sáng (chữ đen mặc định trên nền trắng mặc định) nhưng chữ gần như vô hình ở theme tối (chữ `#e6e9ee` gần trắng trên nền `QTextEdit` mặc định vẫn là trắng). Đã thêm `QTextEdit {...}` vào cả 2 file `.qss`.
  - **Bug thứ 2**: 8 label tiêu đề section (`labelDatablocks`, `labelDetails`, `labelHardware`, `labelRadarSide`, `labelLogicalLink`, `labelFlashSequence`, `labelSecurityDll`, `labelCustomConfig`) hardcode `styleSheet` inline `background-color: #E0E0E0` ngay trong `.ui` — ở theme tối thành 1 thanh xám sáng lạc quẻ, chữ gần như không đọc được. Đã gỡ `background-color` khỏi inline style, gắn dynamic property `sectionHeader=true` cho cả 8 widget, và thêm selector `QLabel[sectionHeader="true"]` vào cả 2 file `.qss` (giữ nguyên `#E0E0E0` ở theme sáng, đổi màu phù hợp ở theme tối).
  - **Bug thứ 3 (2026-08-23, phát hiện qua ảnh chụp thật user gửi)**: cột header đánh số dòng (vertical header) của mọi `QTableWidget` — bảng thấp/ít dòng thì phần header bên dưới ô số cuối cùng (và ô góc trên-trái, `QTableCornerButton`) không hề được `QSS` phủ tới, lộ ra 1 cột trắng dài xấu ở theme tối. Nguyên nhân: `QHeaderView::section` chỉ style **từng ô số** đã có, không style bản thân widget `QHeaderView` (phần trống bên dưới section cuối) hay `QTableCornerButton`. Đã thêm rule `QHeaderView {...}` (base, không phải `::section`) và `QTableCornerButton::section {...}` vào cả 2 file `.qss`, cùng màu với `QHeaderView::section` để cả cột đồng nhất từ trên xuống dưới.
  - **Đổi mặc định thành Dark Mode, rồi đổi lại thành Light Mode (2026-08-23)**: theo yêu cầu user, `is_dark_mode_enabled()`'s default trong `QSettings.value("appearance/darkMode", ...)` đổi từ `False` → `True` (Phase 4.44). Sau khi xem thử màu trạng thái trên cả 2 theme, user quyết định **Light Mode mới là mặc định phù hợp** — đổi lại `True` → `False` (Phase 4.47), tức quay về hành vi gốc trước Phase 4.44. Chỉ ảnh hưởng lần chạy đầu tiên/cài mới (chưa từng lưu lựa chọn nào); một khi user đã bấm toggle 1 lần (dù bật hay tắt), giá trị đã lưu luôn được ưu tiên đọc lại, không bị ghi đè về mặc định nữa.
  - **Bug thứ 4 (2026-08-23, phát hiện qua ảnh chụp thật user gửi — bảng Trace)**: `traceTable` có `alternatingRowColors=True` (`main_window.ui`) — nhưng cả 2 file `.qss` chưa từng khai báo `alternate-background-color`, nên các dòng "xen kẽ" (mọi dòng thứ 2) rơi về màu `AlternateBase` mặc định của palette OS (1 màu xám nhạt cố định, không đổi theo theme app). Ở theme tối, chữ mặc định gần trắng trên nền xám nhạt đó gần như không đọc được — đúng như ảnh user gửi (dòng SYSTEM đọc được, dòng TX/RX xen kẽ giữa mờ hẳn). Đã thêm `alternate-background-color` tường minh vào rule `QTableWidget` của cả 2 file `.qss` (`#f7f9fb` theme sáng, `#20242b` theme tối) thay vì phụ thuộc màu mặc định không kiểm soát được của OS.
- **Vấn đề (trước khi xử lý)**: chỉ có 1 theme sáng cố định — nhiều công cụ kỹ thuật được dùng trong môi trường xưởng/sản xuất ánh sáng yếu, dark mode giảm chói mắt khi dùng lâu.
- **Vị trí liên quan**: `resources/style_dark.qss`, `resources/style.qss`, `gui/style.py`, `gui/main_window.ui`, `gui/menu_bar.py`, `main.py`, `build.bat`.

### 16. Bảng Steps/Segments/Trace Trống Trơn Lúc Chưa Chạy Flash — Thiếu Placeholder

- **Trạng thái**: ✅ Đã xử lý (2026-08-23)
- **Đã làm**: `gui/flash_tab.py` thêm helper `_set_table_placeholder(table, text)` (mirror `gui/configure_tab.py`'s `_add_placeholder_row()` — 1 row span hết cột, canh giữa, màu xám, không editable, chỉ set item ở cột 0). `setup_flash_tab()` gọi helper này cho cả `stepsTable` và `segmentsTable` ngay khi khởi tạo. `add_step()` tự xoá placeholder (`_steps_placeholder_active` flag) ngay khi có step thật đầu tiên. `add_segments_from_datablocks()` được viết lại để tự `setRowCount(0)` ở đầu hàm (không còn phụ thuộc caller đã clear sẵn) rồi tự thêm lại placeholder nếu danh sách rỗng — nhờ vậy hàm này gọi trực tiếp (như trong test) hay qua `prepare_flash_ui()` đều cho kết quả nhất quán. `gui/report_export.py`'s `_report_steps_table()` skip row placeholder (dựa vào cột Description = `None`, cùng pattern `_report_datablocks_table()` đã dùng để skip placeholder của `tableWidgetDatablocks`) — báo cáo xuất ra không bao giờ lẫn dòng "No steps recorded yet." như dữ liệu thật. Trace table giữ nguyên để trống (đúng đề xuất ban đầu — ít quan trọng hơn).
  - Cập nhật 2 test cũ ở `TestEmptyDatablocksGuard` (trước đó assert `segmentsTable.rowCount() == 0` khi rỗng — giờ đúng ra phải là 1 dòng placeholder, không phải rỗng tuyệt đối).
- **Vấn đề (trước khi xử lý)**: mở app lần đầu, 3 bảng này chỉ là khoảng trắng trống trơn — không có gợi ý gì cho user mới dùng lần đầu, khác hẳn bảng Datablocks đã có sẵn placeholder "Please click here to add a Datablock" (UX tốt hơn hẳn).
- **Vị trí liên quan**: `gui/flash_tab.py` (`setup_flash_tab()`, `add_step()`, `add_segments_from_datablocks()`, `prepare_flash_ui()`), `gui/report_export.py` (`_report_steps_table()`).

## Mở Rộng Menu Bar

Menu bar ban đầu (Phase 4.33) chỉ có File/Tools/Help với vài action cơ bản. User nhận xét menu bar còn ít chức năng và yêu cầu brainstorm — 2 mục dưới đây được chọn implement ngay (bounded, không qua spec riêng).

### 17. File > Recent Files

- **Trạng thái**: ✅ Đã xử lý (2026-08-23)
- **Đã làm**: Submenu **File > Recent Files** — lưu tối đa `MAX_RECENT_FILES = 8` đường dẫn file firmware gần nhất qua `QSettings` (cùng `self._settings` với Hardware/Radar Side/Dark Mode). Mỗi lần load file thành công (dù qua "Load Firmware..." hay click 1 mục trong Recent Files) đều tự ghi vào đầu danh sách — file trùng thì dời lên đầu thay vì lặp lại (`_record_recent_file()`). Click 1 mục = nạp lại ngay (`load_recent_file()`), không cần mở lại dialog; file bị xoá/di chuyển thì hiện đúng dialog "Parse Error" đã có sẵn (dùng chung `_parse_firmware_file()`, không code thêm gì cho trường hợp lỗi). Cuối submenu có "Clear Recent Files"; danh sách rỗng thì hiện "(No Recent Files)" mờ, không bấm được — cùng kiểu placeholder với các bảng khác trong app.
  - Refactor: tách phần "parse 1 file + thêm 1 row vào `tableWidgetDatablocks`" trong `add_new_datablock()` (`gui/configure_tab.py`) ra hàm riêng `_load_firmware_file(file_path)` — dùng chung cho cả 2 luồng (chọn file qua dialog, và Recent Files), đảm bảo hành vi/lỗi giống hệt nhau.
  - Đổi thứ tự gọi trong `MainWindow.__init__()`: `setup_settings_profile()` giờ chạy **trước** `setup_menu_bar()` (trước đó ngược lại) — vì `setup_menu_bar()` cần `self._settings` đã tồn tại để dựng submenu Recent Files từ dữ liệu đã lưu ngay lúc khởi động.
- **Vị trí liên quan**: `gui/main_window.ui` (submenu `menuRecentFiles` + action `actionClearRecentFiles`), `gui/menu_bar.py`, `gui/configure_tab.py`, `gui/main_window.py` (thứ tự init).

### 18. Menu Edit — Clear Information Log / Clear Trace Table

- **Trạng thái**: ✅ Đã xử lý (2026-08-23)
- **Đã làm**: Thêm menu **Edit** (giữa File và View) với 2 action: "Clear Information Log" (`informationText.clear()`) và "Clear Trace Table" (`traceTable.setRowCount(0)`) — xoá ngay không hỏi xác nhận (log không phải dữ liệu cần giữ bắt buộc, ai cần lưu thì đã có "Save Log..." qua right-click từ trước).
- **Vấn đề (trước khi xử lý)**: chỉ có "Save Log..." qua right-click trên từng bảng log, chưa có cách xoá log đang xem mà không phải đóng/mở lại app.
- **Vị trí liên quan**: `gui/main_window.ui` (menu `menuEdit` + 2 action), `gui/menu_bar.py`.

## Ghi chú

- Audit thực hiện bằng cách liệt kê toàn bộ `name="..."` trong `gui/main_window.ui`, đối chiếu số lần xuất hiện trong `gui/*.py`/`main.py` qua `self.ui.<name>`/`self.<name>`. Các widget layout/label/container có 0 usage là bình thường (không cần wiring). Danh sách trên chỉ gồm các widget **tương tác được** (combo/checkbox/table có thể sửa) mà không có logic backend thật.
- Không phải bug crash — app vẫn chạy ổn định, đây là các điểm UI "trông như hoạt động nhưng thực chất không" hoặc "chưa có tính năng thật đứng sau", cần quyết định implement thật hay dọn bỏ.
- Mục #7-9 là tính năng mới (gap so với vFlash), không phải bug — khác bản chất với mục #1-6 (component đã tồn tại nhưng chết/giả).
- Mục #10-16 là cải thiện UI/UX (thẩm mỹ, phản hồi tương tác) — không phải bug, không phải gap tính năng so với vFlash, mà là polish chung cho trải nghiệm người dùng. Nên làm theo thứ tự #10 trước (đòn bẩy lớn nhất, các mục #11/#14 phụ thuộc vào file `.qss` chung được tạo ở đây).
