# SFlash CLI — Hướng dẫn sử dụng `SFlash_CLI.exe`

Công cụ nạp firmware ECU qua CAN (UDS / ISO 14229) chạy bằng dòng lệnh, không cần mở giao diện. Tài liệu này dành cho người dùng bản `.exe` đã build sẵn — **không cần cài Python, không cần source code**.

> English version: `README_CLI_EN.md`.

> Nếu máy bạn đã có Python và source của project, dùng `python cli.py ...` thay cho `SFlash_CLI.exe ...`; mọi câu lệnh dưới đây giống hệt nhau. Xem `README.md` của project.

---

## 1. Chuẩn bị

Giải nén / copy **nguyên cả thư mục** `SFlash_CLI\` — không copy mỗi file `.exe`, vì nó cần các file hỗ trợ nằm cùng thư mục.

Kiểm tra chạy được:

```powershell
cd C:\tools\SFlash_CLI
.\SFlash_CLI.exe --version
```

Ra `SFlash 3.1` là xong phần cài đặt.

### Cần thêm gì cho phần cứng thật

| Thứ cần | Khi nào cần | Ghi chú |
|---|---|---|
| **Vector XL Driver Library** | Khi dùng `--hardware vector` | Tải từ trang Vector Informatik. Chỉ có bản Windows. |
| **Security Access DLL** | Khi ECU yêu cầu thuật toán seed/key riêng của OEM | Là file **bên ngoài**, chọn lúc chạy — **không** nằm trong `.exe`. Phải cùng kiến trúc 64-bit với `SFlash_CLI.exe`. |

Không có hai thứ trên thì vẫn chạy được toàn bộ với **Virtual ECU Simulator** (`--hardware virtual`, mặc định) để tập làm quen hoặc kiểm tra script.

---

## 2. Quy trình chuẩn — làm theo thứ tự này

Ba bước đầu đều **an toàn tuyệt đối**, không ghi gì vào ECU.

```powershell
# 1. ECU ảo — tập dượt, không cần phần cứng
.\SFlash_CLI.exe flash firmware.s19 --dry-run

# 2. Máy có nhận đúng phần cứng Vector không
.\SFlash_CLI.exe list-hardware

# 3. DLL bảo mật có dùng được không (không đụng ECU)
.\SFlash_CLI.exe check-security-dll C:\tools\SeedKey64.dll

# 4. ECU có trả lời không — chỉ đọc, không Erase/Download/ghi
.\SFlash_CLI.exe test-connection --hardware vector --channel 0 --serial 123456 ^
    --sequence suzuki --timeout 120

# 5. Chỉ khi bước 4 PASSED mới flash thật
.\SFlash_CLI.exe flash firmware.s19 --hardware vector --channel 0 --serial 123456 ^
    --sequence suzuki --security-dll C:\tools\SeedKey64.dll ^
    --timeout 900 --report report.html --json-summary result.json
```

> Trong PowerShell/CMD, dấu `^` ở cuối dòng nghĩa là "lệnh còn tiếp dòng sau". Muốn viết một dòng thì bỏ hết `^` đi.

---

## 3. Các lệnh

### `list-hardware` — xem máy nhận được gì

```powershell
.\SFlash_CLI.exe list-hardware
```

In ra các channel Vector đang cắm kèm **đúng tham số cần dùng**, ví dụ `--channel 1 --serial 123456`, và bảng CAN ID theo Radar Side.

Nếu báo không thấy phần cứng: chưa cắm thiết bị, hoặc chưa cài Vector XL Driver.

### `info` — xem file firmware

```powershell
.\SFlash_CLI.exe info firmware.s19
.\SFlash_CLI.exe info app.s19 calib.s19        # nhiều file
.\SFlash_CLI.exe info image.bin --base-address 0x8000
```

In số segment, địa chỉ, kích thước, checksum. Không đụng ECU.

### `check-security-dll` — chẩn đoán DLL bảo mật

```powershell
.\SFlash_CLI.exe check-security-dll C:\tools\SeedKey64.dll
```

Cho biết: DLL là 32 hay 64-bit, SFlash là 32 hay 64-bit, DLL export hàm gì, phụ thuộc DLL nào, và thử load thật. Exit `0` = dùng được.

**Chạy lệnh này trước khi flash lần đầu với một DLL mới** — nó trả lời dứt điểm câu hỏi "DLL có dùng được không" trong 1 giây, thay vì phát hiện lúc đang flash dở.

### `test-connection` — kiểm tra kết nối an toàn

```powershell
.\SFlash_CLI.exe test-connection --hardware vector --channel 0 --serial 123456 ^
    --sequence suzuki --timeout 120 --verbose
