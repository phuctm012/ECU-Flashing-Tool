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

- **Trạng thái**: ⬜ Chưa implement — **đã chốt bảng màu (2026-08-23)**, chỉ còn chờ implement thật
- **Vấn đề**: button/table/tab/progress bar đều dùng style mặc định của OS (Qt Fusion) — nhìn khác biệt hoàn toàn với vài label có `styleSheet` inline (header xám, tiêu đề xanh `#2b579a`). Không đồng bộ, không có "bản sắc" riêng của app.
- **Đề xuất**: viết 1 file `.qss` (Qt Style Sheet, cú pháp gần giống CSS) áp dụng toàn app qua `app.setStyleSheet(...)` trong `main.py`, dùng tông màu xanh của icon app (xem `resources/icons/flash_bolt_blue.svg`) làm accent color nhất quán cho button/tab active/progress bar/scrollbar. Đây là đòn bẩy lớn nhất — 1 file `.qss` ảnh hưởng toàn bộ giao diện, không cần sửa từng widget riêng lẻ.
- **Quyết định bảng màu — đã duyệt qua preview thật**: đưa ra 4 phương án (Engineering Blue / Diagnostic Amber / Signal Teal / Racing Green), demo trực tiếp trên app thật (không phải mockup) qua 2 artifact: [before/after 1 bảng màu](https://claude.ai/code/artifact/d3abd295-a9cd-4068-b795-c0d0b7854efd) và [so sánh cả 4 bảng màu](https://claude.ai/code/artifact/b1326b02-1806-4a3a-a836-0b3a71e064d3) (đánh dấu ✓ Đã chọn ngay trên artifact). User **chọn Engineering Blue** — trùng tông thương hiệu Vector, cảm giác tin cậy/chuyên nghiệp. Bảng màu cụ thể:
  - `accent` (chữ/border chính): `#2b579a`
  - `accent-soft` (fill button/progress bar/hover): `#4a7fd6`
  - `accent-bg` (nền hover/header nhạt): `#eef3fa`
  - `bg` (nền tổng thể): `#f4f6f9`
  - File QSS thử nghiệm đã viết sẵn ở phiên trước (scratchpad, chưa vào repo) — có thể dùng làm điểm khởi đầu khi implement thật, không cần thiết kế lại từ đầu.
- **Vị trí liên quan**: file mới `resources/style.qss` (hoặc tương tự), `main.py` (load + `app.setStyleSheet()`).

### 11. Không Có Phản Hồi Hover/Pressed Trên Button

- **Trạng thái**: ⬜ Chưa implement (đề xuất tính năng mới, không phải bug)
- **Vấn đề**: `flashButton`, `buttonRefreshHardware`, `buttonBrowseSecurityDll`, ... đều dùng button mặc định OS — di chuột qua hoặc bấm giữ không có phản hồi thị giác rõ ràng (khác hẳn UI hiện đại, vd. VS Code/web app đều có hover/pressed state).
- **Đề xuất**: thêm rule `QPushButton:hover`/`QPushButton:pressed` vào file `.qss` chung (mục #10) — sáng nhẹ lúc hover, tối nhẹ lúc pressed, có `transition` mượt nếu Qt style engine hỗ trợ (Qt Style Sheet hỗ trợ 1 phần transition qua `QPropertyAnimation`, không phải CSS transition thật).
- **Vị trí liên quan**: cùng file `.qss` ở mục #10.

### 12. Icon App Mới Chưa Được Set Làm Window Icon Lúc Chạy

- **Trạng thái**: ⬜ Chưa implement (đề xuất tính năng mới, không phải bug)
- **Vấn đề**: Phase 4.38 đã làm icon `.ico` cho `.exe` (PyInstaller `--icon`) — nhưng đó chỉ là icon của **file .exe**, không phải icon cửa sổ **lúc app đang chạy** (title bar, taskbar khi mở app). `gui/main_window.py` chưa gọi `self.setWindowIcon(...)`.
- **Đề xuất**: thêm `self.setWindowIcon(QIcon(icon_path))` trong `MainWindow.__init__()`, dùng `resources/icons/flash_bolt_blue.ico` (hoặc render `flash_bolt_blue.svg` ra `QIcon` trực tiếp). Cần path resolution giống hệt `gui/menu_bar.py`'s `_GUIDELINE_PATH` (kiểm tra `sys._MEIPASS` cho bản `.exe` đã đóng gói) — và cần thêm `resources/icons` vào `--add-data` của `build.bat` nếu load icon lúc runtime từ file thay vì nhúng sẵn.
- **Vị trí liên quan**: `gui/main_window.py` (`__init__`), `build.bat` (`--add-data` nếu cần).

### 13. Progress Bar Nhảy Cứng Theo Từng Bước, Không Mượt

- **Trạng thái**: ⬜ Chưa implement (đề xuất tính năng mới, không phải bug)
- **Vấn đề**: [gui/flash_tab.py](../gui/flash_tab.py) (`on_progress_changed()`) — `progressBar.setValue(value)` nhảy tức thì theo % mỗi bước hoàn thành (0→8→16→...→100), không có chuyển động mượt giữa các mốc.
- **Đề xuất**: dùng `QPropertyAnimation` trên property `value` của `progressBar` (animate từ giá trị cũ sang giá trị mới trong ~150-300ms thay vì set thẳng) — hiệu ứng nhỏ nhưng cảm giác "mượt" hơn hẳn, phù hợp mức độ animation vừa phải cho app kỹ thuật (không quá đà).
- **Vị trí liên quan**: `gui/flash_tab.py` (`on_progress_changed()`).

### 14. Màu Trạng Thái (Steps/Segments Table) Lệch Tông So Với Brand Color Của App

- **Trạng thái**: ⬜ Chưa implement (đề xuất tính năng mới, không phải bug)
- **Vấn đề**: `gui/flash_tab.py` tô màu row bằng hex cứng — vàng nhạt `#FFFACD` (đang chạy), xanh lá `#C8E6C9` (xong), đỏ nhạt `#FFCDD2` (lỗi/abort) — các màu pastel này không liên quan gì tới tông xanh dương của icon app/tiêu đề section (`#2b579a`), nhìn hơi lệch "bộ nhận diện".
- **Đề xuất**: thống nhất lại bảng màu trạng thái — có thể giữ nguyên ý nghĩa (vàng=đang chạy, xanh lá=xong, đỏ=lỗi) nhưng chỉnh sắc độ hài hoà hơn với tông xanh dương chủ đạo (vd. dùng biến màu chung định nghĩa 1 chỗ, tái dùng cho cả `.qss` lẫn các nơi tô màu bằng code Python như `flash_tab.py`/`report_export.py`'s HTML report).
- **Vị trí liên quan**: `gui/flash_tab.py` (nhiều chỗ dùng `QColor("#...")`), `gui/report_export.py` (đọc lại màu từ `background().color()` để đưa vào HTML report).

### 15. Chưa Có Dark Mode

- **Trạng thái**: ⬜ Chưa implement (đề xuất tính năng mới, không phải bug)
- **Vấn đề**: chỉ có 1 theme sáng cố định — nhiều công cụ kỹ thuật được dùng trong môi trường xưởng/sản xuất ánh sáng yếu, dark mode giảm chói mắt khi dùng lâu.
- **Đề xuất**: làm sau khi có `.qss` theme chung (mục #10) — thêm 1 bộ `.qss` thứ 2 cho dark mode + toggle (vd. menu Help hoặc 1 action mới), hoặc đơn giản hơn là theo theme OS (`QGuiApplication` có API dò dark/light mode của OS). Ưu tiên thấp hơn mục #10-13, chỉ nên làm nếu người dùng thực sự cần.
- **Vị trí liên quan**: file `.qss` mới (dark variant), `gui/menu_bar.py` (nếu thêm toggle).

### 16. Bảng Steps/Segments/Trace Trống Trơn Lúc Chưa Chạy Flash — Thiếu Placeholder

- **Trạng thái**: ⬜ Chưa implement (đề xuất tính năng mới, không phải bug)
- **Vấn đề**: mở app lần đầu, 3 bảng này chỉ là khoảng trắng trống trơn — không có gợi ý gì cho user mới dùng lần đầu, khác hẳn bảng Datablocks đã có sẵn placeholder "Please click here to add a Datablock" (UX tốt hơn hẳn).
- **Đề xuất**: áp dụng cùng pattern placeholder đã có ở Datablocks cho Steps/Segments (vd. "Click Flash to start" / "No steps yet") — Trace table có thể để trống bình thường (ít quan trọng hơn vì không phải nơi user tương tác trực tiếp).
- **Vị trí liên quan**: `gui/flash_tab.py` (`setup_flash_tab()`, tương tự `_add_placeholder_row()` đã có trong `gui/configure_tab.py`).

## Ghi chú

- Audit thực hiện bằng cách liệt kê toàn bộ `name="..."` trong `gui/main_window.ui`, đối chiếu số lần xuất hiện trong `gui/*.py`/`main.py` qua `self.ui.<name>`/`self.<name>`. Các widget layout/label/container có 0 usage là bình thường (không cần wiring). Danh sách trên chỉ gồm các widget **tương tác được** (combo/checkbox/table có thể sửa) mà không có logic backend thật.
- Không phải bug crash — app vẫn chạy ổn định, đây là các điểm UI "trông như hoạt động nhưng thực chất không" hoặc "chưa có tính năng thật đứng sau", cần quyết định implement thật hay dọn bỏ.
- Mục #7-9 là tính năng mới (gap so với vFlash), không phải bug — khác bản chất với mục #1-6 (component đã tồn tại nhưng chết/giả).
- Mục #10-16 là cải thiện UI/UX (thẩm mỹ, phản hồi tương tác) — không phải bug, không phải gap tính năng so với vFlash, mà là polish chung cho trải nghiệm người dùng. Nên làm theo thứ tự #10 trước (đòn bẩy lớn nhất, các mục #11/#14 phụ thuộc vào file `.qss` chung được tạo ở đây).
