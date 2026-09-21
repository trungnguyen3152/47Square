# AI Status Light — Kế hoạch ứng dụng Windows

Ngày lưu: 2026-09-20

## Mục tiêu

Xây dựng một ứng dụng Windows có giao diện, đóng gói tự chứa và không yêu cầu
người dùng cài Python. Ứng dụng quản lý ESP32, cài hook cho các công cụ AI và
điều khiển LED/buzzer theo trạng thái làm việc.

## Quy ước trạng thái

- `RED`: người dùng bắt đầu nhập yêu cầu mới hoặc AI đang chờ người dùng.
- `YELLOW`: AI đang xử lý yêu cầu.
- `GREEN`: AI đã trả lời xong; buzzer phát âm báo.
- `OFF`: tắt đèn.

## Nền tảng cần hỗ trợ

1. Codex
2. Claude Code
3. Google Antigravity
4. Cursor
5. GitHub Copilot trong VS Code
6. GitHub Copilot CLI

Copilot Cloud Agent không thể điều khiển trực tiếp ESP32 cắm ở máy người dùng;
phạm vi hỗ trợ là các phiên chạy cục bộ.

## Mô hình thiết bị đã thống nhất

- Một ESP32 mở khóa một khe nền tảng AI.
- Một ESP32 chỉ được gán cho một nền tảng AI tại một thời điểm.
- Có `N` ESP32 đang kết nối thì có thể bật tối đa `N` nền tảng AI.
- Một ESP32 gán cho Codex, Claude Code, Cursor... sẽ theo dõi mọi project của
  nền tảng đó trên máy, không gắn riêng với từng project.
- Rút một ESP32 thì nền tảng được gán cho thiết bị đó tạm ngừng điều khiển đèn.
- Ghép thiết bị theo ID chip, không theo tên cổng COM, vì số COM có thể thay đổi.
- ESP32 mới được để ở trạng thái chưa gán cho đến khi người dùng chọn nền tảng.

Ví dụ:

```text
ESP32 #1 -> Codex        [Đã kết nối] [COM5]
ESP32 #2 -> Claude Code  [Đã kết nối] [COM7]
ESP32 #3 -> Chưa gán     [Đã kết nối] [COM9]
```

## Giao diện dự kiến

### Dashboard

- Danh sách ESP32, nền tảng được gán và trạng thái hiện tại.
- Hiển thị RED/YELLOW/GREEN theo thời gian thực.
- Cho phép gán lại nền tảng bằng danh sách chọn.
- Cảnh báo xung đột hoặc thiết bị bị rút.

### Thiết bị

- Tự dò và tự kết nối lại cổng COM.
- Nút thử RED, YELLOW, GREEN, OFF và buzzer.
- Độ sáng LED.
- Bật/tắt buzzer, số lần bíp, thời lượng bíp và khoảng nghỉ.
- Thanh âm lượng PWM khi phần cứng hỗ trợ.

### Tích hợp

- Công tắc cài/gỡ hook riêng cho từng nền tảng.
- Hiển thị trạng thái hook: chưa cài, đã cài, cần trust hoặc lỗi.
- Hook gửi tên nền tảng, ID phiên và trạng thái tới ứng dụng trung tâm.

### Cài đặt và chẩn đoán

- Tự chạy cùng Windows.
- Nhật ký sự kiện và kết nối serial.
- Phiên bản firmware và nút cập nhật firmware.
- Xuất gói chẩn đoán khi có lỗi.

## Kiến trúc dự kiến

- Ứng dụng Windows tự chứa, đóng gói thành `.exe`; máy đích không cần Python.
- Một tiến trình nền sở hữu toàn bộ cổng serial và nhận sự kiện hook qua
  `127.0.0.1`.
- Mỗi sự kiện gồm `provider`, `session_id`, `state` và thời gian.
- Bộ định tuyến tìm ESP32 đang được gán cho `provider` rồi gửi lệnh tương ứng.
- Cấu hình người dùng lưu trong `%LOCALAPPDATA%\AIStatusLight`.
- Hook dùng đường dẫn tuyệt đối tới executable đã cài đặt.
- Firmware trả về ID chip, phiên bản và các khả năng phần cứng khi handshake.

## Ánh xạ hook

| Nền tảng | Bắt đầu xử lý | Hoàn thành |
|---|---|---|
| Codex | `UserPromptSubmit` | `Stop` |
| Claude Code | `UserPromptSubmit` | `Stop` |
| Antigravity | `PreInvocation` | `Stop` |
| Cursor | `beforeSubmitPrompt` | `stop` |
| Copilot VS Code | `UserPromptSubmit` | `Stop` |
| Copilot CLI | `userPromptSubmitted` | `agentStop` |

Việc nhận biết bắt đầu gõ để chuyển RED dựa trên foreground process. Với AI
chạy trong terminal, tính năng này là tùy chọn vì terminal cũng có thể được dùng
cho lệnh thông thường.

## Lưu ý buzzer

Mạch hiện dùng active buzzer. Có thể điều chỉnh bật/tắt, số lần và thời lượng
bíp; điều chỉnh âm lượng bằng PWM chỉ có phạm vi hạn chế. Để thay đổi âm lượng
và cao độ tốt hơn nên dùng passive buzzer qua transistor.

