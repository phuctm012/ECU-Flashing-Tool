# GUI TODO — Component Chưa Hoạt Động / Chưa Implement

Ghi lại từ audit ngày 2026-08-22: đối chiếu từng widget có tên trong `gui/main_window.ui`
với usage thật trong `gui/*.py` để tìm các component không có tác dụng, hiển thị dữ
liệu giả, hoặc chưa được implement logic đứng sau. Cập nhật trạng thái ở đây khi xử lý.

## Danh sách

### 1. Combo "Checksum Method" — hoàn toàn chết (dead)

- **Trạng thái**: ⬜ Chưa xử lý
- **Vị trí**: [gui/main_window.ui:250](../gui/main_window.ui#L250) — `comboBoxChecksum` (tab Data → "Additional Setup")
- **Vấn đề**: 2 lựa chọn "Pre-Calculation: via file selection" / "Calculate during flash", nhưng không có dòng code Python nào đọc giá trị của nó. Checksum thực tế luôn tính CRC32 lúc parse file, bất kể chọn gì ở combo này.
- **Liên quan**: `CHECKSUM_METHODS` trong `config/settings.py:90` — hằng số định nghĩa sẵn nhưng không nơi nào import.
- **Hướng xử lý**: (a) xóa hẳn control này khỏi `.ui`, hoặc (b) implement thật — đọc giá trị combo, áp dụng logic checksum tương ứng trong `parsers/`.

### 2. Checkbox cột đầu bảng Datablocks — không có tác dụng

- **Trạng thái**: ⬜ Chưa xử lý
- **Vị trí**: [gui/configure_tab.py:179](../gui/configure_tab.py#L179)
- **Vấn đề**: mỗi file firmware nạp vào có 1 checkbox (mặc định tick), trông như để bật/tắt "có flash file này hay không". Trạng thái checkbox không bao giờ được đọc lại — bỏ tick 1 dòng không loại trừ nó khỏi flash sequence; mọi datablock đã nạp luôn bị flash hết.
- **Hướng xử lý**: (a) xóa checkbox (chỉ dùng làm hiển thị "đã nạp"), hoặc (b) đọc `checkState()` trong `add_new_datablock()`/`flash_button_clicked()` để lọc datablock trước khi build flash sequence.

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

- **Trạng thái**: ⬜ Chưa xử lý
- **Vị trí**: [gui/flash_tab.py:587-601](../gui/flash_tab.py#L587-L601) (`add_segments_from_datablocks()`)
- **Vấn đề**: nếu bấm Flash mà chưa nạp datablock nào, bảng Segments fallback sang 5 dòng demo cứng (địa chỉ `0x1000`, `0x2000`...) không liên quan gì tới lần flash thực tế (lúc đó không có Download step nào cả) — dễ gây hiểu lầm là đang flash dữ liệu thật.
- **Hướng xử lý**: bỏ fallback demo — nếu không có datablock nào, để bảng Segments trống (hoặc chặn hẳn không cho bấm Flash khi chưa nạp file gì).

## Ghi chú

- Audit thực hiện bằng cách liệt kê toàn bộ `name="..."` trong `gui/main_window.ui`, đối chiếu số lần xuất hiện trong `gui/*.py`/`main.py` qua `self.ui.<name>`/`self.<name>`. Các widget layout/label/container có 0 usage là bình thường (không cần wiring). Danh sách trên chỉ gồm các widget **tương tác được** (combo/checkbox/table có thể sửa) mà không có logic backend thật.
- Không phải bug crash — app vẫn chạy ổn định, đây là các điểm UI "trông như hoạt động nhưng thực chất không" hoặc "chưa có tính năng thật đứng sau", cần quyết định implement thật hay dọn bỏ.
