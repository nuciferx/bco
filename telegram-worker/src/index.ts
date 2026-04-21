export interface Env {
  TELEGRAM_BOT_TOKEN: string;
  TELEGRAM_CHAT_ID?: string;
  TELEGRAM_WEBHOOK_SECRET?: string;
  BCO_LOGIN_MODE?: string;
  BCO_USERNAME?: string;
  BCO_PASSWORD?: string;
  BCO_TOTP_SECRET?: string;
  BCO_OTP_CODE?: string;
  BCO_ACCESS_TOKEN?: string;
  BCO_REFRESH_TOKEN?: string;
  REPORT_TIMEZONE?: string;
  DAILY_REPORT_HOUR?: string;
  BCO_BOT_KV: KVNamespace;
}

type Dict = Record<string, unknown>;

interface TokenData {
  accessToken: string;
  refreshToken: string;
  exp?: number | null;
  fetchedAt?: number;
}

interface TelegramMessage {
  message_id: number;
  text?: string;
  chat?: { id: number; type?: string };
}

interface TelegramCallbackQuery {
  id: string;
  data?: string;
  message?: TelegramMessage;
}

interface TelegramUpdate {
  update_id: number;
  message?: TelegramMessage;
  callback_query?: TelegramCallbackQuery;
}

interface BcoOfficerRow {
  id: number;
  name: string;
  role: string;
  total: number;
  overdue: number;
  critical: number;
  near: number;
  status?: unknown;
  username?: string;
}

const BCO_API_BASE = "https://bco-api.bangkok.go.th/api/v1";
const ALLOWED_ROLES = new Set(["วิศวกร", "นายตรวจ"]);
const R1_ATTACHMENT_DOC_ID = 69;
const R1_ATTACHMENT_SEQ = 4;
const R1_ATTACHMENT_NAME = "สำเนาภาพถ่ายใบรับรองการตรวจสอบอาคารฉบับล่าสุด (ร.1)";
const R1_FILE_NAME_HINTS = ["ร1", "ร.1", "ใบร.1", "ใบรับรอง", "ตรวจสอบอาคาร"];
const KV_TOKEN_KEY = "bco:tokens";
const KV_AUTH_ALERT_KEY = "bco:auth_alert_active";
const KV_RUNTIME_OTP_KEY = "bco:runtime_otp";
const MAP_DOC_HINTS = ["แผนที่ที่ตั้งอาคาร", "แผนที่", "ป้าย"];
const BUILDING_DOC_HINTS = ["ภาพถ่ายหน้าอาคาร", "ภาพถ่ายอาคาร", "รูปอาคาร", "หน้าอาคาร"];
const TASKS_PAGE_SIZE = 8;

function telegramRequest(token: string, method: string, body: Record<string, unknown>): Promise<Response> {
  return fetch(`https://api.telegram.org/bot${token}/${method}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

async function sendMessage(
  env: Env,
  chatId: number | string,
  text: string,
  extra: Record<string, unknown> = {},
): Promise<void> {
  const resp = await telegramRequest(env.TELEGRAM_BOT_TOKEN, "sendMessage", {
    chat_id: chatId,
    text,
    ...extra,
  });
  if (!resp.ok) {
    throw new Error(`Telegram sendMessage failed: HTTP ${resp.status}`);
  }
}

async function editMessageText(
  env: Env,
  chatId: number | string,
  messageId: number,
  text: string,
  extra: Record<string, unknown> = {},
): Promise<void> {
  const resp = await telegramRequest(env.TELEGRAM_BOT_TOKEN, "editMessageText", {
    chat_id: chatId,
    message_id: messageId,
    text,
    ...extra,
  });
  if (!resp.ok) {
    throw new Error(`Telegram editMessageText failed: HTTP ${resp.status}`);
  }
}

async function answerCallbackQuery(env: Env, callbackQueryId: string, text = ""): Promise<void> {
  const body: Record<string, unknown> = { callback_query_id: callbackQueryId };
  if (text) body.text = text;
  const resp = await telegramRequest(env.TELEGRAM_BOT_TOKEN, "answerCallbackQuery", body);
  if (!resp.ok) {
    throw new Error(`Telegram answerCallbackQuery failed: HTTP ${resp.status}`);
  }
}

async function sendBinary(
  env: Env,
  method: "sendDocument" | "sendPhoto",
  chatId: number | string,
  fileName: string,
  bytes: ArrayBuffer | Uint8Array,
  caption: string,
): Promise<void> {
  const form = new FormData();
  form.set("chat_id", String(chatId));
  form.set("caption", caption);
  form.set(method === "sendPhoto" ? "photo" : "document", new File([bytes], fileName));
  const resp = await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/${method}`, {
    method: "POST",
    body: form,
  });
  if (!resp.ok) {
    throw new Error(`Telegram ${method} failed: HTTP ${resp.status}`);
  }
}

function decodeJwtExp(token: string | undefined): number | null {
  if (!token) return null;
  const parts = token.split(".");
  if (parts.length < 2) return null;
  try {
    const payload = JSON.parse(atob(parts[1].replace(/-/g, "+").replace(/_/g, "/")));
    const exp = Number(payload?.exp);
    return Number.isFinite(exp) ? exp : null;
  } catch {
    return null;
  }
}

function normalizeTokenData(data: Partial<TokenData> | null | undefined): TokenData | null {
  if (!data) return null;
  const accessToken = String(data.accessToken || "");
  const refreshToken = String(data.refreshToken || "");
  if (!accessToken && !refreshToken) return null;
  return {
    accessToken,
    refreshToken,
    exp: decodeJwtExp(accessToken),
    fetchedAt: Date.now() / 1000,
  };
}

function isTokenValid(token: string | undefined, minTtlSeconds = 300): boolean {
  const exp = decodeJwtExp(token);
  if (!exp) return false;
  return exp > Math.floor(Date.now() / 1000) + minTtlSeconds;
}

function tokenDataValid(data: TokenData | null | undefined, minTtlSeconds = 300): boolean {
  return !!data?.accessToken && isTokenValid(data.accessToken, minTtlSeconds);
}

async function loadKvTokens(env: Env): Promise<TokenData | null> {
  const raw = await env.BCO_BOT_KV.get(KV_TOKEN_KEY);
  if (!raw) return null;
  try {
    return normalizeTokenData(JSON.parse(raw));
  } catch {
    return null;
  }
}

async function saveKvTokens(env: Env, data: TokenData | null): Promise<void> {
  if (!data) return;
  await env.BCO_BOT_KV.put(KV_TOKEN_KEY, JSON.stringify(data));
}

function loadEnvTokens(env: Env): TokenData | null {
  return normalizeTokenData({
    accessToken: env.BCO_ACCESS_TOKEN || "",
    refreshToken: env.BCO_REFRESH_TOKEN || "",
  });
}

