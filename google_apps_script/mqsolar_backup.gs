/**
 * Sao lưu thống kê MPPT từ cloud Mạnh Quân Solar vào Google Drive (JSON), chạy
 * tự động trên máy chủ Google mỗi 4 giờ — không cần bật máy tính ở nhà.
 *
 * Chỉ gọi API cloud (HTTPS), KHÔNG đụng tới thiết bị MPPT.
 *
 * CÀI ĐẶT (làm 1 lần):
 *  1. script.google.com -> Dự án mới -> dán toàn bộ file này vào Code.gs.
 *  2. Cài đặt dự án (⚙) -> "Thuộc tính tập lệnh" -> Thêm thuộc tính:
 *       MQSOLAR_TOKEN = <token JWT đăng nhập tài khoản Mạnh Quân Solar>
 *     (KHÔNG viết token vào mã; nó chỉ nằm trong Script Properties.)
 *  3. Chọn hàm testConnection -> Chạy (cấp quyền Drive + gọi dịch vụ ngoài).
 *     Xem Nhật ký thực thi: phải thấy danh sách thiết bị. Nếu lỗi 403 / "error
 *     code: 1010" thì Cloudflare chặn máy chủ Google, cách này không dùng được.
 *  4. Chọn hàm installTrigger -> Chạy (tạo trigger mỗi 4 giờ). Chạy 1 lần thôi.
 *  5. (Tuỳ chọn) chạy backupMppt 1 lần để kéo toàn bộ lịch sử ngay.
 *
 * KẾT QUẢ: thư mục "Monitoring_Solar" trong Drive, mỗi thiết bị 1 file
 *   mqsolar_<deviceId>.json  =  { meta, hourly: {ts: dòng}, daily: {ts: dòng} }
 * Dòng giữ NGUYÊN số liệu cloud (không sửa). kWh của cloud tính theo phía PV;
 * điện thực vào ắc quy = kWh x meta.batterySideFactor (0.91, đo 2026-10-02).
 * Mỗi lần chạy chỉ kéo vài ngày gần nhất rồi gộp theo `ts` (không trùng lặp).
 *
 * WEB APP (cho app local đọc lại bản sao lưu): Triển khai -> Triển khai mới ->
 * loại "Ứng dụng web", Thực thi bằng: Tôi, Ai có quyền truy cập: Bất kỳ ai ->
 * sao chép URL (.../exec). App local gọi  <URL>?key=<WEBAPP_KEY>&device=<id>&since=<ISO>
 * (since tuỳ chọn, chỉ trả dòng từ mốc đó). Sai/thiếu key -> {ok:false}.
 * Mỗi lần sửa mã phải "Quản lý triển khai" -> chỉnh sửa -> Phiên bản mới.
 */

const API = 'https://api.manhquansolar.io.vn/api';
const FOLDER_NAME = 'Monitoring_Solar';
const FIRST_RUN_FROM = '2026-09-01T00:00:00Z'; // lần đầu: kéo từ ngày này
const LOOKBACK_DAYS = 3;                       // các lần sau: kéo lại 3 ngày gần nhất
const CHUNK_DAYS = 7;                          // chia nhỏ mỗi yêu cầu để không quá lớn
const BATTERY_SIDE_FACTOR = 0.91;
const MAX_ERROR_MAIL_INTERVAL_MS = 24 * 3600 * 1000; // báo lỗi qua email tối đa 1 lần/ngày
// Tuỳ chọn: dán token vào đây NẾU không muốn dùng Script Properties. CHỈ điền trong
// bản trên script.google.com, TUYỆT ĐỐI không điền vào file này trong repo / GitHub.
// Nếu đã đặt MQSOLAR_TOKEN trong Script Properties thì giá trị đó được ưu tiên.
const TOKEN_FALLBACK = '';
// Mã bí mật cho web app (doGet): app local phải gửi đúng mã này. Đặt trong Script
// Properties (WEBAPP_KEY) hoặc điền vào đây CHỈ trên bản script.google.com.
const WEBAPP_KEY_FALLBACK = '';
const HEADERS_BASE = { 'User-Agent': 'curl/8.5.0' };  // Cloudflare chặn UA mặc định (lỗi 1010)

