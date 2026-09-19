/**
 * AMS 解析網頁 — 登入驗證與登入紀錄（Google Apps Script 網頁應用程式）
 *
 * 綁定的試算表：「物料管理系統 的副本」（或任何含 Users 分頁的試算表）
 *   Users 分頁：EMPLOYEE_ID、EMPLOYEE_NAME（以表頭名稱尋找，找不到才用 A/B 欄）
 *   AMS_Log 分頁：自動建立；7 欄 Timestamp | EmployeeID | EmployeeName | ActionType | Site | Page | UserAgent
 *     ActionType：LOGIN（登入成功）、LOGIN_FAIL（代號不在清單）、LOGIN_BLOCKED（10 分鐘內失敗過多，只記第一次）、
 *                 VISIT（沿用工作階段再次開站）、LOGOUT（登出；只記有效工作階段）
 *   Timestamp 依「試算表」的時區顯示：檔案 → 設定 → 時區 請設為 (GMT+08:00) 台北。
 *
 * 部署：擴充功能 → Apps Script → 貼上本檔 → 部署 → 新增部署作業 → 類型「網頁應用程式」
 *       執行身分「我」、誰可以存取「所有人」→ 部署 → 複製「網頁應用程式網址」(…/exec) → 填到 docs/auth-config.json 的 endpoint
 *       之後改程式碼要「管理部署作業 → 編輯 → 版本：新版本」才會生效。
 * 前端以 Content-Type: text/plain 送 JSON（避免 CORS preflight）；本函式一律回 JSON。
 * 安全：所有寫入紀錄的字串都經過 cell_()（去掉公式前綴），AMS_Log 的文字欄設為純文字格式，避免試算表公式注入。
 */
var USERS_SHEET = 'Users';
var LOG_SHEET = 'AMS_Log';
var SESSION_HOURS = 12;          // 工作階段有效時數
var MAX_FAILS_PER_10MIN = 20;    // 同一代號 10 分鐘內失敗次數上限（CacheService 計數）

function doGet(e) {
  try { readUsers_(); return json_({ ok: true, service: 'ams-auth' }); }
  catch (err) { console.error(err); return json_({ ok: false, service: 'ams-auth', error: '設定不完整（找不到 Users 分頁？）' }); }
}

function doPost(e) {
  var body = {};
  try { body = JSON.parse((e && e.postData && e.postData.contents) || '{}'); } catch (err) { return json_({ ok: false, error: '請求格式錯誤' }); }
  var action = String(body.action || '');
  var site = clip_(body.site, 60), page = clip_(body.page, 200), ua = clip_(body.ua, 200);
  var id = clip_(norm_(body.id), 20), name = clip_(norm_(body.name), 40);
  try {
    if (action === 'login') {
      if (!id) return json_({ ok: false, error: '請輸入員工代號。' });
      if (failCount_(id) >= MAX_FAILS_PER_10MIN) {
        if (failCount_(id) === MAX_FAILS_PER_10MIN) { log_(id, name, 'LOGIN_BLOCKED', site, page, ua); bumpFail_(id); }
        return json_({ ok: false, error: '嘗試次數過多，請 10 分鐘後再試。' });
      }
      var u = findUserById_(id); // 只憑員工代號；姓名由 Users 分頁帶出
      if (!u) { bumpFail_(id); log_(id, name, 'LOGIN_FAIL', site, page, ua); return json_({ ok: false, error: '員工代號不在使用者清單中，請再試一次。' }); }
      var exp = Date.now() + SESSION_HOURS * 3600 * 1000;
      var token = sign_(u.id, exp);
      log_(u.id, u.name, 'LOGIN', site, page, ua);
      return json_({ ok: true, id: u.id, name: u.name, token: token, exp: exp });
    }
    if (action === 'resume') {
      var v = verify_(id, String(body.token || ''));
      if (!v.ok) return json_({ ok: false, error: v.error });
      var u2 = findUserById_(id);
      if (!u2) return json_({ ok: false, error: '此代號已不在使用者清單中。' });
      log_(u2.id, u2.name, 'VISIT', site, page, ua);
      return json_({ ok: true, id: u2.id, name: u2.name, exp: v.exp });
    }
    if (action === 'logout') {
      var v2 = verify_(id, String(body.token || ''));
      if (v2.ok) { var u3 = findUserById_(id); log_(id, u3 ? u3.name : '', 'LOGOUT', site, page, ua); }
      return json_({ ok: true });
    }
    return json_({ ok: false, error: '未知的動作' });
  } catch (err) {
    console.error(err);
    // transient:true → 前端視同「端點暫時無法服務」（沿用快取工作階段），不會把使用者踢出
    return json_({ ok: false, transient: true, error: '伺服器暫時無法服務，請稍後再試。' });
  }
}

