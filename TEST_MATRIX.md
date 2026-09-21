# Ma trận kiểm thử tích hợp AI

Không đóng gói `.exe` cho đến khi cả sáu tích hợp đạt kiểm thử end-to-end.

| Nền tảng | Có trên máy | Adapter | Hook thật | YELLOW | GREEN | Kết quả |
|---|---:|---:|---:|---:|---:|---|
| Codex | Có | Đạt | Đạt | Đạt | Đạt | Đạt |
| Claude Desktop | Có (2.2553.1) | Đạt | Không hỗ trợ | Đang test | Đang test | Dùng bộ dò cục bộ, chờ prompt thật |
| Claude Code | Chưa | Đạt | Chưa test | Chưa test | Chưa test | Chờ cài/đăng nhập |
| Antigravity IDE | Có | Đạt | Đạt | Đạt | Đạt | RED đang kiểm thử sau khi thêm tiến trình IDE |
| Cursor | Có (3.21.16) | Đạt | Đang test | Đang test | Đang test | Hook toàn cục đã chuẩn bị |
| GitHub Copilot VS Code | Có (tích hợp trong VS Code 1.124.2) | Đạt | Đạt | Đạt | Đạt | Prompt thật đã kích hoạt hook và phần cứng |
| GitHub Copilot app | Có (github.exe 1.1.22) | Đạt | Đang test | Đang test | Đang test | Hook camelCase đã cài cho app riêng |
| GitHub Copilot CLI | Chưa | Đạt | Chưa test | Chưa test | Chưa test | Chờ cài/đăng nhập |

## Tiêu chí đạt cho từng nền tảng

1. Client tự kích hoạt hook khi gửi prompt thật.
2. Log ghi đúng `provider` và không lưu nội dung prompt.
3. ESP32 nhận YELLOW khi bắt đầu xử lý.
4. ESP32 nhận GREEN khi câu trả lời kết thúc.
5. Buzzer kêu khi chuyển GREEN.
6. Hook hỏng hoặc ESP32 bị rút không được làm gián đoạn AI client.

## Nhật ký kiểm thử hiện tại

- Codex: đã xác nhận bằng phiên Codex thật từ một thư mục khác; các hook
  `SessionStart`, `UserPromptSubmit` và `Stop` đều được client báo `Completed`,
  bridge nhận YELLOW rồi GREEN.
- Antigravity IDE 2.5.5: prompt thật đã kích hoạt `PreInvocation` và `Stop`, log
  ghi đúng `conversationId`, bridge nhận YELLOW rồi GREEN. Tiến trình
  `Antigravity IDE.exe` đã được thêm vào typing watcher để kiểm thử RED.
- VS Code 1.124.2 có Copilot tích hợp sẵn. Hook người dùng dùng
  `UserPromptSubmit` và `Stop` từ `~/.copilot/hooks/ai-status-light.json`;
  prompt thật đã tạo YELLOW rồi GREEN trên ESP32. `Code.exe` được theo dõi để
  chuyển RED khi bắt đầu nhập.
- Claude Desktop 2.2553.1 không có hook cho Chat. Đã thêm `claude.exe` vào bộ
  theo dõi: gõ chuyển RED, Enter chuyển YELLOW; GREEN được suy ra sau khi đã có
  hoạt động thật của Claude và toàn bộ tiến trình Claude yên liên tục 3 giây.
  Log chỉ lưu provider/sự kiện/trạng thái, không lưu prompt.