async function postJson(url: string, payload: Record<string, unknown>): Promise<Response> {
  return fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

async function tryRefreshToken(accessToken: string, refreshToken: string): Promise<TokenData | null> {
  const resp = await postJson(`${BCO_API_BASE}/auth/refresh_token`, {
    access_token: accessToken,
    refresh_token: refreshToken,
  });
  if (!resp.ok) return null;
  const payload = (await resp.json()) as Dict;
  const data = (payload.data && typeof payload.data === "object" ? payload.data : payload) as Dict;
  return normalizeTokenData({
    accessToken: String(data.accessToken || data.access_token || ""),
    refreshToken: String(data.refreshToken || data.refresh_token || ""),
  });
}

function base32Decode(secret: string): Uint8Array {
  const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
  let bits = "";
  for (const ch of secret.toUpperCase().replace(/[^A-Z2-7]/g, "")) {
    const value = alphabet.indexOf(ch);
    if (value < 0) continue;
    bits += value.toString(2).padStart(5, "0");
  }
  const bytes: number[] = [];
  for (let i = 0; i + 8 <= bits.length; i += 8) {
    bytes.push(Number.parseInt(bits.slice(i, i + 8), 2));
  }
  return new Uint8Array(bytes);
}

async function generateTotp(secret: string): Promise<string> {
  const key = await crypto.subtle.importKey(
    "raw",
    base32Decode(secret),
    { name: "HMAC", hash: "SHA-1" },
    false,
    ["sign"],
  );
  const counter = Math.floor(Date.now() / 1000 / 30);
  const buffer = new ArrayBuffer(8);
  const view = new DataView(buffer);
  view.setUint32(4, counter, false);
  const signature = new Uint8Array(await crypto.subtle.sign("HMAC", key, buffer));
  const offset = signature[signature.length - 1] & 0x0f;
  const binary =
    ((signature[offset] & 0x7f) << 24) |
    (signature[offset + 1] << 16) |
    (signature[offset + 2] << 8) |
    signature[offset + 3];
  return String(binary % 1_000_000).padStart(6, "0");
}

async function loginWithPassword(env: Env): Promise<TokenData | null> {
  const username = (env.BCO_USERNAME || "").trim();
  const password = (env.BCO_PASSWORD || "").trim();
  if (!username || !password) return null;

  const mode = (env.BCO_LOGIN_MODE || "").trim().toLowerCase() || "backoffice";
  const otpSecret = (env.BCO_TOTP_SECRET || "").trim();
  const kvOtp = ((await env.BCO_BOT_KV.get(KV_RUNTIME_OTP_KEY)) || "").trim();
  const otpCode = kvOtp || (env.BCO_OTP_CODE || "").trim();
  const otp = otpSecret ? await generateTotp(otpSecret) : otpCode;

  const attempts: Array<{ endpoint: string; payload: Record<string, unknown> }> = [];
  if (mode === "officer" && otp) {
    attempts.push({
      endpoint: "/auth/login/sso",
      payload: { username, password, otp },
    });
  }
  attempts.push({
    endpoint: "/auth/login",
    payload: { username, password },
  });

  for (const attempt of attempts) {
    const resp = await postJson(`${BCO_API_BASE}${attempt.endpoint}`, attempt.payload);
    if (!resp.ok) continue;
    const payload = (await resp.json()) as Dict;
    const data = (payload.data && typeof payload.data === "object" ? payload.data : payload) as Dict;
    const normalized = normalizeTokenData({
      accessToken: String(data.accessToken || data.access_token || ""),
      refreshToken: String(data.refreshToken || data.refresh_token || ""),
    });
    if (tokenDataValid(normalized)) {
      return normalized;
    }
  }

  return null;
}

async function getValidToken(env: Env, forceRefresh = false): Promise<string> {
  const kvTokens = forceRefresh ? null : await loadKvTokens(env);
  if (tokenDataValid(kvTokens)) return kvTokens!.accessToken;

  const envTokens = forceRefresh ? null : loadEnvTokens(env);
  if (tokenDataValid(envTokens)) return envTokens!.accessToken;

  const staleCandidates = [kvTokens, envTokens].filter(Boolean) as TokenData[];
  for (const candidate of staleCandidates) {
    if (!candidate.accessToken || !candidate.refreshToken) continue;
    const refreshed = await tryRefreshToken(candidate.accessToken, candidate.refreshToken);
    if (tokenDataValid(refreshed)) {
      await saveKvTokens(env, refreshed);
      return refreshed!.accessToken;
    }
  }

  const directLogin = await loginWithPassword(env);
  if (tokenDataValid(directLogin)) {
    await saveKvTokens(env, directLogin);
    return directLogin!.accessToken;
  }

  throw new Error("Could not obtain a valid BCO token");
}

function extractItems(payload: unknown): Dict[] {
  if (Array.isArray(payload)) {
    return payload.filter((item): item is Dict => !!item && typeof item === "object");
  }
  if (!payload || typeof payload !== "object") return [];
  const dict = payload as Dict;
  for (const key of ["data", "items", "results"]) {
    const value = dict[key];
    if (Array.isArray(value)) return value.filter((item): item is Dict => !!item && typeof item === "object");
    if (value && typeof value === "object") {
      for (const nested of ["data", "items", "results"]) {
        const nestedValue = (value as Dict)[nested];
        if (Array.isArray(nestedValue)) return nestedValue.filter((item): item is Dict => !!item && typeof item === "object");
      }
    }
  }
  return [];
}

async function bcoGet(env: Env, path: string, retry = true): Promise<unknown> {
  const token = await getValidToken(env);
  const resp = await fetch(`${BCO_API_BASE}${path}`, {
    headers: {
      Authorization: `Bearer ${token}`,
      Accept: "application/json",
    },
  });
  if (resp.status === 401 && retry) {
    await env.BCO_BOT_KV.delete(KV_TOKEN_KEY);
    const refreshedToken = await getValidToken(env, true);
    const secondResp = await fetch(`${BCO_API_BASE}${path}`, {
      headers: {
        Authorization: `Bearer ${refreshedToken}`,
        Accept: "application/json",
      },
    });
    if (!secondResp.ok) throw new Error(`BCO API failed: HTTP ${secondResp.status}`);
    return secondResp.json();
  }
  if (!resp.ok) throw new Error(`BCO API failed: HTTP ${resp.status}`);
  return resp.json();
}

async function bcoDownload(env: Env, url: string): Promise<{ bytes: ArrayBuffer; contentType: string; }> {
  const token = await getValidToken(env);
  let resp = await fetch(url, {
    headers: {
      Authorization: `Bearer ${token}`,
      Accept: "*/*",
    },
  });
  if (resp.status === 401) {
    await env.BCO_BOT_KV.delete(KV_TOKEN_KEY);
    const refreshed = await getValidToken(env, true);
    resp = await fetch(url, {
      headers: {
        Authorization: `Bearer ${refreshed}`,
        Accept: "*/*",
      },
    });
  }
  if (!resp.ok) throw new Error(`BCO file download failed: HTTP ${resp.status}`);
  return {
    bytes: await resp.arrayBuffer(),
    contentType: resp.headers.get("content-type") || "application/octet-stream",
  };
}

async function getAllUsers(env: Env): Promise<Dict[]> {
  return extractItems(await bcoGet(env, "/users?page=1&limit=200"));
}

async function getAllForms(env: Env): Promise<Dict[]> {
  return extractItems(await bcoGet(env, "/form?form_status_id=1&per_page=10000&page=1"));
}

async function getFormDetail(env: Env, formId: number): Promise<Dict> {
  const payload = (await bcoGet(env, `/form/${formId}`)) as Dict;
  return ((payload.data && typeof payload.data === "object") ? payload.data : {}) as Dict;
}

async function getFormAttachments(env: Env, formId: number): Promise<Dict> {
  const payload = (await bcoGet(env, `/form/${formId}/attachment`)) as Dict;
  return ((payload.data && typeof payload.data === "object") ? payload.data : {}) as Dict;
}

function officerRows(users: Dict[]): Array<Omit<BcoOfficerRow, "total" | "overdue" | "critical" | "near">> {
  return users
    .map((user) => {
      const roles = Array.isArray(user.roles) ? user.roles : [];
      const allowed = roles
        .filter((role): role is Dict => !!role && typeof role === "object")
        .map((role) => String(role.name || ""))
        .filter((name) => ALLOWED_ROLES.has(name));
      const id = Number(user.id);
      if (!allowed.length || !Number.isFinite(id)) return null;
      return {
        id,
        name: `${String(user.first_name || "").trim()} ${String(user.last_name || "").trim()}`.trim(),
        role: allowed[0],
        status: user.status,
        username: String(user.username || ""),
      };
    })
    .filter((item): item is Omit<BcoOfficerRow, "total" | "overdue" | "critical" | "near"> => !!item);
}

function findOfficer(summary: BcoOfficerRow[], query: string): BcoOfficerRow | null {
  const trimmed = query.trim();
  if (!trimmed) return null;
  if (/^\d+$/.test(trimmed)) {
    const match = summary.find((row) => row.id === Number(trimmed));
    if (match) return match;
  }
  const lowered = trimmed.toLowerCase();
  return (
    summary.find((row) => row.name.toLowerCase() === lowered || String(row.username || "").toLowerCase() === lowered) ||
    summary.find((row) => row.name.toLowerCase().includes(lowered) || String(row.username || "").toLowerCase().includes(lowered)) ||
    null
  );
}

function formatStatusMessage(summary: BcoOfficerRow[]): string {
  const grouped = new Map<string, BcoOfficerRow[]>();
  for (const row of summary) {
    const bucket = grouped.get(row.role) || [];
    bucket.push(row);
    grouped.set(row.role, bucket);
  }
  const today = new Date().toLocaleDateString("th-TH", { timeZone: "Asia/Bangkok" });
  const lines = [`สรุปงานเจ้าหน้าที่ BCO`, `วันที่ ${today}`, ``];
  for (const role of ["วิศวกร", "นายตรวจ"]) {
    const rows = grouped.get(role) || [];
    if (!rows.length) continue;
    lines.push(role);
    for (const row of rows) {
      lines.push(`- ${row.name} งาน: ${row.total} เกิน: ${row.overdue} วิกฤต: ${row.critical} ใกล้: ${row.near}`);
    }
    lines.push("");
  }
  const overdue = summary.reduce((sum, row) => sum + row.overdue, 0);
  const critical = summary.reduce((sum, row) => sum + row.critical, 0);
  lines.push(`รวม เกิน: ${overdue} | วิกฤต (>30วัน): ${critical}`);
  return lines.join("\n").trim();
}

function formatTopMessage(rows: BcoOfficerRow[]): string {
  const today = new Date().toLocaleDateString("th-TH", { timeZone: "Asia/Bangkok" });
  const lines = [`อันดับงานเกินกำหนดสูงสุด`, `วันที่ ${today}`, ``];
  rows.forEach((row, index) => {
    lines.push(`${index + 1}. ${row.name} | เกิน: ${row.overdue} | วิกฤต: ${row.critical} | งานทั้งหมด: ${row.total}`);
  });
  return lines.join("\n");
}

function formatOfficerMessage(row: BcoOfficerRow): string {
  return [
    row.name,
    `รหัส: ${row.id}`,
    `บทบาท: ${row.role}`,
    `username: ${row.username || "-"}`,
    `งานทั้งหมด: ${row.total}`,
    `เกินกำหนด: ${row.overdue}`,
    `วิกฤต (>30วัน): ${row.critical}`,
    `ใกล้ครบกำหนด (0-7วัน): ${row.near}`,
  ].join("\n");
}

function formatTasksMessage(officer: BcoOfficerRow, forms: Dict[]): string {
  const lines = [
    `งานของ ${officer.name}`,
    `บทบาท: ${officer.role}`,
    `จำนวนงาน: ${forms.length}`,
    ``,
  ];
  if (!forms.length) {
    lines.push("ไม่พบงานในสถานะ active");
    return lines.join("\n");
  }
  for (const form of forms.slice(0, 10)) {
    lines.push(
      `- ${String(form.form_number || "-")} | ${String(form.status || "-")} | เหลือ ${String(form.day_remaining ?? "-")} วัน | ${String(form.building_name || form.project_name || form.applicant || "-")}`,
    );
  }
  if (forms.length > 10) {
    lines.push("");
    lines.push(`แสดง 10 รายการแรกจากทั้งหมด ${forms.length} งาน`);
  }
  return lines.join("\n");
}

function formatTasksPickerMessage(officer: BcoOfficerRow, forms: Dict[]): string {
  return formatTasksPickerPageMessage(officer, forms, 0);
}

function formatTasksPickerPageMessage(officer: BcoOfficerRow, forms: Dict[], page: number): string {
  const totalPages = Math.max(1, Math.ceil(forms.length / TASKS_PAGE_SIZE));
  const safePage = Math.max(0, Math.min(page, totalPages - 1));
  const start = safePage * TASKS_PAGE_SIZE;
  const pageItems = forms.slice(start, start + TASKS_PAGE_SIZE);

  const lines = [
    `รายการค้างของ ${officer.name}`,
    `บทบาท: ${officer.role}`,
    `จำนวนงานทั้งหมด: ${forms.length}`,
    `หน้า ${safePage + 1}/${totalPages}`,
    "",
    "กดเลือกเรื่องเพื่อดูรายการไฟล์แนบ",
  ];
  if (pageItems.length) {
    lines.push("");
    for (const form of pageItems) {
      lines.push(`- ${String(form.form_number || form.id)} | คงเหลือ: ${String(form.day_remaining ?? "-")} | ${String(form.status || "-")}`);
    }
  } else {
    lines.push("", "ไม่พบงานในหน้านี้");
  }
  return lines.join("\n");
}

function buildTasksKeyboard(officerId: number, forms: Dict[], page: number): Record<string, unknown> {
  const totalPages = Math.max(1, Math.ceil(forms.length / TASKS_PAGE_SIZE));
  const safePage = Math.max(0, Math.min(page, totalPages - 1));
  const start = safePage * TASKS_PAGE_SIZE;
  const pageItems = forms.slice(start, start + TASKS_PAGE_SIZE);
  const inline_keyboard: Array<Array<{ text: string; callback_data: string }>> = [];

  for (const form of pageItems) {
    const text = `${String(form.form_number || form.id)} (${String(form.day_remaining ?? "-")})`.slice(0, 64);
    inline_keyboard.push([{ text, callback_data: `form:${officerId}:${String(form.id)}:${safePage}` }]);
  }

  const nav: Array<{ text: string; callback_data: string }> = [];
  if (safePage > 0) nav.push({ text: "ก่อนหน้า", callback_data: `tasks:${officerId}:${safePage - 1}` });
  if (safePage + 1 < totalPages) nav.push({ text: "ถัดไป", callback_data: `tasks:${officerId}:${safePage + 1}` });
  if (nav.length) inline_keyboard.push(nav);

  return { inline_keyboard };
}

function formatFormFilesPickerMessage(formId: number, detail: Dict, files: FormFileRow[]): string {
  const realFiles = files.filter((row) => row.has_file);
  const mapRow = findSpecialFile(files, MAP_DOC_HINTS, ["a5.6"]);
  const buildingRow = findSpecialFile(files, BUILDING_DOC_HINTS, ["a5.5"]);
  const lines = [
    `${String(detail.form_number || formId)}`,
    `id: ${formId}`,
    `สถานะ: ${String(detail.status || "-")}`,
    `คงเหลือ: ${String(detail.day_remaining ?? "-")}`,
    `ไฟล์ที่มีจริง: ${realFiles.length}`,
    `มีแผนที่แนบ: ${mapRow ? "มี" : "ไม่มี"}`,
    `มีรูปหน้าอาคาร: ${buildingRow ? "มี" : "ไม่มี"}`,
  ];
  if (detail.latitude && detail.longitude) {
    lines.push(`พิกัด: ${String(detail.latitude)}, ${String(detail.longitude)}`);
  }
  lines.push("", "กดปุ่มดูแผนที่/รูปอาคาร หรือเลือกไฟล์ต้นฉบับให้บอทส่งเข้าแชต");
  return lines.join("\n");
}

function buildFormFilesKeyboard(formId: number, files: FormFileRow[], officerId: number | null = null, page = 0): Record<string, unknown> {
  const realFiles = files.filter((row) => row.has_file);
  const inline_keyboard: Array<Array<{ text: string; callback_data: string }>> = [];
  inline_keyboard.push([
    { text: "ประวัติการดำเนินการ", callback_data: `section:${formId}:history:${officerId ?? 0}:${page}` },
    { text: "เอกสารแนบ", callback_data: `section:${formId}:attachments:${officerId ?? 0}:${page}` },
  ]);
  inline_keyboard.push([
    { text: "การดำเนินการ", callback_data: `section:${formId}:action:${officerId ?? 0}:${page}` },
  ]);
  const actionRow: Array<{ text: string; callback_data: string }> = [];
  if (findSpecialFile(files, MAP_DOC_HINTS, ["a5.6"])) actionRow.push({ text: "ดูแผนที่", callback_data: `preview:${formId}:map` });
  if (findSpecialFile(files, BUILDING_DOC_HINTS, ["a5.5"])) actionRow.push({ text: "ดูรูปอาคาร", callback_data: `preview:${formId}:building` });
  if (actionRow.length) inline_keyboard.push(actionRow);

  for (const row of realFiles) {
    inline_keyboard.push([{ text: `${row.key} ${String(row.doc_name || "-")}`.slice(0, 64), callback_data: `file:${formId}:${row.key}` }]);
  }

  if (officerId !== null) {
    inline_keyboard.push([{ text: "กลับไปรายการเรื่อง", callback_data: `tasks:${officerId}:${page}` }]);
  }

  return { inline_keyboard };
}

async function sendTasksMenu(env: Env, chatId: number | string, officer: BcoOfficerRow, forms: Dict[], page: number): Promise<void> {
  await sendMessage(env, chatId, formatTasksPickerPageMessage(officer, forms, page), {
    reply_markup: buildTasksKeyboard(officer.id, forms, page),
  });
}

async function editTasksMenu(env: Env, chatId: number | string, messageId: number, officer: BcoOfficerRow, forms: Dict[], page: number): Promise<void> {
  await editMessageText(env, chatId, messageId, formatTasksPickerPageMessage(officer, forms, page), {
    reply_markup: buildTasksKeyboard(officer.id, forms, page),
  });
}

async function editFormFilesMenu(
  env: Env,
  chatId: number | string,
  messageId: number,
  formId: number,
  detail: Dict,
  files: FormFileRow[],
  officerId: number,
  page: number,
): Promise<void> {
  await editMessageText(env, chatId, messageId, formatFormFilesPickerMessage(formId, detail, files), {
    reply_markup: buildFormFilesKeyboard(formId, files, officerId, page),
  });
}

async function editFormMenu(
  env: Env,
  chatId: number | string,
  messageId: number,
  formId: number,
  detail: Dict,
  files: FormFileRow[],
  officerId: number | null,
  page: number,
): Promise<void> {
  await editMessageText(env, chatId, messageId, formatFormMenuMessage(detail), {
    reply_markup: buildFormFilesKeyboard(formId, files, officerId, page),
  });
}

function flattenFormDetail(formId: number, data: Dict): Dict {
  const formDetail = (data.form_detail && typeof data.form_detail === "object" ? data.form_detail : {}) as Dict;
  const applicant = (data.applicant_person && typeof data.applicant_person === "object" ? data.applicant_person : {}) as Dict;
  const owner = (data.owner_person && typeof data.owner_person === "object" ? data.owner_person : {}) as Dict;
  const building = (data.k1_form_building && typeof data.k1_form_building === "object" ? data.k1_form_building : {}) as Dict;
  const buildingInfoList = Array.isArray(building.k1_form_building_info) ? building.k1_form_building_info : [];
  const buildingInfo = buildingInfoList.length && buildingInfoList[0] && typeof buildingInfoList[0] === "object" ? buildingInfoList[0] as Dict : {};
  const latitude = building.latitude;
  const longitude = building.longitude;
  const googleMapsUrl = latitude && longitude ? `https://www.google.com/maps?q=${latitude},${longitude}` : null;
  const openstreetmapUrl = latitude && longitude ? `https://www.openstreetmap.org/?mlat=${latitude}&mlon=${longitude}#map=18/${latitude}/${longitude}` : null;
  const addressParts = [
    building.addr_no,
    building.addr_moo ? `หมู่ ${String(building.addr_moo)}` : null,
    building.addr_soi ? `ซอย${String(building.addr_soi)}` : null,
    building.addr_road_name,
  ].filter(Boolean);
  return {
    id: formId,
    form_number: formDetail.form_number,
    status: formDetail.ref_status_name,
    status_id: formDetail.ref_status_id,
    day_remaining: formDetail.day_remaining,
    owner_name: formDetail.owner_name,
    assign_to_name: formDetail.assign_to_name,
    assign_to_department: formDetail.assign_to_deparment,
    assign_to_owner: formDetail.assign_to_owner,
    user_assign: formDetail.user_assign,
    reason_send_back: formDetail.reason_send_back,
    authorized_number: formDetail.authorized_number,
    form_date: formDetail.form_date,
    applicant_name: [((applicant.prefix_name && typeof applicant.prefix_name === "object" ? (applicant.prefix_name as Dict).name : null) as string | null), applicant.first_name, applicant.last_name].filter(Boolean).join(" "),
    applicant_mobile: applicant.mobile,
    applicant_email: applicant.email,
    owner_person_name: [((owner.prefix_name && typeof owner.prefix_name === "object" ? (owner.prefix_name as Dict).name : null) as string | null), owner.first_name, owner.last_name].filter(Boolean).join(" "),
    building_name: buildingInfo.building_name || building.building_name,
    project_name: buildingInfo.project_name,
    address: addressParts.join(" "),
    latitude,
    longitude,
    google_maps_url: googleMapsUrl,
    openstreetmap_url: openstreetmapUrl,
  };
}

type FormFileRow = {
  form_id: number;
  key: string;
  parent_key?: string | null;
  kind: string;
  doc_id?: unknown;
  seq?: unknown;
  doc_name?: unknown;
  form_attachment_id?: unknown;
  has_file: boolean;
  file_name?: string | null;
  file_type?: string | null;
  file_url?: string | null;
  file_created_at?: string | null;
};

function listFormFiles(formId: number, data: Dict): FormFileRow[] {
  const applicantDoc = (data.applicant_doc && typeof data.applicant_doc === "object" ? data.applicant_doc : {}) as Dict;
  const files: FormFileRow[] = [];
  const addRow = (kind: string, key: string, row: Dict, parentKey: string | null = null) => {
    const fileInfo = row.file && typeof row.file === "object" ? row.file as Dict : null;
    files.push({
      form_id: formId,
      key,
      parent_key: parentKey,
      kind,
      doc_id: row.id,
      seq: row.seq,
      doc_name: row.doc_name,
      form_attachment_id: row.form_attachment_id,
      has_file: !!(row.is_file && fileInfo && fileInfo.url),
      file_name: fileInfo ? String(fileInfo.name || "") : null,
      file_type: fileInfo ? String(fileInfo.type || "") : null,
      file_url: fileInfo ? String(fileInfo.url || "") : null,
      file_created_at: fileInfo ? String(fileInfo.created_at || "") : null,
    });
  };

  const downloadDocs = Array.isArray(applicantDoc.dowload_doc) ? applicantDoc.dowload_doc : [];
  for (const row of downloadDocs) {
    if (!row || typeof row !== "object") continue;
    const dict = row as Dict;
    addRow("download", `d${String(dict.seq ?? "")}`, dict);
  }
  const attachments = Array.isArray(applicantDoc.attachment) ? applicantDoc.attachment : [];
  for (const row of attachments) {
    if (!row || typeof row !== "object") continue;
    const dict = row as Dict;
    const key = `a${String(dict.seq ?? "")}`;
    addRow("attachment", key, dict);
    const subDocs = Array.isArray(dict.sub_doc) ? dict.sub_doc : [];
    subDocs.forEach((sub, index) => {
      if (!sub || typeof sub !== "object") return;
      addRow("sub_doc", `${key}.${index + 1}`, sub as Dict, key);
    });
  }
  return files;
}

function findFormFileByKey(data: Dict, key: string): FormFileRow | null {
  const wanted = key.trim().toLowerCase();
  return listFormFiles(0, data).find((row) => row.key.toLowerCase() === wanted) || null;
}

function formatFormDetailMessage(detail: Dict): string {
  const lines = [
    `${String(detail.form_number || "-")}`,
    `id: ${String(detail.id || "-")}`,
    `สถานะ: ${String(detail.status || "-")}`,
    `คงเหลือ: ${String(detail.day_remaining ?? "-")}`,
    `ผู้รับผิดชอบ: ${String(detail.assign_to_name || "-")}`,
    `หน่วยงาน: ${String(detail.assign_to_department || "-")}`,
    `owner: ${String(detail.assign_to_owner || "-")}`,
  ];
  if (detail.building_name) lines.push(`อาคาร: ${String(detail.building_name)}`);
  if (detail.project_name) lines.push(`โครงการ: ${String(detail.project_name)}`);
  if (detail.address) lines.push(`ที่ตั้ง: ${String(detail.address)}`);
  if (detail.latitude && detail.longitude) lines.push(`พิกัด: ${String(detail.latitude)}, ${String(detail.longitude)}`);
  if (detail.google_maps_url) lines.push(`Google Maps: ${String(detail.google_maps_url)}`);
  if (detail.openstreetmap_url) lines.push(`OpenStreetMap: ${String(detail.openstreetmap_url)}`);
  if (detail.applicant_name) lines.push(`ผู้ยื่น: ${String(detail.applicant_name)}`);
  if (detail.applicant_mobile) lines.push(`มือถือ: ${String(detail.applicant_mobile)}`);
  if (detail.applicant_email) lines.push(`อีเมล: ${String(detail.applicant_email)}`);
  if (detail.authorized_number) lines.push(`เลขอนุมัติ: ${String(detail.authorized_number)}`);
  if (detail.reason_send_back) lines.push(`เหตุผลส่งกลับ: ${String(detail.reason_send_back)}`);
  const users = Array.isArray(detail.user_assign) ? detail.user_assign : [];
  if (users.length) {
    lines.push("ผู้เกี่ยวข้อง:");
    users.forEach((entry) => lines.push(`- ${String(entry)}`));
  }
  return lines.join("\n");
}

function formatFormMenuMessage(detail: Dict): string {
  return formatFormDetailMessage(detail) + "\n\nเลือกเมนูด้านล่าง";
}

function findSpecialFile(files: FormFileRow[], docHints: string[], keyHints: string[]): FormFileRow | null {
  for (const key of keyHints) {
    const found = files.find((row) => row.has_file && row.key.toLowerCase() === key.toLowerCase());
    if (found) return found;
  }
  const loweredHints = docHints.map((hint) => hint.toLowerCase());
  return files.find((row) => row.has_file && loweredHints.some((hint) => String(row.doc_name || "").toLowerCase().includes(hint))) || null;
}

function formatMapMessage(detail: Dict, row: FormFileRow | null): string {
  const lines = [`แผนที่ของ ${String(detail.form_number || detail.id || "-")}`, `id: ${String(detail.id || "-")}`];
  if (detail.building_name) lines.push(`อาคาร: ${String(detail.building_name)}`);
  if (detail.address) lines.push(`ที่ตั้ง: ${String(detail.address)}`);
  if (detail.latitude && detail.longitude) lines.push(`พิกัด: ${String(detail.latitude)}, ${String(detail.longitude)}`);
  if (detail.google_maps_url) lines.push(`Google Maps: ${String(detail.google_maps_url)}`);
  if (detail.openstreetmap_url) lines.push(`OpenStreetMap: ${String(detail.openstreetmap_url)}`);
  lines.push(row ? `ไฟล์แผนที่: ${row.key} | ${row.file_name || "-"}` : "ไม่พบไฟล์แผนที่แนบในฟอร์มนี้");
  return lines.join("\n");
}

function formatBuildingPhotoMessage(detail: Dict, row: FormFileRow | null): string {
  const lines = [`รูปหน้าอาคารของ ${String(detail.form_number || detail.id || "-")}`, `id: ${String(detail.id || "-")}`];
  if (detail.building_name) lines.push(`อาคาร: ${String(detail.building_name)}`);
  if (detail.address) lines.push(`ที่ตั้ง: ${String(detail.address)}`);
  lines.push(row ? `ไฟล์รูปอาคาร: ${row.key} | ${row.file_name || "-"}` : "ไม่พบไฟล์รูปหน้าอาคารในฟอร์มนี้");
  return lines.join("\n");
}

function formatFormFilesMessage(formId: number, files: FormFileRow[]): string {
  const lines = [`ไฟล์ในฟอร์ม ${formId}`, `จำนวนไฟล์ที่มีจริง: ${files.filter((row) => row.has_file).length}`, ``];
  const realFiles = files.filter((row) => row.has_file);
  if (!realFiles.length) {
    lines.push("ไม่พบไฟล์ที่แนบจริง");
    return lines.join("\n");
  }
  realFiles.forEach((row) => lines.push(`- ${row.key} | ${String(row.doc_name || "-")} | ${row.file_name || "-"}`));
  lines.push("", "ใช้ /file <form_id> <key> เพื่อให้บอทส่งไฟล์ต้นฉบับ");
  return lines.join("\n");
}

function flattenHistoryRows(rows: Dict[], depth = 0): Dict[] {
  const result: Dict[] = [];
  for (const row of rows) {
    result.push({ ...row, _depth: depth });
    const children = Array.isArray(row.children) ? row.children.filter((item): item is Dict => !!item && typeof item === "object") : [];
    result.push(...flattenHistoryRows(children, depth + 1));
  }
  return result;
}

function formatFormHistoryMessage(formId: number, rows: Dict[]): string {
  const flat = flattenHistoryRows(rows);
  const lines = [`ประวัติการดำเนินการของ ${formId}`, `จำนวนรายการ: ${flat.length}`, ``];
  if (!flat.length) {
    lines.push("ไม่พบประวัติการดำเนินการ");
    return lines.join("\n");
  }
  for (const row of flat.slice(0, 25)) {
    const indent = "  ".repeat(Number(row._depth || 0));
    lines.push(`${indent}- ${String(row.name || "-")} | ${String(row.department || "-")} | ${String(row.assign_date || "-")}`);
    lines.push(`${indent}  ผู้รับผิดชอบ: ${String(row.name_onwer || "-")} | เจ้าของงาน: ${String(row.owner || "-")}`);
    const reason = String(row.reason || "").trim();
    if (reason) lines.push(`${indent}  เหตุผล: ${reason}`);
  }
  if (flat.length > 25) lines.push("", `แสดง 25 รายการแรกจากทั้งหมด ${flat.length}`);
  return lines.join("\n");
}

function extractOfficialRows(attachments: Dict): Array<{ section: string; row: Dict }> {
  const official = (attachments.official_doc && typeof attachments.official_doc === "object" ? attachments.official_doc : {}) as Dict;
  const result: Array<{ section: string; row: Dict }> = [];
  const addRows = (section: string, rows: unknown) => {
    if (!Array.isArray(rows)) return;
    for (const row of rows) {
      if (!row || typeof row !== "object") continue;
      result.push({ section, row: row as Dict });
    }
  };
  addRows("หนังสือ/เอกสารทางการ", official.doc);
  addRows("เอกสารวิศวกร", (official.engineer_doc && typeof official.engineer_doc === "object" ? (official.engineer_doc as Dict).doc : []));
  addRows("เอกสารนายตรวจ", (official.inspectors_doc && typeof official.inspectors_doc === "object" ? (official.inspectors_doc as Dict).doc : []));
  return result;
}

function formatOfficialFilesMessage(formId: number, attachments: Dict): string {
  const official = (attachments.official_doc && typeof attachments.official_doc === "object" ? attachments.official_doc : {}) as Dict;
  const rows = extractOfficialRows(attachments);
  const lines = [
    `เอกสารแนบของ ${formId}`,
    `manage_file_status: ${String(official.manage_file_status ?? "-")}`,
    `จำนวนรายการ: ${rows.length}`,
    "",
  ];
  if (!rows.length) {
    lines.push("ไม่พบเอกสารแนบฝั่งทางการ");
    return lines.join("\n");
  }
  for (const { section, row } of rows) {
    const file = row.file && typeof row.file === "object" ? row.file as Dict : null;
    lines.push(`- [${section}] ${String(row.seq ?? "-")} ${String(row.doc_name || "-")} | ${file ? String(file.name || "-") : "ยังไม่มีไฟล์"}`);
  }
  return lines.join("\n");
}

function formatActionSummaryMessage(formId: number, detail: Dict, attachments: Dict): string {
  const official = (attachments.official_doc && typeof attachments.official_doc === "object" ? attachments.official_doc : {}) as Dict;
  const engineerDoc = (official.engineer_doc && typeof official.engineer_doc === "object" ? official.engineer_doc : {}) as Dict;
  const inspectorDoc = (official.inspectors_doc && typeof official.inspectors_doc === "object" ? official.inspectors_doc : {}) as Dict;
  const lines = [
    `การดำเนินการของ ${String(detail.form_number || formId)}`,
    `id: ${formId}`,
    `สถานะ: ${String(detail.status || "-")}`,
    `คงเหลือ: ${String(detail.day_remaining ?? "-")}`,
    `ผู้รับผิดชอบ: ${String(detail.assign_to_name || "-")}`,
    `หน่วยงาน: ${String(detail.assign_to_department || "-")}`,
    `owner: ${String(detail.assign_to_owner || "-")}`,
    `manage_file_status: ${String(official.manage_file_status ?? "-")}`,
    `วิศวกรจัดการไฟล์ได้: ${String(engineerDoc.manage_file_status ?? "-")}`,
    `นายตรวจจัดการไฟล์ได้: ${String(inspectorDoc.manage_file_status ?? "-")}`,
  ];
  if (detail.authorized_number) lines.push(`เลขอนุมัติ: ${String(detail.authorized_number)}`);
  if (detail.reason_send_back) lines.push(`เหตุผลส่งกลับ: ${String(detail.reason_send_back)}`);
  const users = Array.isArray(detail.user_assign) ? detail.user_assign : [];
  if (users.length) {
    lines.push("ผู้เกี่ยวข้อง:");
    users.forEach((entry) => lines.push(`- ${String(entry)}`));
  }
  return lines.join("\n");
}

function formatOneFileMessage(formId: number, row: FormFileRow): string {
  return [
    `ไฟล์ในฟอร์ม ${formId}`,
    `key: ${row.key}`,
    `เอกสาร: ${String(row.doc_name || "-")}`,
    `ไฟล์: ${row.file_name || "-"}`,
    `ประเภท: ${row.file_type || "-"}`,
    `อัปโหลดเมื่อ: ${row.file_created_at || "-"}`,
    `ลิงก์: ${row.file_url || "-"}`,
  ].join("\n");
}

function buildFileCaption(formId: number, row: FormFileRow): string {
  return [`ฟอร์ม ${formId}`, `key: ${row.key}`, `เอกสาร: ${String(row.doc_name || "-")}`, `ไฟล์: ${row.file_name || "-"}`].join("\n");
}

function findR1AttachmentRow(data: Dict): Dict | null {
  const applicantDoc = (data.applicant_doc && typeof data.applicant_doc === "object" ? data.applicant_doc : {}) as Dict;
  const rows = Array.isArray(applicantDoc.attachment) ? applicantDoc.attachment : [];
  for (const row of rows) {
    if (!row || typeof row !== "object") continue;
    const dict = row as Dict;
    const docName = String(dict.doc_name || "").trim();
    if (dict.id === R1_ATTACHMENT_DOC_ID) return dict;
    if (dict.seq === R1_ATTACHMENT_SEQ && docName.includes("ร.1")) return dict;
    if (docName === R1_ATTACHMENT_NAME) return dict;
  }
  return null;
}

function r1FileNameLooksLikeR1(fileName: string | null | undefined): boolean {
  const lowered = String(fileName || "").toLowerCase();
  return R1_FILE_NAME_HINTS.some((hint) => lowered.includes(hint));
}

function flattenR1AttachmentStatus(formId: number, formNumber: unknown, dayRemaining: unknown, status: unknown, data: Dict): Dict {
  const row = findR1AttachmentRow(data);
  const fileInfo = row?.file && typeof row.file === "object" ? row.file as Dict : null;
  const hasFile = !!(row && row.form_attachment_id && row.is_file && fileInfo?.url);
  return {
    id: formId,
    form_number: formNumber,
    day_remaining: dayRemaining,
    status,
    has_r1_file: hasFile,
    file_name_looks_like_r1: hasFile ? r1FileNameLooksLikeR1(String(fileInfo?.name || "")) : null,
    file_name: fileInfo ? String(fileInfo.name || "") : null,
  };
}

function formatR1Message(officer: BcoOfficerRow, rows: Dict[]): string {
  const attached = rows.filter((row) => row.has_r1_file).length;
  const missing = rows.length - attached;
  const lines = [
    `ไฟล์ ร.1 ของ ${officer.name}`,
    `จำนวน ขร.1: ${rows.length}`,
    `แนบแล้ว: ${attached}`,
    `ยังไม่แนบ: ${missing}`,
    "",
  ];
  if (!rows.length) {
    lines.push("ไม่พบงาน ขร.1");
    return lines.join("\n");
  }
  for (const row of rows) {
    const state = !row.has_r1_file ? "ไม่มีไฟล์" : row.file_name_looks_like_r1 ? "มีไฟล์" : "มีไฟล์/ต้องเปิดดู";
    lines.push(`- ${String(row.id)} ${String(row.form_number || "-")} | ${state} | คงเหลือ: ${String(row.day_remaining ?? "-")} | ${String(row.file_name || "-")}`);
  }
  return lines.join("\n");
}

function authWarning(detail: string): string {
  return [
    "BCO auth ใช้งานไม่ได้",
    detail,
    "",
    "ให้ทำอย่างใดอย่างหนึ่ง:",
    "• ใส่ BCO_ACCESS_TOKEN / BCO_REFRESH_TOKEN ใหม่",
    "• หรือใส่ BCO_USERNAME / BCO_PASSWORD",
    "• ถ้าใช้ officer flow ให้เพิ่ม BCO_TOTP_SECRET หรือส่ง /otp <รหัส>",
  ].join("\n");
}

async function sendFormFile(env: Env, chatId: number | string, formId: number, row: FormFileRow): Promise<void> {
  if (!row.file_url) throw new Error("File URL missing");
  const { bytes, contentType } = await bcoDownload(env, row.file_url);
  const fileName = row.file_name || `${formId}_${row.key}`;
  const isPhoto = contentType.toLowerCase().startsWith("image/");
  await sendBinary(env, isPhoto ? "sendPhoto" : "sendDocument", chatId, fileName, bytes, buildFileCaption(formId, row));
}

async function getWorkSummary(env: Env): Promise<BcoOfficerRow[]> {
  const [users, forms] = await Promise.all([getAllUsers(env), getAllForms(env)]);
  const officers = officerRows(users);
  const byId = new Map<number, BcoOfficerRow>();
  const nameToIds = new Map<string, number[]>();

  for (const officer of officers) {
    const fullRow: BcoOfficerRow = { ...officer, total: 0, overdue: 0, critical: 0, near: 0 };
    byId.set(officer.id, fullRow);
    const bucket = nameToIds.get(officer.name) || [];
    bucket.push(officer.id);
    nameToIds.set(officer.name, bucket);
  }

  for (const form of forms) {
    const rawOwner = form.user_owner;
    let owners: number[] = [];
    const ownerId = Number(rawOwner);
    if (Number.isFinite(ownerId) && byId.has(ownerId)) {
      owners = [ownerId];
    } else if (typeof rawOwner === "string") {
      owners = nameToIds.get(rawOwner.trim()) || [];
    }
    if (!owners.length) continue;
    const dayRemaining = Number(form.day_remaining);
    if (!Number.isFinite(dayRemaining)) continue;
    for (const owner of owners) {
      const row = byId.get(owner);
      if (!row) continue;
      row.total += 1;
      if (dayRemaining < 0) row.overdue += 1;
      if (dayRemaining < -30) row.critical += 1;
      if (dayRemaining >= 0 && dayRemaining <= 7) row.near += 1;
    }
  }

  return [...byId.values()].sort((a, b) => {
    return b.overdue - a.overdue || b.critical - a.critical || b.total - a.total || a.name.localeCompare(b.name, "th");
  });
}

async function getTasksForOfficer(env: Env, officerQuery: string): Promise<{ officer: BcoOfficerRow; forms: Dict[] }> {
  const summary = await getWorkSummary(env);
  const officer = findOfficer(summary, officerQuery);
  if (!officer) throw new Error(`Officer not found: ${officerQuery}`);
  const forms = (await getAllForms(env))
    .filter((form) => String(form.user_owner || "").trim() === officer.name)
    .map((form) => ({
      id: form.id,
      form_number: form.form_number,
      status: form.ref_status_name,
      day_remaining: form.day_remaining,
      applicant: form.applicant_person || form.person_request_name,
      building_name: form.building_name,
      project_name: form.project_name,
    }))
    .sort((a, b) => Number(a.day_remaining ?? 1e9) - Number(b.day_remaining ?? 1e9));
  return { officer, forms };
}

async function handleCommand(env: Env, message: TelegramMessage): Promise<string | null> {
  const text = (message.text || "").trim();
  const [command, ...args] = text.split(/\s+/);
  const query = args.join(" ").trim();

  if (command === "/start" || command === "/help") {
    return [
      "คำสั่งที่ใช้ได้",
      "/help - แสดงรายการคำสั่ง",
      "/start - แสดงรายการคำสั่ง",
      "",
      "/status - สรุปงานทั้งหมด",
      "/top - 5 คนที่งานเกินกำหนดมากสุด",
      "/officer <ชื่อ|id|username> - ดูสรุปรายคน",
      "/tasks <ชื่อ|id|username> - ดูรายการค้าง",
      "/form <form_id> - ดูรายละเอียดงานเดี่ยว",
      "/map <form_id> - ดูพิกัดและไฟล์แผนที่ของเรื่อง",
      "/building <form_id> - ดูรูปหน้าอาคารของเรื่อง",
      "/files <form_id> - ดูรายการไฟล์ทั้งหมดในฟอร์ม",
      "/file <form_id> <key> - ส่งไฟล์หนึ่งตัวเข้าแชต",
      "/r1 <ชื่อ|id|username> - เช็คไฟล์ ร.1 ของงาน ขร.1",
      "/otp <รหัส> - ส่ง OTP เพื่อให้บอท login BCO",
      "/chatid - ดู chat id",
      "/refresh - ล้าง token cache แล้วลองใหม่",
    ].join("\n");
  }

  if (command === "/chatid") {
    return `chat_id: ${message.chat?.id ?? "-"}`;
  }

  if (command === "/status") {
    return formatStatusMessage(await getWorkSummary(env));
  }

  if (command === "/top") {
    const summary = await getWorkSummary(env);
    return formatTopMessage(summary.slice(0, 5));
  }

  if (command === "/refresh") {
    await env.BCO_BOT_KV.delete(KV_TOKEN_KEY);
    return "รีเฟรช token แล้ว\n\n" + formatStatusMessage(await getWorkSummary(env));
  }

  if (command === "/officer") {
    if (!query) return "ใช้คำสั่ง /officer <id|username|ชื่อ>";
    const summary = await getWorkSummary(env);
    const officer = findOfficer(summary, query);
    return officer ? formatOfficerMessage(officer) : `ไม่พบเจ้าหน้าที่: ${query}`;
  }

  if (command === "/tasks") {
    if (!query) return "ใช้คำสั่ง /tasks <id|username|ชื่อ>";
    const { officer, forms } = await getTasksForOfficer(env, query);
    await sendTasksMenu(env, message.chat?.id || "", officer, forms, 0);
    return null;
  }

  if (command === "/form") {
    if (!/^\d+$/.test(query)) return "ใช้คำสั่ง /form <form_id>";
    const detail = flattenFormDetail(Number(query), await getFormDetail(env, Number(query)));
    return formatFormDetailMessage(detail) + "\n\nใช้ /map เพื่อดูแผนที่ และ /building เพื่อดูรูปหน้าอาคาร";
  }

  if (command === "/map") {
    if (!/^\d+$/.test(query)) return "ใช้คำสั่ง /map <form_id>";
    const formId = Number(query);
    const detail = flattenFormDetail(formId, await getFormDetail(env, formId));
    const files = listFormFiles(formId, await getFormAttachments(env, formId));
    const row = findSpecialFile(files, MAP_DOC_HINTS, ["a5.6"]);
    await sendMessage(env, message.chat?.id || "", formatMapMessage(detail, row));
    if (row) {
      await sendFormFile(env, message.chat?.id || "", formId, row);
      return null;
    }
    return null;
  }

  if (command === "/building") {
    if (!/^\d+$/.test(query)) return "ใช้คำสั่ง /building <form_id>";
    const formId = Number(query);
    const detail = flattenFormDetail(formId, await getFormDetail(env, formId));
    const files = listFormFiles(formId, await getFormAttachments(env, formId));
    const row = findSpecialFile(files, BUILDING_DOC_HINTS, ["a5.5"]);
    await sendMessage(env, message.chat?.id || "", formatBuildingPhotoMessage(detail, row));
    if (row) {
      await sendFormFile(env, message.chat?.id || "", formId, row);
      return null;
    }
    return null;
  }

  if (command === "/files") {
    if (!/^\d+$/.test(query)) return "ใช้คำสั่ง /files <form_id>";
    const formId = Number(query);
    const files = listFormFiles(formId, await getFormAttachments(env, formId));
    return formatFormFilesMessage(formId, files);
  }

  if (command === "/file") {
    const [formIdText, key] = args;
    if (!formIdText || !/^\d+$/.test(formIdText) || !key) return "ใช้คำสั่ง /file <form_id> <key>";
    const formId = Number(formIdText);
    const attachmentData = await getFormAttachments(env, formId);
    const row = findFormFileByKey(attachmentData, key);
    if (!row) return `ไม่พบ key ${key} ในฟอร์ม ${formId}`;
    if (!row.has_file) return `key ${key} มีช่องเอกสาร แต่ยังไม่มีไฟล์แนบ`;
    await sendFormFile(env, message.chat?.id || "", formId, row);
    return null;
  }

  if (command === "/r1") {
    if (!query) return "ใช้คำสั่ง /r1 <id|username|ชื่อ>";
    const { officer, forms } = await getTasksForOfficer(env, query);
    const rows: Dict[] = [];
    for (const form of forms) {
      const formNumber = String(form.form_number || "");
      const formId = Number(form.id);
      if (!formNumber.startsWith("ขร.1") || !Number.isFinite(formId)) continue;
      rows.push(flattenR1AttachmentStatus(formId, form.form_number, form.day_remaining, form.status, await getFormAttachments(env, formId)));
    }
    rows.sort((a, b) => Number(Boolean(a.has_r1_file)) - Number(Boolean(b.has_r1_file)) || Number(a.day_remaining ?? 1e9) - Number(b.day_remaining ?? 1e9));
    return formatR1Message(officer, rows);
  }

  if (command === "/otp") {
    const code = query.replace(/\s+/g, "");
    if (message.chat?.type !== "private") return "คำสั่ง /otp ใช้ได้เฉพาะใน private chat กับบอท";
    if (!/^\d{6,8}$/.test(code)) return "ใช้คำสั่ง /otp <รหัส OTP 6 หรือ 8 หลัก>";
    if (!(env.BCO_USERNAME || "").trim() || !(env.BCO_PASSWORD || "").trim()) {
      return "ยังไม่มี BCO_USERNAME / BCO_PASSWORD จึงใช้ OTP จาก Telegram ต่อไม่ได้";
    }
    await env.BCO_BOT_KV.put(KV_RUNTIME_OTP_KEY, code, { expirationTtl: 120 });
    await env.BCO_BOT_KV.delete(KV_TOKEN_KEY);
    return "รับ OTP แล้วและลอง login เรียบร้อย\n\n" + formatStatusMessage(await getWorkSummary(env));
  }

  return "ไม่รู้จักคำสั่งนี้ ใช้ /help";
}

async function processTelegramUpdate(env: Env, update: TelegramUpdate): Promise<void> {
  const callbackQuery = update.callback_query;
  if (callbackQuery?.id && callbackQuery.data && callbackQuery.message?.chat?.id && callbackQuery.message.message_id) {
    try {
      await answerCallbackQuery(env, callbackQuery.id);
      const data = callbackQuery.data;
      const chatId = callbackQuery.message.chat.id;
      const messageId = callbackQuery.message.message_id;

      if (data.startsWith("tasks:")) {
        const [, officerIdText, pageText] = data.split(":", 3);
        const { officer, forms } = await getTasksForOfficer(env, officerIdText);
        await editTasksMenu(env, chatId, messageId, officer, forms, Number(pageText || "0"));
        return;
      }

      if (data.startsWith("form:")) {
        const [, officerIdText, formIdText, pageText] = data.split(":", 4);
        const formId = Number(formIdText);
        const detail = flattenFormDetail(formId, await getFormDetail(env, formId));
        const files = listFormFiles(formId, await getFormAttachments(env, formId));
        await editFormFilesMenu(env, chatId, messageId, formId, detail, files, Number(officerIdText), Number(pageText || "0"));
        return;
      }

      if (data.startsWith("file:")) {
        const [, formIdText, key] = data.split(":", 3);
        const formId = Number(formIdText);
        const attachmentData = await getFormAttachments(env, formId);
        const row = findFormFileByKey(attachmentData, key);
        if (!row) {
          await sendMessage(env, chatId, `ไม่พบ key ${key} ในฟอร์ม ${formId}`);
          return;
        }
        if (!row.has_file) {
          await sendMessage(env, chatId, `key ${key} มีช่องเอกสาร แต่ยังไม่มีไฟล์แนบ`);
          return;
        }
        await sendFormFile(env, chatId, formId, row);
        return;
      }

      if (data.startsWith("preview:")) {
        const [, formIdText, previewType] = data.split(":", 3);
        const formId = Number(formIdText);
        const detail = flattenFormDetail(formId, await getFormDetail(env, formId));
        const files = listFormFiles(formId, await getFormAttachments(env, formId));
        if (previewType === "map") {
          const row = findSpecialFile(files, MAP_DOC_HINTS, ["a5.6"]);
          await sendMessage(env, chatId, formatMapMessage(detail, row));
          if (row) await sendFormFile(env, chatId, formId, row);
          return;
        }
        if (previewType === "building") {
          const row = findSpecialFile(files, BUILDING_DOC_HINTS, ["a5.5"]);
          await sendMessage(env, chatId, formatBuildingPhotoMessage(detail, row));
          if (row) await sendFormFile(env, chatId, formId, row);
          return;
        }
      }
    } catch (error) {
      const detail = error instanceof Error ? error.message : String(error);
      await sendMessage(env, callbackQuery.message.chat.id, authWarning(detail));
      return;
    }
  }

  const message = update.message;
  if (!message?.chat?.id || !message.text) return;
  try {
    const reply = await handleCommand(env, message);
    if (reply) {
      await sendMessage(env, message.chat.id, reply);
    }
  } catch (error) {
    const detail = error instanceof Error ? error.message : String(error);
    await sendMessage(env, message.chat.id, authWarning(detail));
  }
}

async function sendDailyStatus(env: Env): Promise<void> {
  const chatId = (env.TELEGRAM_CHAT_ID || "").trim();
  if (!chatId) return;
  await sendMessage(env, chatId, formatStatusMessage(await getWorkSummary(env)));
}

async function monitorAuthHealth(env: Env): Promise<void> {
  const chatId = (env.TELEGRAM_CHAT_ID || "").trim();
  if (!chatId) return;
  let ok = true;
  let detail = "";
  try {
    await getWorkSummary(env);
  } catch (error) {
    ok = false;
    detail = error instanceof Error ? error.message : String(error);
  }

  const active = (await env.BCO_BOT_KV.get(KV_AUTH_ALERT_KEY)) === "1";
  if (!ok && !active) {
    await sendMessage(env, chatId, `BCO auth ใช้งานไม่ได้\n\nสาเหตุ: ${detail}`);
    await env.BCO_BOT_KV.put(KV_AUTH_ALERT_KEY, "1");
  } else if (ok && active) {
    await sendMessage(env, chatId, "BCO auth กลับมาใช้งานได้แล้ว");
    await env.BCO_BOT_KV.put(KV_AUTH_ALERT_KEY, "0");
  }
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);
    if (url.pathname === "/health") {
      return Response.json({ ok: true, service: "bco-telegram-bot" });
    }

    if (url.pathname === "/telegram/webhook" && request.method === "POST") {
      const secret = (env.TELEGRAM_WEBHOOK_SECRET || "").trim();
      if (secret) {
        const header = request.headers.get("x-telegram-bot-api-secret-token") || "";
        if (header !== secret) {
          return new Response("forbidden", { status: 403 });
        }
      }
      const update = (await request.json()) as TelegramUpdate;
      await processTelegramUpdate(env, update);
      return Response.json({ ok: true });
    }

    return new Response("Not found", { status: 404 });
  },

  async scheduled(controller: ScheduledController, env: Env): Promise<void> {
    if (controller.cron === "0 1 * * *") {
      await sendDailyStatus(env);
      return;
    }
    await monitorAuthHealth(env);
  },
};
