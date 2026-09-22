# AI Status Light

Đèn trạng thái vật lý cho Codex, dùng XIAO ESP32-C3 qua USB Serial.

- `RED`: người dùng bắt đầu nhập prompt mới trong cửa sổ Codex.
- `YELLOW`: Codex đang xử lý prompt.
- `GREEN`: Codex hoàn tất; firmware phát số tiếng bíp đã chọn và giữ xanh cho đến khi người dùng bắt đầu nhập prompt mới.
- `OFF`: tắt toàn bộ LED.

## Cấu trúc

```text
ai-status/
├── firmware/firmware.ino
├── tests/test_ai_status_core.py
├── ai_status.py
├── ai_status_bridge.py
├── ai_status_core.py
├── ai_status_hook.py
├── ai_status_test.py
├── config.json
└── requirements.txt
```

## 1. Firmware và đấu dây

Mặc định firmware dùng chân của XIAO ESP32-C3:

| Thiết bị | Chân board | GPIO |
|---|---:|---:|
| LED đỏ | D1 | 3 |
| LED vàng | D2 | 4 |
| LED xanh | D3 | 5 |
| Buzzer | D4 | 6 |

Mỗi LED cần điện trở hạn dòng phù hợp. Nếu mạch dùng chân hoặc mức kích hoạt khác, sửa các hằng số đầu file `firmware/firmware.ino`. Nạp firmware bằng Arduino IDE với baud `115200`.

## 2. Cài và chạy bridge

```powershell
py -3 -m pip install -r requirements.txt
py -3 ai_status_bridge.py
```

Bridge giữ `COM5` mở và nhận lệnh nội bộ tại `127.0.0.1:8765`. Hook sẽ tự khởi động bridge và bộ theo dõi nhập liệu nếu cần. Đổi cổng, COM hoặc tên tiến trình cửa sổ cần theo dõi (`typing_process_names`) trong `config.json`. `green_hold_seconds` bằng `0` nghĩa là giữ xanh đến khi bắt đầu gõ; đặt số dương để dùng chế độ tự về đỏ theo thời gian.

### Bảo mật thiết bị

Mỗi thiết bị có thể dùng khóa riêng 256-bit trong trường `auth_key`. Firmware giữ cùng
khóa tại `firmware/security_config.h` (file này bị loại khỏi Git). Trước mỗi lệnh,
ESP32 phát nonce ngẫu nhiên dùng một lần; bridge ký `nonce|command` bằng HMAC-SHA256.
Firmware từ chối lệnh chữ thuần, chữ ký sai, nonce quá 10 giây và việc phát lại lệnh.
Bridge tại `127.0.0.1:8765` cũng yêu cầu `bridge_auth_token`, vì vậy một chương
trình cục bộ không thể mượn bridge để ký lệnh nếu không có token của ứng dụng.
Muốn tạo khóa cho thiết bị mới, sao chép `security_config.example.h`, tạo 32 byte ngẫu
nhiên và điền cùng chuỗi hex 64 ký tự vào header lẫn thiết bị tương ứng trong config.

Lớp xác thực này ngăn phần mềm khác điều khiển đèn qua cổng COM nhưng không thay thế
khóa phần cứng. Trước khi phát hành, dùng ESP-IDF để bật đồng thời Secure Boot v2,
Flash Encryption và Secure UART Download Mode. Không đốt eFuse trong giai đoạn thử
nghiệm: một số lựa chọn là vĩnh viễn và có thể khiến bo mạch không nạp lại được nếu
mất khóa ký.

### Cập nhật firmware bằng GitHub Releases

Trang **Cập nhật** nhận repository theo dạng `owner/repository`, gọi GitHub Releases
API, chọn asset `ai-status-light-esp32c3.bin`, kiểm tra digest SHA-256 do GitHub trả
về rồi truyền firmware qua USB bằng phiên cập nhật HMAC. ESP32 ghi ảnh mới vào phân
vùng OTA không hoạt động, xác minh lại SHA-256 và chỉ sau đó mới đổi phân vùng boot.
Firmware 1.1 dùng ACK cho từng khối 256 byte để không làm tràn bộ đệm USB của
XIAO ESP32-C3. Nếu mất dữ liệu hơn 30 giây, phân vùng đang ghi bị hủy và firmware
đang chạy được giữ nguyên. Trước khi cập nhật, thiết bị lưu phân vùng nguồn để có
thể rollback ngay cả với biến thể bootloader Arduino không bật rollback native.

