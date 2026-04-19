/**
 * D-CORE Dataset Download Survey Collector
 * Google Apps Script — 将前端提交的问卷写入 Google Sheet
 *
 * ── 部署步骤 ──────────────────────────────────────────────────────────────────
 * 1. 新建一个 Google Sheet，第一行手动填写表头（见 HEADERS 常量）
 * 2. 打开 扩展程序 → Apps Script，把本文件内容粘贴进去
 * 3. 修改下方 SHEET_NAME 为你的 Sheet 名称（默认 "Sheet1"）
 * 4. 点击 部署 → 新增部署 → 类型选"网络应用"
 *    - 执行身份：选"我"
 *    - 访问权限：选"所有人"（包括匿名）
 * 5. 复制部署后的 Web 应用网址
 * 6. 在 s04_web_visualization/.env.local 中添加：
 *    VITE_SURVEY_ENDPOINT=<刚才复制的 URL>
 * 7. 重启前端开发服务器
 * ─────────────────────────────────────────────────────────────────────────────
 */

const SHEET_NAME = "Sheet1";

const HEADERS = [
  "Submitted At",
  "Email",
  "Name",
  "Organization",
  "Org Type",
  "Country",
  "Use Case",
  "Wants Updates",
  "Record Count",
];

function doPost(e) {
  try {
    const ss = SpreadsheetApp.getActiveSpreadsheet();
    const sheet = ss.getSheetByName(SHEET_NAME);

    // 首次运行时自动写入表头
    if (sheet.getLastRow() === 0) {
      sheet.appendRow(HEADERS);
    }

    const p = e.parameter;
    sheet.appendRow([
      p.submitted_at  || new Date().toISOString(),
      p.email         || "",
      p.name          || "",
      p.organization  || "",
      p.org_type      || "",
      p.country       || "",
      p.use_case      || "",
      p.wants_updates || "",
      p.record_count  || "",
    ]);

    return ContentService
      .createTextOutput(JSON.stringify({ status: "ok" }))
      .setMimeType(ContentService.MimeType.JSON);

  } catch (err) {
    return ContentService
      .createTextOutput(JSON.stringify({ status: "error", message: String(err) }))
      .setMimeType(ContentService.MimeType.JSON);
  }
}

// GET 请求返回简单状态，方便测试部署是否成功
function doGet() {
  return ContentService
    .createTextOutput(JSON.stringify({ status: "ok", message: "D-CORE survey endpoint is live." }))
    .setMimeType(ContentService.MimeType.JSON);
}
