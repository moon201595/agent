/**
 * paper-harness 반응 버튼 웹앱 (Google Apps Script) — 2026-09-15 (2026-09-16: mode=json 출구 — 닫히는 페이지 web/reaction 이 부른다)
 *
 * 하는 일은 둘뿐이다.
 *  1) 메일의 버튼 링크(?t=토큰)를 받으면 서명·만료를 확인하고 시트에 [받은 시각, 토큰] 한 줄을 적은 뒤 글자만 있는 화면을 보여준다.
 *  2) 매일 새벽 paper-harness 가 서명된 요청(?mode=export&since=&exp=&sig=)으로 새 줄을 가져간다.
 *
 * 보안 원칙(paper-harness feedback_links.py 와 같은 설계):
 *  - 시트에는 무작위 tid 가 든 토큰과 시각만 적는다. 논문·수신자 정보는 없다.
 *  - 비밀키는 코드에 쓰지 않는다. 스크립트 속성 FEEDBACK_HMAC_SECRET 에만 둔다.
 *  - 외부로 요청을 보내지 않는다(UrlFetchApp 을 쓰지 않는다). 응답 화면에 입력칸·외부 링크를 넣지 않는다.
 *  - 이 스크립트에 붙은 시트 하나만 쓴다(appsscript.json 의 oauthScopes 참고).
 */

var SHEET_NAME = 'events';
var TOKEN_VERSION = 'v1';
var ACTIONS = { more: '더 보고 싶음', useful: '유용함', out: '관심 밖', undo: '취소' };

function secret_() {
  var s = PropertiesService.getScriptProperties().getProperty('FEEDBACK_HMAC_SECRET');
  if (!s || s.length < 32) throw new Error('FEEDBACK_HMAC_SECRET 미설정');
  return s;
}

function sign_(payload) {
  var bytes = Utilities.computeHmacSha256Signature(payload, secret_());
  return Utilities.base64EncodeWebSafe(bytes).replace(/=+$/, '');
}

function safeEqual_(a, b) {
  if (typeof a !== 'string' || typeof b !== 'string' || a.length !== b.length) return false;
  var diff = 0;
  for (var i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

function sheet_() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sh = ss.getSheetByName(SHEET_NAME);
  if (!sh) {
    sh = ss.insertSheet(SHEET_NAME);
    sh.appendRow(['received_at', 'token']);
  }
  return sh;
}

function page_(message, undoUrl) {
  // 2026-09-16 사용자 요청: "버튼 누르면 끝이었으면 좋겠다". 메일 규격상 새 탭이 열리는 것 자체는 막을 수 없다.
  // 처음엔 페이지가 열리자마자 스스로 닫게 했는데 **안 닫혔다**(실측, 웹메일). 이유: Apps Script 는 이 HTML 을 구글의 샌드박스 iframe 안에
  // 띄우고, 그 iframe 의 권한이 allow-top-navigation-by-user-activation 뿐이라 **사용자가 이 페이지 안에서 직접 클릭했을 때만** 바깥 탭을
  // 닫거나 옮길 수 있다. 메일에서의 클릭은 다른 탭의 일이라 여기로 넘어오지 않는다. 그래서 큰 "탭 닫기" 버튼 하나를 두고(웹메일에서
  // 연 탭은 닫힌다), 자동 시도는 혹시 허용하는 환경을 위해 남겨 둔다. 닫기가 막히면 그 자리에 Ctrl+W 안내를 보인다.
  var t = HtmlService.createTemplate(
    '<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1">' +
    '<div style="font-family:sans-serif;max-width:420px;margin:48px auto;text-align:center;color:#111">' +
    '<p style="font-size:17px;margin:0 0 20px"><?= message ?></p>' +
    '<button id="close" style="font-size:16px;padding:12px 28px;border:0;border-radius:8px;background:#3B5BDB;color:#fff;cursor:pointer">탭 닫기</button>' +
    '<p id="hint" style="display:none;font-size:13px;color:#555;margin-top:12px">브라우저가 닫기를 막았습니다 — Ctrl+W(맥은 ⌘W)로 닫아 주세요.</p>' +
    '<? if (undoUrl) { ?><p style="font-size:13px;color:#555;margin-top:24px">잘못 눌렀다면 <a href="<?= undoUrl ?>">취소</a></p><? } ?>' +
    '<p style="font-size:12px;color:#888">paper-harness 연구 동향 브리핑</p></div>' +
    '<script>' +
    'function shut(){' +
    '  try { window.top.close(); } catch (e) {}' +
    '  try { window.close(); } catch (e) {}' +
    '}' +
    'document.getElementById("close").onclick = function () {' +
    '  shut(); setTimeout(function(){ document.getElementById("hint").style.display = "block"; }, 300);' +
    '};' +
    'shut();' +
    '</script>');
  t.message = message;       // <?= ?> 는 HTML 이스케이프된다
  t.undoUrl = undoUrl || '';
  return t.evaluate().setTitle('반응 기록').setXFrameOptionsMode(HtmlService.XFrameOptionsMode.ALLOWALL);
}

function json_(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON);
}

