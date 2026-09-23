# MQ Solar Local Monitor (Standalone)

Service Python độc lập, poll dữ liệu từ mạch MPPT Charger / Inverter hòa lưới
Mạnh Quân Solar (qua Local HTTP hoặc Cloud WebSocket) và UPS qua server
ViewPower (Vertiv/Liebert), lưu lịch sử vào SQLite và expose REST/JSON API.
Chạy trực tiếp trên Ubuntu (venv/systemd), **không cần Home Assistant hay
HAOS**.

> Nếu bạn cần dùng qua Home Assistant, xem [readme.md](readme.md) — hướng dẫn
> cài `custom_components/local_mqsolar` (integration HACS gốc), độc lập với
> service ở đây.

## Cài đặt

```bash
uv sync
cp config.example.toml config.toml
```

Sau đó khai báo thiết bị theo 1 trong 2 cách:

- **Tự động quét mạng LAN** (giống bước "Quét mạng" trong config flow của HA):
  ```bash
  ./scan.sh
  ```
  Script sẽ dò toàn bộ dải mạng `/24` hiện tại, tự thêm các thiết bị tìm được
  vào khối `[[local_devices]]` trong `config.toml` (bỏ qua thiết bị đã có sẵn,
  chạy lại nhiều lần không bị trùng).
- **Nhập tay**: sửa trực tiếp `config.toml`, khai báo host thiết bị local
  và/hoặc token cloud.

### Thêm UPS qua ViewPower (Vertiv/Liebert)

Nếu máy đã cài phần mềm ViewPower theo dõi UPS (chạy trên Tomcat, mặc định
port 15178), lấy `port_name` bằng cách mở `http://<host>:15178/ViewPower/monitor`
rồi xem `window.sessionStorage.getItem("portName")` qua DevTools Console
(thường là `"USBusbdev1"` cho UPS nối qua USB), rồi khai báo trong
`config.toml`:

```toml
[[viewpower_devices]]
port_name = "USBusbdev1"
host = "localhost"
port = 15178
name = "UPS Phòng Server"
nominal_watts = 800   # công suất định mức thực (W); ViewPower không tự báo
```

Trang tổng quan (`/`) hiển thị **sơ đồ dòng điện** của UPS (Input → Rectifier
→ Inverter → Output, nhánh Battery, chỉ báo Bypass, nhánh Solar nối sang
Battery) giống giao diện ViewPower gốc, cùng các khung thông tin
Input/UPS/Solar/Output/Battery — tự đổi màu theo trạng thái thực tế: Bypass
đang bật (nhãn xanh), đang xả ắc quy/mất điện lưới (nút Input mờ đi, nhánh
Battery sáng vàng nhấp nháy), đang có dòng sạc từ Solar (nhánh Solar→Battery
chạy xanh). Không tính vào tổng công suất/điện năng mặt trời. Các trường live
hiển thị đầy đủ, nhưng **chỉ mục Load của UPS được lưu lịch sử** (qua
`/api/history`/`/api/chart`, xem README API bên dưới) — các trường còn lại
(Battery Charge, Runtime, Voltage...) chỉ xem live, không lưu lâu dài.

### Bảo vệ ổ đĩa/SSD: 2 tầng giảm tải, tách rời tần suất poll

Poll thiết bị vẫn mỗi `interval_seconds` (mặc định 2s) — số liệu live
(`/api/status`, trang dashboard) luôn tươi mới ở tần suất này vì lấy trực
tiếp từ bộ nhớ của Poller, không qua DB. Việc lưu xuống bảng dữ liệu thô
tách thành 2 tầng độc lập:

1. **Giảm khối lượng dữ liệu** — chỉ *lấy mẫu* để lưu 1 lần mỗi
   `raw_persist_interval_seconds` (mặc định 120s = 2 phút) cho mỗi thiết
   bị, thay vì mọi lần poll (giảm ~60 lần dung lượng tích luỹ: ≈10 MB thay
   vì ≈618 MB cho 30 ngày/thiết bị).
2. **Giảm tần suất ghi đĩa** — các mẫu ở bước 1 lại được đệm trong RAM và
   chỉ commit xuống đĩa theo lô mỗi `db_flush_interval_seconds` (mặc định
   600s = 10 phút), thay vì fsync ngay từng mẫu.