```

Chỉ mở Extended Session và đọc vài DID nhận dạng ECU. **Không bao giờ** đụng tới Programming Session, Security Access, Erase Memory hay bất kỳ lệnh ghi nào. Luôn cố khôi phục ECU về Default Session khi kết thúc, kể cả khi lỗi giữa chừng.

Chạy lệnh này trước mỗi lần flash thật trên bench mới.

### `flash` — nạp firmware

```powershell
# Một file
.\SFlash_CLI.exe flash firmware.s19 --hardware vector --channel 0 --serial 123456 ^
    --sequence suzuki --security-dll C:\tools\SeedKey64.dll --timeout 900

# Nhiều file (nhiều datablock, nạp theo đúng thứ tự liệt kê)
.\SFlash_CLI.exe flash app.s19 calib.s19 --hardware vector --channel 0 --sequence suzuki

# Xem trước các bước, không gửi gì tới ECU
.\SFlash_CLI.exe flash firmware.s19 --sequence suzuki --dry-run
```

### `batch` — nạp nhiều ECU lần lượt

Mỗi ECU được nhận dạng (Identify) trước để lấy Serial Number vào báo cáo, rồi mới nạp. Một ECU lỗi **không** làm dừng cả loạt.

```powershell
# 5 ECU cùng cấu hình, chờ nhấn Enter giữa các lần để đổi ECU
.\SFlash_CLI.exe batch firmware.s19 --hardware vector --channel 0 ^
    --count 5 --pause --report batch.html

# Mỗi ECU một cấu hình riêng
.\SFlash_CLI.exe batch firmware.s19 --hardware vector ^
    --unit "name=Left,channel=0,side=s0" ^
    --unit "name=Right,channel=1,side=s1"
```

Tuỳ chọn riêng: `--count N`, `--pause` (chờ Enter giữa các ECU — **mặc định tắt**), `--no-identify`, `--stop-on-fail`.

### `parallel` — nạp nhiều ECU cùng lúc

Mỗi ECU một channel CAN riêng, chạy song song.

```powershell
.\SFlash_CLI.exe parallel firmware.s19 --hardware vector ^
    --unit "name=Left,channel=0,serial=123456,side=s0" ^
    --unit "name=Right,channel=1,serial=123456,side=s1" ^
    --timeout 900 --report parallel.html
```

Hai unit trỏ vào **cùng một ECU** (trùng channel + trùng Tx ID) sẽ bị từ chối với exit `2` — hai phiên UDS song song lên một ECU sẽ phá nhau và trông y như lỗi phần cứng.

Nhấn **Ctrl+C** = Abort All: mọi channel đang chạy được yêu cầu dừng rồi mới thoát.

### Khai báo nhiều ECU bằng file (`--units-file`)

Dùng chung cho `batch` và `parallel`. Tiện khi cấu hình line cố định.

```json
{
  "comment": "Line 1 - 2 radar moi xe",
  "units": [
    { "name": "Left",  "hardware": "vector", "channel": 0, "serial": 123456, "side": "s0" },
    { "name": "Right", "hardware": "vector", "channel": 1, "serial": 123456, "side": "s1" }
  ]
}
```

```powershell
.\SFlash_CLI.exe parallel firmware.s19 --units-file line1.json --timeout 900
```

Các trường: `name`, `hardware` (`virtual`/`vector`), `channel`, `serial`, `side` (`s0`/`s1`), `tx_id`, `rx_id`, `functional_id`. **Trường nào không khai thì lấy theo tham số chung của lệnh.** Gõ sai tên trường sẽ báo lỗi rõ ràng chứ không bị bỏ qua âm thầm.

### `gitlab` — tải firmware từ GitLab

Cần **access token**. Nên đặt qua biến môi trường để token không lọt vào lịch sử lệnh:

```powershell
$env:SFLASH_GITLAB_TOKEN = "glpat-xxxxxxxx"