function doGet(e) {
  var p = (e && e.parameter) || {};
  var asJson = p.mode === 'json';     // 닫히는 페이지(web/reaction)가 부르는 경로 — 화면 대신 결과만 돌려준다(2026-09-16)
  try {
    if (p.mode === 'export') return export_(p);
    var r = record_(String(p.t || ''));
    if (asJson) return json_({ ok: r.ok, message: r.message, undo: r.undoToken || '' });
    var undoUrl = r.undoToken ? ScriptApp.getService().getUrl() + '?t=' + encodeURIComponent(r.undoToken) : '';
    return page_(r.message, undoUrl);
  } catch (err) {
    if (p.mode === 'export') return json_({ error: 'server' });
    return asJson ? json_({ ok: false, message: '잠시 후 다시 눌러 주세요.', undo: '' }) : page_('잠시 후 다시 눌러 주세요.');
  }
}

// 토큰을 검증하고 시트에 남긴다. returns {ok, message, undoToken} — 화면(page_)과 JSON(json_) 두 출구가 같은 판정을 쓴다.
function record_(token) {
  var bad = { ok: false, message: '링크가 올바르지 않습니다.', undoToken: '' };
  var parts = token.split('.');
  if (parts.length !== 5 || parts[0] !== TOKEN_VERSION) return bad;
  var payload = parts.slice(0, 4).join('.');
  if (!safeEqual_(sign_(payload), parts[4])) return bad;
  if (!ACTIONS.hasOwnProperty(parts[2])) return bad;
  if (Number(parts[3]) < Date.now() / 1000) return { ok: false, message: '기간이 지난 링크입니다.', undoToken: '' };

  var lock = LockService.getScriptLock();
  lock.waitLock(5000);
  try {
    // 앞의 ' 는 시트가 ISO 시각 문자열을 날짜 형식으로 바꾸지 못하게 한다 — 바뀌면 export 의 문자열 비교가 깨진다.
    sheet_().appendRow(["'" + new Date().toISOString(), "'" + token]);
  } finally {
    lock.releaseLock();
  }

  if (parts[2] === 'undo') return { ok: true, message: '취소했어요.', undoToken: '' };
  var undoPayload = [TOKEN_VERSION, parts[1], 'undo', parts[3]].join('.');
  return { ok: true, message: '"' + ACTIONS[parts[2]] + '" 으로 기록했어요. 고맙습니다.', undoToken: undoPayload + '.' + sign_(undoPayload) };
}

function export_(p) {
  var since = String(p.since || '');
  var exp = String(p.exp || '');
  if (!safeEqual_(sign_('export.' + since + '.' + exp), String(p.sig || ''))) return json_({ error: 'forbidden' });
  if (Number(exp) < Date.now() / 1000) return json_({ error: 'expired' });
  var values = sheet_().getDataRange().getValues();
  var rows = [];
  for (var i = 1; i < values.length; i++) {
    var receivedAt = String(values[i][0]);
    if (receivedAt > since) rows.push({ received_at: receivedAt, token: String(values[i][1]) });
  }
  return json_({ rows: rows });
}
