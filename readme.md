# Mạnh Quân Solar ⚡

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg?style=for-the-badge)](https://github.com/hacs/integration)
[![GitHub Release](https://img.shields.io/github/v/release/loctrantv/ha_local_mqsolar?style=for-the-badge)](https://github.com/loctrantv/ha_local_mqsolar/releases)

Đây là Integration chính thức dành cho Home Assistant, hỗ trợ các bị Mạch Sạc MPPT và Thiết bị Hoà Lưới (Inverter) của thương hiệu **Mạnh Quân Solar**. Hỗ trợ cả kết nối trực tiếp **Local (mạng nội bộ)** và kết nối qua **Cloud** khi bạn ở xa.

Chỉ mất vài giây cài đặt để kéo toàn bộ thông số, biểu đồ, lượng điện vôn/ampe và số liệu KWh vào trực tiếp bảng điều khiển Home Assistant tốc độ làm mới liên tục **mỗi 1 giây**.

## ✨ Tính năng chính

- 🚀 **Cực nhanh, cập nhật từng giây:** Dữ liệu kéo về theo thời gian thực (1000ms delay). Không phụ thuộc mảng Cloud trung gian, không cần có mạng Internet để xem hệ thống ở nhà.
- 🔍 **Quét mạng Local:** Không cần biết IP thiết bị, plugin tích hợp sẵn trình quét Asynchronous, mò tìm tự động thiết bị hợp lệ trong toàn mạng với tốc độ chớp mắt.
- ⚡ **Tự nhận dạng phần cứng:** Tự động phát hiện phiên bản mạch là "MPPT Charger" hay "Gtil Inverter" để sinh ra bảng cảm biến tương ứng.
- ☁️ **Hỗ trợ Cloud:** Cho phép kết nối và giám sát thiết bị từ xa qua Internet bằng API Token.
- 🔄 **Quản lý đa thiết bị:** Một cấu hình Cloud có thể quản lý đồng thời nhiều thiết bị MPPT và Inverter cùng lúc.
- 📊 **Cấu trúc chuẩn Home Assistant Energy:** Trả về entity chuẩn định dạng `total_increasing` cho KWH, dễ dàng tích hợp trực tiếp vào báo cáo năng lượng của gia đình.

---

## 🛠️ Cài đặt trực tiếp qua HACS (Khuyến nghị)

Bằng cách cài thông qua [HACS](https://hacs.xyz/), bạn sẽ luôn tự động nhận được bản báo cập nhật sửa lỗi/tính năng mới nhất.

1. Bật **HACS** trong menu Home Assistant.
2. Chọn mục **Integrations**.
3. Bấm vào nút `3 chấm vạch dọc` ở góc trên bên phải màn hình và chọn **Custom repositories** (Kho lưu trữ tùy chỉnh).
4. Dán URL bên dưới vào ô Repository:
   > `https://github.com/loctrantv/ha_local_mqsolar`
5. Chọn Category/Danh mục là **Integration**. Bấm **ADD**.
6. Gõ chữ "Mạnh Quân" vào thanh tìm kiếm của HACS và tiến hành bấm **Download** tải về bản mới nhất.
7. Bạn cần **Khởi động lại (Restart)** Home Assistant để hệ thống nạp mã thư viện mới.

## 🗂️ Cài đặt thủ công
1. Tải về gói `.zip` mã nguồn hoặc clone repository này.
2. Copy toàn bộ thư mục `custom_components/local_mqsolar` vào bên trong thư mục `custom_components` của Home Assistant nhà bạn. (Đường dẫn cuối cùng sẽ ở dạng: `/config/custom_components/local_mqsolar`)
3. Khởi động lại Home Assistant.

---

## ⚙️ Hướng dẫn Khởi chạy (Config Flow)

1. Mở Home Assistant, truy cập vào menu **Cài đặt -> Thiết bị & Dịch vụ** (Settings -> Devices & Services).
2. Bấm vào nút **+ Thêm tích hợp** (+ Add Integration) ở góc dưới cùng bên phải.
3. Tìm cụm từ `Mạnh Quân Solar`.
4. Màn hình tuỳ chọn sẽ hiện ra cho 3 lựa chọn: 
   - **Quét mạng nội bộ (Scan local network):** Bấm vào mục này, plugin sẽ tự động lùng sục IP để tìm mạch trong tối đa 1 giây và hiển thị kết quả cho bạn chọn. (Hỗ trợ cấu hình Cắm chạy Plug & Play).
   - **Nhập IP thủ công:** Nếu bạn biết được IP của thiết bị được cấp phép từ Router, hoặc thiêt bị nằm trong dải VPN mạng khác, bạn có thể tự nhập IP vô đây.
   - **Kết nối qua Cloud:** Dành cho trường hợp bạn muốn quản lý thiết bị từ xa hoặc không cùng mạng nội bộ. Bạn sẽ cần nhập `API Token` và danh sách các `Device ID` muốn theo dõi.
     - **Cách lấy Token:** Truy cập vào ứng dụng **Mạnh Quân Solar** (Android/iOS) hoặc trang web [manhquansolar.io.vn](https://manhquansolar.io.vn), vào menu **Cá nhân (Profile) -> Kết nối bên thứ 3**. Tại đây bạn có thể tạo và sao chép mã Token để dán vào Home Assistant.
   
*Lưu ý: Bạn nên cấu hình IP tĩnh mạng WLAN (Static IP/DHCP Binding) trên Router cho Module WiFi để tránh bị nhảy thay đổi kết nối mỗi khi cúp điện.*

---

## 📊 Danh sách các Cảm Biến Cung Cấp (Sensors)

### Đối với Mạch Sạc MPPT
| Giá trị hiển thị | Đơn vị tính | Device Class |
| :--- | :--- | :--- |
| PV Voltage (Điện áp dàn pin) | `V` | `voltage` |
| PV Current (Dòng tải dàn pin) | `A` | `current` |
| Battery Voltage (Điện áp ắc quy) | `V` | `voltage` |
| Battery Current (Dòng sạc ắc quy) | `A` | `current` |
| Charging Power (Công suất sạc) | `W` | `power` |
| Energy Today (Đã sạc hôm nay) | `kWh` | `energy` |
| Energy Total (Tổng đo sạc tích lũy) | `kWh` | `energy` |
| Nhiệt độ sạc | `°C` | `temperature` |
| Trạng thái sạc (VD: IDLE, CHARGING) | `Text` | `None` |

### Đối với Inverter Hòa Lưới
| Giá trị hiển thị | Đơn vị tính | Device Class |
| :--- | :--- | :--- |
| DC Voltage (Điện áp DC đầu vào) | `V` | `voltage` |
| AC Voltage (Điện áp lưới) | `V` | `voltage` |
| Output Power (Công suất hòa lưới) | `W` | `power` |
| Grid Power (Công suất lấy lưới) | `W` | `power` |
| Grid Today (Lấy lưới hôm nay) | `kWh` | `energy` |
| Grid Total (Tổng lấy lưới tích luỹ) | `kWh` | `energy` |
| Temperature (Nhiệt độ) | `°C` | `temperature` |
| Energy Today (Hòa lưới hôm nay) | `kWh` | `energy` |
| Energy Total (Tổng hòa lưới tích luỹ) | `kWh` | `energy` |
| Trạng thái Inverter | `Text` | `None` |

---

## Môi trường cấu hình yêu cầu
- `Home Assistant Core` `>= v2023.11.0`
- `HACS` `>= 1.34.0`

## Hỗ trợ & Khắc phục
Mọi ý kiến đóng góp cũng như các vấn đề phát sinh lỗi vặt cần giải đáp, vui lòng tạo Request ở trang Tab Issues.
Rất nhiều tính năng hỗ trợ, đóng góp code từ phía người dùng đều được hoan nghênh nồng nhiệt!

© Copyright 2026. Made with ❤️ by fb.com/loctrantv for Mạnh Quân Solar Ecosystem.