/** Hàm chính — trigger gọi mỗi 4 giờ. */
function backupMppt() {
  try {
    const devices = listDevices_();
    if (!devices.length) throw new Error('Tài khoản không có thiết bị nào.');
    devices.forEach(backupDevice_);
  } catch (e) {
    console.error(e);
    notifyError_(e);
    throw e;
  }
}

function backupDevice_(dev) {
  const id = dev.device_uid;
  const type = statType_(dev);
  const file = getOrCreateFile_('mqsolar_' + id + '.json');
  const data = readJson_(file) || { meta: {}, hourly: {}, daily: {} };

  const now = new Date();
  const first = !data.meta.lastSync;
  const from = first ? new Date(FIRST_RUN_FROM)
                     : new Date(now.getTime() - LOOKBACK_DAYS * 86400 * 1000);
  const to = new Date(now.getTime() + 3600 * 1000);

  let hourly = 0, daily = 0;
  fetchRange_(id, type, '1h', from, to).forEach(function (r) { data.hourly[r.ts] = r; hourly++; });
  fetchRange_(id, type, '1d', from, to).forEach(function (r) { data.daily[r.ts] = r; daily++; });

  data.meta = {
    deviceId: id,
    deviceType: dev.device_type,
    model: dev.model,
    name: dev.device_name,
    batterySideFactor: BATTERY_SIDE_FACTOR,
    note: 'Số liệu nguyên bản từ cloud; kWh là phía PV, nhân batterySideFactor để ra điện vào ắc quy.',
    lastSync: now.toISOString(),
    hourlyCount: Object.keys(data.hourly).length,
    dailyCount: Object.keys(data.daily).length,
  };
  file.setContent(JSON.stringify(data));
  console.log(id + ': kéo ' + hourly + ' giờ / ' + daily + ' ngày; tổng lưu ' +
              data.meta.hourlyCount + ' giờ, ' + data.meta.dailyCount + ' ngày');
}

/** Cloud nhận type dạng "MPPT Charger" / "Grid-Tie Inverter" cho API thống kê. */
function statType_(dev) {
  return dev.device_type === 'MPPT' ? 'MPPT Charger' : 'Grid-Tie Inverter';
}

function fetchRange_(id, type, period, from, to) {
  const requests = [];
  for (let t = from.getTime(); t < to.getTime(); t += CHUNK_DAYS * 86400 * 1000) {
    const end = Math.min(t + CHUNK_DAYS * 86400 * 1000, to.getTime());
    const qs = 'period=' + period + '&type=' + encodeURIComponent(type) +
               '&from=' + encodeURIComponent(new Date(t).toISOString()) +
               '&to=' + encodeURIComponent(new Date(end).toISOString());
    requests.push({
      url: API + '/devices/statistic/' + encodeURIComponent(id) + '?' + qs,
      method: 'get', headers: authHeaders_(), muteHttpExceptions: true,
    });
  }
  const rows = [];
  UrlFetchApp.fetchAll(requests).forEach(function (resp) {
    const body = parseResponse_(resp);
    (body.data || []).forEach(function (r) { rows.push(r); });
  });
  return rows;
}

function listDevices_() {
  const resp = UrlFetchApp.fetch(API + '/devices/my',
    { method: 'get', headers: authHeaders_(), muteHttpExceptions: true });
  return parseResponse_(resp).data || [];
}

function authHeaders_() {
  const token = PropertiesService.getScriptProperties().getProperty('MQSOLAR_TOKEN') || TOKEN_FALLBACK;
  if (!token) throw new Error('Chưa có token: đặt MQSOLAR_TOKEN trong Script Properties hoặc điền TOKEN_FALLBACK.');
  return Object.assign({ 'x-access-token': token }, HEADERS_BASE);
}