.\SFlash_CLI.exe gitlab refs
.\SFlash_CLI.exe gitlab jobs --ref main --success-only
.\SFlash_CLI.exe gitlab artifact --ref main --job build_fw -o downloads --extract
.\SFlash_CLI.exe gitlab packages --package-name radar_fw
.\SFlash_CLI.exe gitlab package --package-name radar_fw --version 1.2.3 -o downloads
```

Nối tải-rồi-nạp trong một mạch (PowerShell):

```powershell
$FW = .\SFlash_CLI.exe gitlab artifact --ref main --job build_fw -o downloads --print-firmware
.\SFlash_CLI.exe flash $FW --hardware vector --channel 0 --sequence suzuki --timeout 900
```

---

## 4. Cấu hình toàn bộ bằng một file JSON (`--config`)

Thay vì gõ cả chục tham số mỗi lần, đặt **tất cả** vào một file JSON và đưa vào version control.

```json
{
  "comment": "Line 1 - radar trai - bench 2",

  "file": ["C:\\builds\\firmware.s19"],

  "hardware": "vector",
  "channel": 0,
  "serial": 123456,
  "sequence": "suzuki",
  "radar-side": "s0",
  "bitrate": 500000,

  "security-dll": "C:\\tools\\SeedKey64.dll",
  "timeout": 900,

  "report": "report.html",
  "json-summary": "result.json",
  "trace-csv": "trace.csv",
  "quiet": true
}
```

```powershell
.\SFlash_CLI.exe flash --config line1_left.json
```

**Quy tắc:**

- **Key chính là tên tham số dài, bỏ dấu gạch đầu.** `--json-summary` → `"json-summary"` hoặc `"json_summary"`, cả hai đều được. Chép thẳng từ `--help` là đúng.
- **Dùng được cho mọi tham số của lệnh đó**, kể cả những thứ `.sfproj` không lưu: `timeout`, `bitrate`, đường dẫn report, `security-dll-signature`, `tx_id`/`rx_id`…
- **Thứ tự ưu tiên: tham số gõ trên dòng lệnh > file config > `--project` > mặc định.** Nên có thể dùng một file chung rồi ghi đè từng lần:

  ```powershell
  .\SFlash_CLI.exe flash --config line1.json --serial 999888
  ```

- **Gõ sai tên key sẽ báo lỗi (exit 2), không bị bỏ qua âm thầm** — kèm gợi ý key đúng:

  ```
  Config error: Config file has 1 setting(s) that `flash` does not accept:
    'timeout_s' — did you mean 'timeout'?
  ```

  Điều này quan trọng: một key `timeout_s` bị bỏ qua lặng lẽ nghĩa là lần chạy đó **không có watchdog nào cả**, đúng thứ mà `--timeout` sinh ra để ngăn.

- **Giá trị được kiểm tra như trên dòng lệnh.** `"tx_id": "0x77A"` thành số, `"tester_serial": "AABBCCDD"` thành chuỗi byte, `"sequence": "bogus"` bị từ chối ngay.
- Có thể để `"comment"` tuỳ ý — JSON không có cú pháp comment nên key này được bỏ qua.
- Dùng được cho `flash`, `batch`, `parallel`, `test-connection`.

Với `batch`/`parallel`, danh sách ECU cũng nằm luôn trong file được:

```json
{
  "file": ["firmware.s19"],
  "hardware": "vector",
  "timeout": 900,
  "unit": [
    "name=Left,channel=0,serial=123456,side=s0",
    "name=Right,channel=1,serial=123456,side=s1"
  ]
}
```

> Lưu ý đường dẫn Windows trong JSON: phải viết `"C:\\tools\\SeedKey64.dll"` (gạch chéo ngược nhân đôi) hoặc `"C:/tools/SeedKey64.dll"`.

> Lưu ý về cờ bật/tắt: đã đặt `"quiet": true` trong file thì **không tắt được từ dòng lệnh**; muốn tắt thì sửa thành `"quiet": false` trong file.

---

## 5. Tham số hay dùng

| Tham số | Ý nghĩa |
|---|---|
| `--hardware virtual\|vector` | `virtual` = ECU giả lập (mặc định), `vector` = phần cứng thật |
| `--channel N` `--serial S` | Chọn phần cứng. **Nên luôn kèm `--serial`** — nó chọn trực tiếp thiết bị, bỏ qua phần cấu hình channel trong Vector Hardware Config. Lấy giá trị đúng từ `list-hardware`. |
| `--sequence suzuki\|generic` | Bộ bước nạp. Mặc định `suzuki`. |
| `--radar-side s0\|s1` | Preset CAN ID cho Suzuki Radar. `s0` = Tx 0x77B/Rx 0x78B, `s1` = Tx 0x77A/Rx 0x78A |
| `--tx-id` `--rx-id` | Chỉ định thẳng CAN ID, ghi đè `--radar-side` |
| `--security-dll <path>` | DLL seed/key của OEM. Không truyền = dùng thuật toán dummy built-in. |
| `--timeout <giây>` | **Nên luôn dùng.** Xem mục 7. |
| `--report x.html` | Báo cáo HTML đầy đủ |
| `--json-summary x.json` | Tóm tắt máy đọc được (cho script/pipeline) |
| `--trace-csv x.csv` | Bảng trace CAN/UDS dạng CSV |
| `-v` / `-q` | In thêm trace TX/RX / chỉ in kết quả cuối |
| `--dry-run` | In các bước rồi thoát, không gửi gì tới ECU |
| `--config x.json` | Nạp toàn bộ setting từ file JSON (xem mục 4) |

Xem đầy đủ: `.\SFlash_CLI.exe flash --help`, `.\SFlash_CLI.exe batch --help`, …

---

## 6. Mã thoát (exit code)

Dùng trong script để biết nên thử lại hay dừng hẳn.

| Code | Nghĩa | Nên làm |
|---|---|---|
| `0` | Thành công | đi tiếp |
| `1` | ECU có trả lời nhưng quá trình nạp lỗi | **Dừng** — lỗi thật, thử lại cũng vậy |
| `2` | Sai tham số, sai file, sai cấu hình | Dừng, sửa lệnh |
| `3` | Không kết nối được ECU | Thử lại được — kiểm tra cáp, nguồn, channel có bị chiếm |
| `4` | Hết thời gian `--timeout` | Thử lại được, nhưng nên xem report trước |
| `130` | Bị ngắt bằng Ctrl+C | — |

Với `batch` và `parallel`, exit `0` chỉ khi **mọi** ECU đều PASS.

Trong PowerShell, mã thoát nằm ở `$LASTEXITCODE`:

```powershell
.\SFlash_CLI.exe flash firmware.s19 --hardware vector --channel 0 --timeout 900
if ($LASTEXITCODE -ne 0) { Write-Error "Flash that bai: $LASTEXITCODE"; exit $LASTEXITCODE }
```

---

## 7. `--timeout` — nên luôn dùng khi chạy tự động

Không có `--timeout`, một lần chạy **không có giới hạn thời gian**: khi ECU liên tục trả "đang xử lý" (`0x78`), một lệnh đơn lẻ có thể chiếm tới **500 giây**. Job tự động sẽ đứng im cho tới khi bị hệ thống giết — **không kịp ghi báo cáo** và ECU bị bỏ dở giữa phiên.

Có `--timeout`, khi hết giờ công cụ tự dừng đúng cách: khôi phục bus, **vẫn ghi report**, thoát mã `4`.

Với `batch`/`parallel`, đây là ngân sách **cho mỗi ECU**, không phải cho cả loạt.

Giá trị gợi ý: `--timeout 120` cho `test-connection`, `--timeout 900` cho `flash` (điều chỉnh theo kích thước firmware thật).

---

## 8. Báo cáo

```powershell
.\SFlash_CLI.exe flash firmware.s19 --hardware vector --channel 0 --timeout 900 ^
    --report report.html --json-summary result.json --trace-csv trace.csv