Đặt `storage_enabled = false` trong `[polling]` để **tắt hẳn** việc lưu
xuống đĩa (không ghi readings, không rollup, không cleanup) — chỉ hiển thị
số liệu live từ RAM, `/api/history`/`/api/chart` luôn trả rỗng. Phù hợp khi
đang thử nghiệm/đổi nguồn dữ liệu, chưa muốn DB tích luỹ. Bật lại bất kỳ lúc
nào bằng cách đổi về `true` rồi restart.

Kết hợp mặc định: mỗi thiết bị thực sự ghi đĩa ~1 lần/10 phút, gộp ~5 mẫu
mỗi lần. DB cũng bật sẵn `journal_mode=WAL`. Dừng êm (`./stop.sh`) luôn
flush hết phần đang đệm trước khi thoát — **không mất dữ liệu** khi
restart/deploy bình thường; chỉ mất tối đa `db_flush_interval_seconds` gần
nhất nếu tiến trình bị kill đột ngột (mất điện, `kill -9`). Không ảnh
hưởng biểu đồ giờ/ngày/tháng (đã có bảng tổng hợp riêng, tính lại mỗi 10
phút).

## Chạy

```bash
./start.sh   # chạy nền, PID lưu ở mqsolar.pid, log ở mqsolar.log
./stop.sh    # dừng êm (SIGTERM), tự kill -9 nếu sau 10s vẫn chưa thoát
```

Hoặc chạy foreground để xem log trực tiếp:

```bash
uv run python main.py
```

Mặc định server nghe ở `0.0.0.0:8000` (đổi trong `config.toml`).

## API

| Endpoint | Mô tả |
| --- | --- |
| `GET /health` | Kiểm tra service còn sống |
| `GET /api/status` | Dữ liệu mới nhất của tất cả thiết bị (`{device_id: data}`) |
| `GET /api/status/{device_id}` | Dữ liệu mới nhất của 1 thiết bị |
| `GET /api/devices` | Danh sách thiết bị đã từng ghi nhận (kèm `last_seen`) |
| `GET /api/history/{device_id}?since=&until=&limit=` | Lịch sử đọc (unix timestamp) |
| `GET /api/scan` | Quét mạng LAN nội bộ tìm thiết bị local (giống config flow HA) |
| `GET /docs` | Swagger UI (FastAPI) |

## Giao diện xem trực tiếp

| URL | Nội dung |
| --- | --- |
| `GET /` | Trang duy nhất: số liệu tổng hợp toàn hệ thống (tổng công suất, điện năng hôm nay/tích luỹ, số thiết bị online) + sơ đồ dòng điện UPS đầy đủ (Input/UPS/Solar/Output/Battery information), tự refresh mỗi 2 giây |

## Chạy nền bằng systemd

```bash
cp mqsolar.service.example mqsolar.service
# sửa User= và đường dẫn WorkingDirectory/ExecStart cho đúng máy bạn
sudo cp mqsolar.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now mqsolar
```

## Cấu trúc

```
app/
  config.py          # đọc config.toml
  mqsolar_client.py   # client giao tiếp thiết bị (local HTTP + cloud WebSocket)
  viewpower_client.py   # client server ViewPower (UPS Vertiv/Liebert)
  discovery.py        # quét mạng LAN tìm thiết bị local
  autoconfig.py        # quét mạng + tự ghi thiết bị mới vào config.toml (dùng bởi scan.sh)
  storage.py           # lưu/đọc lịch sử SQLite
  poller.py            # vòng lặp poll + giữ dữ liệu mới nhất trong bộ nhớ
  server.py            # FastAPI app / route REST + trang HTML
  static/
    index.html          # trang duy nhất: tổng hợp số liệu + sơ đồ dòng điện UPS
    icon.jpg              # logo (dùng lại từ custom_components/local_mqsolar/icon.png)
main.py                # entrypoint, khởi động poller + uvicorn, xử lý SIGINT/SIGTERM
start.sh / stop.sh      # chạy / dừng service nền (PID + log file)
scan.sh                 # quét mạng LAN, tự thêm thiết bị mới vào config.toml
```

`app/mqsolar_client.py` không phụ thuộc `homeassistant` — logic giống hệt
`custom_components/local_mqsolar/api.py` nên hành vi giao tiếp thiết bị nhất
quán giữa hai bản triển khai.