Workflow `.github/workflows/firmware-release.yml` tự build và tạo Release khi push
tag `ai-status-light-v*`. Ứng dụng chỉ chọn Release chứa đúng firmware asset nên có
thể dùng chung repository với website 47Square. Bản Release chung không chứa khóa thiết bị. Khóa riêng được provision một
lần ở nhà máy rồi lưu trong NVS, vì vậy OTA giữ nguyên danh tính và khóa của từng
ESP32. `config.json` và `firmware/security_config.h` chứa bí mật cục bộ nên đã bị
loại khỏi Git; dùng các file `.example` làm mẫu.

Thiết bị xuất xưởng cần được full-flash firmware 1.1 ít nhất một lần để nhận bảng
phân vùng OTA. Những lần sau cập nhật hoàn toàn qua GitHub Release. Kết quả
`started`, `succeeded`, `failed` và `rollback` được gửi qua RPC giới hạn quyền tới
`device_events`; ứng dụng không chứa service-role key. Có thể chạy lại ba bài test
phần cứng bằng:

```powershell
py -3 ai_status_ota_diagnostics.py build\esp32-rollback\firmware.ino.bin
```

## 3. Kiểm tra

Ở terminal thứ hai:

```powershell
py -3 ai_status.py red
py -3 ai_status.py yellow
py -3 ai_status.py green
py -3 ai_status.py off
```

Hoặc chạy chu trình tự động:

```powershell
py -3 ai_status_test.py --delay 1
```

Chạy unit test không cần phần cứng:

```powershell
py -3 -m unittest discover -s tests -v
```

## Giao diện điều khiển

Mở dashboard desktop bằng:

```powershell
pyw -3 ai_status_app.py
```

Hoặc nhấp đúp `open_ai_status.cmd`. Trong giai đoạn kiểm thử, shortcut
`AI Status Light` trên Desktop trỏ trực tiếp tới mã nguồn này; mọi thay đổi giao
diện sẽ xuất hiện ở lần mở tiếp theo mà chưa cần đóng gói lại `.exe`.

Giao diện hiển thị trạng thái RED/YELLOW/GREEN theo thời gian thực, tình trạng
bridge và COM, bốn nút kiểm tra đèn, nút thử buzzer, danh sách thiết bị, sáu
tích hợp AI và nhật ký sự kiện gần nhất. Dashboard chỉ đọc metadata sự kiện;
không đọc hoặc hiển thị nội dung prompt. Bridge hỗ trợ lệnh `PING` riêng để
giao diện kiểm tra kết nối mà không làm thay đổi LED.

Trang Cài đặt cho phép chọn độc lập 0–3 tiếng bíp cho RED, YELLOW và GREEN;
bridge đọc cấu hình mới ở mỗi lệnh nên không cần khởi động lại. Trang Thiết bị
tự nhận diện các cổng USB serial và cho phép gán đúng một AI cho mỗi ESP32. Một
AI đã được gán sẽ bị loại khỏi menu của các thiết bị còn lại. Nếu chưa gán bất
kỳ thiết bị nào, bridge dùng chế độ tương thích và tiếp tục chuyển mọi sự kiện
tới ESP32 hiện có.

Trang Tích hợp dùng logo riêng, tự kiểm tra adapter và hiển thị `Hoạt động`,
`Cần thiết lập` hoặc `Chưa cài ứng dụng`. Nút `Thiết lập` chỉ cài hook/plugin và
quyền cần cho AI được chọn; phần lõi của ứng dụng không tự cài toàn bộ adapter.

## 4. Hook Codex

Hook toàn cục `C:\Users\admin\.codex\hooks.json` cấu hình `SessionStart`, `UserPromptSubmit`, `Stop` và `Interrupt`, nên đèn hoạt động trong mọi project Codex của tài khoản Windows này. `Stop` gửi trạng thái hoàn tất để bật GREEN và buzzer. `ai_status_typing_watcher.py` chỉ phân loại mã phím khi `ChatGPT.exe` ở foreground; nó không chuyển mã phím thành văn bản và không lưu nội dung. Phím nhập đầu tiên chuyển đèn sang RED, còn `UserPromptSubmit` chuyển sang YELLOW. Hook chủ động bỏ qua lỗi nên việc rút ESP32 không làm gián đoạn Codex.