```

Cả ba file đều được ghi **dù thành công hay thất bại** — lúc lỗi mới là lúc cần chúng nhất.

- **`report.html`** — mở bằng trình duyệt: tóm tắt, firmware, từng bước, toàn bộ trace.
- **`result.json`** — cho script. Có `result`, `duration_seconds`, `ecu_info` (serial/phiên bản ECU), `units` (với batch/parallel), `unit_counts`.
- **`trace.csv`** — bảng TX/RX đầy đủ, mở bằng Excel. Đây là thứ nên gửi kèm khi cần hỗ trợ.

Trace được ghi đầy đủ **không cần** `-v`; cờ `-v` chỉ quyết định có in ra màn hình hay không.

> Lưu ý: sequence `suzuki` cố tình không có bước đọc DID, nên `ecu_info` sẽ rỗng khi nạp bằng sequence đó. Muốn ghi lại thông tin ECU thì chạy `test-connection --json-summary` trước, hoặc dùng `batch` (tự Identify từng ECU).

---

## 9. Xử lý sự cố

### "Cannot load Security DLL ... the DLL is 32-bit (x86) but SFlash is running as 64-bit"

DLL seed/key của bạn là bản 32-bit. Windows **không thể** nạp DLL 32-bit vào tiến trình 64-bit — đây là quy tắc của hệ điều hành, chọn lại file không giải quyết được.

→ Xin bên cấp DLL **bản x64**. Họ thường có sẵn cả hai; bản 32-bit hay được gửi vì đi kèm bộ công cụ CANoe/CANape.

### "the file exists, but Windows could not find one of the DLLs it depends on (WinError 126)"

DLL đúng kiến trúc nhưng thiếu DLL mà nó phụ thuộc.

→ Chạy `check-security-dll` để xem nó cần những DLL nào, rồi copy đủ vào **cùng thư mục** với nó, kèm Visual C++ Runtime tương ứng.

### "Failed to load dynlib/dll ... not found when the application was frozen"

Nếu còn thấy câu này: bạn đang chạy **bản cũ**. Bản mới đã bóc lỗi gốc ra và in nguyên nhân thật.

### "ECU not reachable" (exit 3)

Theo thứ tự: cáp CAN và nguồn ECU → `list-hardware` có thấy thiết bị không → `--channel`/`--serial` có đúng không → có phần mềm khác (CANoe/CANalyzer/CANape) đang chiếm channel không → `--bitrate` và CAN ID (`--radar-side`) có đúng ECU không.

### "Security DLL returned error N" hoặc ECU từ chối key (NRC 0x35)

DLL chạy được nhưng không ra key ECU chấp nhận. Thường là thiếu tham số `iVariant` — chuỗi để DLL biết áp thuật toán nào:

```powershell
.\SFlash_CLI.exe flash firmware.s19 ... --security-dll-variant SUZ05
```

→ Hỏi bên cấp DLL giá trị đúng (hoặc xác nhận để trống là được).

### "called as a uint32 -> uint32 function ... but the ECU sent N bytes"

Chữ ký hàm của DLL bị hiểu sai. Mặc định `auto` đúng cho mọi DLL chuẩn Vector; chỉ DLL kiểu cũ 1 tham số mới cần `--security-dll-signature uint32`. Chạy `check-security-dll` để xem nó export hàm gì.

### Cảnh báo "possible CAN bus conflict"

Có CANoe/CANalyzer/CANape đang chạy, hoặc channel đang được ứng dụng khác dùng. Công cụ **chỉ cảnh báo rồi chạy tiếp** (để script không bị treo). Nếu phần mềm kia đang đo trên cùng channel, hãy đóng nó lại — lưu lượng chẩn đoán của nó sẽ xung đột với phiên nạp.

---

## 10. Dùng trong pipeline CI/CD

```yaml
flash_ecu:
  tags: [windows, vector-hw]
  variables:
    SFLASH_GITLAB_TOKEN: $CI_JOB_TOKEN
  script:
    - C:\tools\SFlash_CLI\SFlash_CLI.exe check-security-dll C:\tools\SeedKey64.dll
    - C:\tools\SFlash_CLI\SFlash_CLI.exe test-connection --config ci\identify.json
    - C:\tools\SFlash_CLI\SFlash_CLI.exe flash --config ci\flash_left.json
  artifacts:
    when: always
    paths: [flash_report.html, flash_result.json, trace.csv]
```

Bốn điểm đáng lưu ý:

1. **Đưa cấu hình vào file `--config` và commit cùng repo** — pipeline đọc được, review được, không phải dòng lệnh dài dằng dặc trong YAML.
2. **`artifacts: when: always`** — báo cáo có giá trị nhất đúng lúc job thất bại.
3. **Luôn có `--timeout`** (đặt trong file config) — xem mục 7.
4. **Dựa vào exit code, đừng đọc chuỗi trong log.** Mã `3`/`4` có thể thử lại, mã `1` thì không.

---

## 11. Cần hỗ trợ — gửi kèm những gì

1. Nguyên văn thông báo lỗi (copy từ terminal, đừng chụp màn hình nếu copy được).
2. `trace.csv` — quan trọng nhất, có đủ từng khung TX/RX.
3. `report.html` và `result.json`.
4. Kết quả `list-hardware` và `check-security-dll <dll>`.
5. Lệnh đã chạy, đầy đủ tham số (hoặc file `--config` đã dùng).