function parseResponse_(resp) {
  const code = resp.getResponseCode();
  const text = resp.getContentText();
  let body;
  try { body = JSON.parse(text); } catch (e) { body = null; }
  if (code !== 200 || !body || !body.success) {
    throw new Error('Cloud trả HTTP ' + code + ': ' + text.substring(0, 150) +
      (code === 401 ? ' (token hết hạn hoặc sai)' : '') +
      (text.indexOf('1010') >= 0 ? ' (Cloudflare chặn)' : ''));
  }
  return body;
}

function getOrCreateFile_(name) {
  const folders = DriveApp.getFoldersByName(FOLDER_NAME);
  const folder = folders.hasNext() ? folders.next() : DriveApp.createFolder(FOLDER_NAME);
  const files = folder.getFilesByName(name);
  return files.hasNext() ? files.next() : folder.createFile(name, '{}', MimeType.PLAIN_TEXT);
}

function readJson_(file) {
  try {
    const obj = JSON.parse(file.getBlob().getDataAsString());
    return obj && obj.hourly ? obj : null;
  } catch (e) { return null; }
}

/** Gửi email báo lỗi cho chủ script, tối đa 1 lần/ngày để không spam. */
function notifyError_(err) {
  const props = PropertiesService.getScriptProperties();
  const last = Number(props.getProperty('LAST_ERROR_MAIL') || 0);
  if (Date.now() - last < MAX_ERROR_MAIL_INTERVAL_MS) return;
  try {
    MailApp.sendEmail(Session.getEffectiveUser().getEmail(),
      'MQSolar Backup: lỗi sao lưu', String(err && err.message ? err.message : err));
    props.setProperty('LAST_ERROR_MAIL', String(Date.now()));
  } catch (e) { console.error('Không gửi được email báo lỗi: ' + e); }
}

/** Chạy trước để kiểm tra token và kết nối (không ghi gì vào Drive). */
function testConnection() {
  const devices = listDevices_();
  console.log('Kết nối OK. Thiết bị: ' + devices.map(function (d) {
    return d.device_uid + ' (' + d.device_name + ', ' + d.status + ')';
  }).join('; '));
}

/** Tạo trigger mỗi 4 giờ (xoá trigger cũ của backupMppt để không bị nhân đôi). */
function installTrigger() {
  ScriptApp.getProjectTriggers().forEach(function (t) {
    if (t.getHandlerFunction() === 'backupMppt') ScriptApp.deleteTrigger(t);
  });
  ScriptApp.newTrigger('backupMppt').timeBased().everyHours(4).create();
  console.log('Đã tạo trigger: backupMppt mỗi 4 giờ.');
}

/** Web app: trả JSON bản sao lưu cho app local (bảo vệ bằng mã bí mật WEBAPP_KEY). */
function doGet(e) {
  const p = (e && e.parameter) || {};
  const key = PropertiesService.getScriptProperties().getProperty('WEBAPP_KEY') || WEBAPP_KEY_FALLBACK;
  if (!key || p.key !== key) return jsonOut_({ ok: false, error: 'unauthorized' });
  try {
    const folders = DriveApp.getFoldersByName(FOLDER_NAME);
    if (!folders.hasNext()) return jsonOut_({ ok: false, error: 'folder not found' });
    const folder = folders.next();
    let file = null;
    if (p.device) {
      const it = folder.getFilesByName('mqsolar_' + p.device + '.json');
      if (it.hasNext()) file = it.next();
    } else {
      const it = folder.getFiles();
      while (it.hasNext()) {
        const f = it.next();
        if (/^mqsolar_.*\.json$/.test(f.getName())) { file = f; break; }
      }
    }
    const data = file && readJson_(file);
    if (!data) return jsonOut_({ ok: false, error: 'backup file not found' });
    const since = p.since ? new Date(p.since).getTime() : 0;
    const pick = function (obj) {
      const out = {};
      Object.keys(obj || {}).forEach(function (ts) {
        if (!since || new Date(ts).getTime() >= since) out[ts] = obj[ts];
      });
      return out;
    };
    return jsonOut_({ ok: true, meta: data.meta, hourly: pick(data.hourly), daily: pick(data.daily) });
  } catch (err) {
    return jsonOut_({ ok: false, error: String(err && err.message ? err.message : err) });
  }
}

function jsonOut_(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON);
}