Khi người dùng hủy lượt đang chạy, `Interrupt` chuyển RED. Fallback theo dõi
transcript cũng nhận `turn_aborted`/`turn_cancelled`, nên đèn không bị kẹt ở
YELLOW nếu hook giao diện đến chậm hoặc không chạy.

Nếu sửa nội dung hook, Codex có thể yêu cầu duyệt tin cậy lại bằng lệnh `/hooks`.

## 5. GitHub Copilot trong VS Code

Hook toàn cục nằm tại `C:\Users\admin\.copilot\hooks\ai-status-light.json`.
`UserPromptSubmit` chuyển LED sang YELLOW và `Stop` chuyển sang GREEN, đồng thời
kích hoạt buzzer. Bộ theo dõi nhập liệu nhận diện `Code.exe` để chuyển LED sang
RED khi người dùng bắt đầu gõ. User Settings bật `chat.useHooks` và đăng ký
`~/.copilot/hooks` qua `chat.hookFilesLocations`. VS Code tự nạp lại file hook
khi file được lưu; nếu một cửa sổ cũ chưa nhận cấu hình, chạy
`Developer: Reload Window` một lần.

## 6. Ứng dụng GitHub Copilot riêng

Ứng dụng `github.exe` sử dụng engine Copilot CLI. Hook toàn cục riêng nằm tại
`C:\Users\admin\.copilot\hooks\ai-status-app.json`, dùng
`userPromptSubmitted` để chuyển YELLOW và `agentStop` để chuyển GREEN. Bộ theo
dõi nhập liệu nhận diện `github.exe` để chuyển RED khi bắt đầu gõ.

## 7. Cursor

Hook toàn cục nằm tại `C:\Users\admin\.cursor\hooks.json` và áp dụng cho mọi
project. `beforeSubmitPrompt` chuyển LED sang YELLOW, còn `stop` chuyển sang
GREEN và kích hoạt buzzer. Bộ theo dõi nhập liệu nhận diện `Cursor.exe` để
chuyển LED sang RED khi bắt đầu gõ.

Payload `stop` của Cursor được phân biệt theo `status`: `completed` chuyển
GREEN, còn `aborted`, `interrupted` hoặc `error` chuyển RED. Phím nhập đầu tiên
sau khi chuyển từ AI khác luôn được ưu tiên chuyển RED, kể cả trạng thái Cursor
trước đó chưa tồn tại hoặc còn YELLOW cũ.

## 8. Claude Desktop

Claude Desktop Chat hiện không cung cấp lifecycle hook như Claude Code. Vì vậy
`ai_status_typing_watcher.py` nhận diện `claude.exe` theo cách không đọc hoặc
lưu nội dung hội thoại: phím nhập đầu tiên chuyển RED, Enter không kèm phím bổ
trợ chuyển YELLOW, rồi bộ dò hoạt động CPU/I/O của các tiến trình Claude chờ
luồng trả lời yên ít nhất 3 giây để chuyển GREEN và kích hoạt buzzer. Shift+Enter
chỉ xuống dòng và không đổi sang YELLOW. Đây là adapter riêng cho Claude
Desktop; khi Claude Code được cài, adapter hook gốc `claude-code` vẫn được ưu
tiên vì chính xác hơn.
# Project-aware device routing

Each ESP32 is assigned one AI and one project. Multiple devices may use the
same AI as long as their projects differ. Hook events carry the normalized
workspace path, so activity from another project is ignored. Changing either
the AI or project immediately resets that device to RED.

The device page discovers projects from hook history and the recent-workspace
metadata maintained by supported editors. A project first seen by a hook is
automatically added to the list without storing prompt text.

Project isolation uses each provider's event-scoped workspace field (`cwd`,
`workspace_roots`, or `workspacePaths`). If an older client omits that field
while multiple editor windows are open, the event is ignored rather than
guessed from a potentially stale `lastActiveWindow` value.

## Decor mode

The Decor page controls each ESP32 independently, including per-channel
red/yellow/green toggles, all-on/all-off, speed, and firmware-driven effects:
Static, Blink, Breathing, Fade In/Out, Smooth Transition, Color Cycle, Rainbow,
Theater Chase, Comet, Meteor, Scanner, Sparkle, and Police. On the three
discrete LEDs, color effects interpolate or sequence red, yellow, and green.
Decor runs non-blocking on the ESP32. Any normal AI status command immediately
stops the effect and restores RED/YELLOW/GREEN status priority.