## Việc cần làm ở phiên tiếp theo

1. Hoàn tất adapter và kiểm thử hook cho đủ sáu AI client.
2. Chạy prompt thật trên từng client và xác nhận YELLOW/GREEN trong log/phần cứng.
3. Chỉ khi bảng kiểm thử đạt 6/6 mới chốt giao diện MVP và đóng gói executable.
4. Thiết kế giao thức serial mới có handshake và device ID.
5. Nâng cấp firmware nhưng vẫn tương thích các lệnh RED/YELLOW/GREEN/OFF cũ.
6. Xây service quản lý nhiều cổng COM và định tuyến theo nền tảng.
7. Xây giao diện Dashboard/Thiết bị/Tích hợp/Cài đặt.
8. Viết bộ cài hook không ghi đè cấu hình hook hiện hữu của người dùng.
9. Đóng gói bản self-contained và kiểm thử trên máy không cài Python.

## Cổng chất lượng trước khi đóng gói

Không bắt đầu đóng gói `.exe` cho đến khi cả sáu dòng trong bảng kiểm thử đều
đạt: hook bắt đầu thực sự được client kích hoạt, hook kết thúc thực sự được kích
hoạt, log đúng nguồn, LED nhận YELLOW rồi GREEN và hook lỗi theo kiểu fail-open.

## Trạng thái project hiện tại

- Bridge Python và hook Codex hiện tại vẫn hoạt động.
- Hook Codex đang được cài ở cấp người dùng cho mọi project Codex trên máy này.
- Cổng hiện tại là COM5, bridge lắng nghe tại `127.0.0.1:8765`.
- Unit test hiện tại: 9 test.
- Chưa bắt đầu thay thế hệ thống đang chạy bằng ứng dụng Windows mới.

## Checkpoint 04:45 ngày 21/09/2026

Đã lưu giữa chừng theo yêu cầu, tiếp tục sau 09:37:

- Giao diện navy/tím/cyan hiện đại đã chạy được.
- `config.json` đã có `status_beeps` cho RED/YELLOW/GREEN, mỗi màu nhận 0–3 bíp.
- Firmware đã nhận cú pháp `RED 2`, `YELLOW 1`, `GREEN 3`; chưa build/flash bản mới.
- Bridge đã có `DeviceRouter`, hỗ trợ lệnh theo provider hoặc theo cổng COM.
- State hook đã bắt đầu tách theo provider để nhiều AI không ghi đè trạng thái nhau.
- Trang Thiết bị đã có menu gán một AI cho mỗi ESP32; lựa chọn đã dùng sẽ bị loại
  khỏi menu thiết bị khác.
- Đã thêm module `ai_status_integrations.py` để kiểm tra và cài adapter theo từng AI.
- Trang Tích hợp đã có logo vẽ bằng code, trạng thái và nút Thiết lập/Cài lại.

Việc còn lại khi tiếp tục:

1. Hoàn thiện UI chọn số bíp cho cả ba màu và lưu cấu hình.
2. Nối kết quả cài adapter vào hàng đợi UI và làm mới trạng thái.
3. Hoàn thiện/kiểm thử định tuyến provider → đúng ESP32.
4. Sửa test theo giao thức provider mới và chạy toàn bộ test.
5. Build/flash firmware mới vào COM5 rồi test bíp 0/1/2/3 cho từng màu.
6. Khởi động lại bridge/watcher/UI và kiểm tra end-to-end.

## Hoàn tất mở rộng giao diện ngày 21/09/2026

- Đã hoàn thiện lựa chọn 0/1/2/3 bíp riêng cho RED, YELLOW và GREEN.
- Đã triển khai `DeviceRouter` và giao thức `STATUS <provider> <status>` /
  `DEVICE <port> <status>`.
- Trang Thiết bị gán một AI duy nhất cho mỗi cổng USB ESP32 và loại lựa chọn
  trùng khỏi các menu khác.
- Trang Tích hợp có logo, phát hiện trạng thái và nút cài/cài lại adapter riêng.
- Firmware buzzer mới đã build và flash thành công vào XIAO ESP32-C3 trên COM5.
- Bridge đã nhận thành công PING và lệnh RED/YELLOW/GREEN sau khi flash.
- Bộ test đã được mở rộng để kiểm tra provider routing và chống rò sự kiện giữa
  thiết bị.

## Sửa trạng thái hủy và chuyển AI ngày 21/09/2026

- Codex transcript fallback nhận `turn_aborted`/`turn_cancelled` và trả RED.
- Cursor `stop` đọc trường `status`; hủy/lỗi trả RED thay vì GREEN.
- Typing watcher không còn yêu cầu provider phải từng ở GREEN; phím đầu tiên
  sau khi đổi AI ghi đè trạng thái rỗng hoặc YELLOW cũ ngay lập tức.
- Test phần cứng Cursor `busy → aborted` đã cho YELLOW → RED.
- Đường chuyển trạng thái RED đo được khoảng 32.5 ms trên máy thử nghiệm.