/* ---------------- 使用者 ---------------- */
function readUsers_() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sh = ss.getSheetByName(USERS_SHEET);
  if (!sh) throw new Error('找不到分頁 ' + USERS_SHEET);
  var vals = sh.getDataRange().getDisplayValues(); // 顯示值：保留前導零、避免數字型儲存格
  if (!vals.length) return [];
  var hdr = vals[0].map(function (h) { return norm_(h).toUpperCase(); });
  var ci = hdr.indexOf('EMPLOYEE_ID'), cn = hdr.indexOf('EMPLOYEE_NAME');
  if (ci < 0) ci = 0;
  if (cn < 0) cn = 1;
  var out = [];
  for (var i = 1; i < vals.length; i++) {
    var id = canon_(vals[i][ci]), raw = String(vals[i][cn] == null ? '' : vals[i][cn]).trim();
    if (id && raw) out.push({ id: id, name: raw, key: nameKey_(raw) });
  }
  return out;
}
function findUser_(id, name) {
  var us = readUsers_(), cid = canon_(id), k = nameKey_(name);
  for (var i = 0; i < us.length; i++) if (us[i].id === cid && us[i].key === k) return us[i];
  return null;
}
function findUserById_(id) {
  var us = readUsers_(), cid = canon_(id);
  for (var i = 0; i < us.length; i++) if (us[i].id === cid) return us[i];
  return null;
}

/* ---------------- 紀錄 ---------------- */
function logSheet_() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sh = ss.getSheetByName(LOG_SHEET);
  if (!sh) {
    sh = ss.insertSheet(LOG_SHEET);
    sh.appendRow(['Timestamp', 'EmployeeID', 'EmployeeName', 'ActionType', 'Site', 'Page', 'UserAgent']);
    sh.setFrozenRows(1);
    sh.getRange('A:A').setNumberFormat('yyyy-mm-dd hh:mm:ss');
    sh.getRange('B:G').setNumberFormat('@'); // 純文字：不解讀公式、保留前導零
  }
  return sh;
}
function log_(id, name, action, site, page, ua) {
  var lock = LockService.getScriptLock();
  try { lock.waitLock(5000); } catch (e) { /* 拿不到鎖也照寫 */ }
  try {
    logSheet_().appendRow([new Date(), cell_(id), cell_(name), action, cell_(site), cell_(page), cell_(ua)]);
  } finally { try { lock.releaseLock(); } catch (e) { /* ignore */ } }
}
function failCount_(id) { return Number(CacheService.getScriptCache().get('fail:' + canon_(id)) || 0); }
function bumpFail_(id) { var c = CacheService.getScriptCache(), k = 'fail:' + canon_(id); c.put(k, String(Number(c.get(k) || 0) + 1), 600); }

/* ---------------- 工作階段 token（HMAC-SHA256，密鑰存在指令碼屬性） ---------------- */
function secret_() {
  var p = PropertiesService.getScriptProperties();
  var s = p.getProperty('AUTH_SECRET');
  if (s) return s;
  var lock = LockService.getScriptLock();
  try { lock.waitLock(5000); } catch (e) { /* ignore */ }
  try {
    s = p.getProperty('AUTH_SECRET');
    if (!s) { s = Utilities.getUuid() + Utilities.getUuid(); p.setProperty('AUTH_SECRET', s); }
  } finally { try { lock.releaseLock(); } catch (e) { /* ignore */ } }
  return s;
}
function hmac_(msg) {
  var raw = Utilities.computeHmacSha256Signature(msg, secret_()); // (value, key) → byte[]
  return Utilities.base64EncodeWebSafe(raw).replace(/=+$/, '');
}
function sign_(id, exp) { return exp + '.' + hmac_(canon_(id) + '|' + exp); }
function verify_(id, token) {
  var m = /^(\d+)\.([A-Za-z0-9_-]+)$/.exec(token || '');
  if (!id || !m) return { ok: false, error: '工作階段無效，請重新登入。' };
  var exp = Number(m[1]);
  if (!(exp > Date.now())) return { ok: false, error: '工作階段已逾期，請重新登入。' };
  if (hmac_(canon_(id) + '|' + exp) !== m[2]) return { ok: false, error: '工作階段無效，請重新登入。' };
  return { ok: true, exp: exp };
}

/* ---------------- 工具 ---------------- */
function norm_(s) { s = String(s == null ? '' : s); try { s = s.normalize('NFKC'); } catch (e) { /* ignore */ } return s.replace(/\s+/g, '').trim(); }
function canon_(s) { return norm_(s).replace(/^0+(?=\d)/, ''); }          // 員工代號：去空白、全形轉半形、去前導零後比對
function nameKey_(s) { return norm_(s).toUpperCase(); }                     // 姓名：去空白、不分大小寫
function clip_(s, n) { return String(s == null ? '' : s).slice(0, n); }
function cell_(s) { s = String(s == null ? '' : s); return /^[=+\-@\t\r]/.test(s) ? "'" + s : s; } // 防公式注入
function json_(o) { return ContentService.createTextOutput(JSON.stringify(o)).setMimeType(ContentService.MimeType.JSON); }
