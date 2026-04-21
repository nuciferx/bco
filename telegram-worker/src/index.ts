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

interface TelegramUpdate {
  update_id: number;
  message?: TelegramMessage;
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
const KV_TOKEN_KEY = "bco:tokens";
const KV_AUTH_ALERT_KEY = "bco:auth_alert_active";

function telegramRequest(token: string, method: string, body: Record<string, unknown>): Promise<Response> {
  return fetch(`https://api.telegram.org/bot${token}/${method}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

async function sendMessage(env: Env, chatId: number | string, text: string): Promise<void> {
  const resp = await telegramRequest(env.TELEGRAM_BOT_TOKEN, "sendMessage", {
    chat_id: chatId,
    text,
  });
  if (!resp.ok) {
    throw new Error(`Telegram sendMessage failed: HTTP ${resp.status}`);
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
  const otpCode = (env.BCO_OTP_CODE || "").trim();
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

async function getAllUsers(env: Env): Promise<Dict[]> {
  return extractItems(await bcoGet(env, "/users?page=1&limit=200"));
}

async function getAllForms(env: Env): Promise<Dict[]> {
  return extractItems(await bcoGet(env, "/form?form_status_id=1&per_page=10000&page=1"));
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

async function handleCommand(env: Env, message: TelegramMessage): Promise<string> {
  const text = (message.text || "").trim();
  const [command, ...args] = text.split(/\s+/);
  const query = args.join(" ").trim();

  if (command === "/start" || command === "/help") {
    return [
      "BCO Worker Bot",
      "",
      "/status - สรุปงานทั้งหมด",
      "/top - 5 คนที่งานเกินกำหนดสูงสุด",
      "/officer <id|username|ชื่อ> - ดูรายละเอียดเจ้าหน้าที่",
      "/tasks <id|username|ชื่อ> - ดูงานของเจ้าหน้าที่",
      "/refresh - บังคับ refresh token แล้วสรุปใหม่",
      "/chatid - ดู chat id",
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
    return formatStatusMessage(await getWorkSummary(env));
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
    return formatTasksMessage(officer, forms);
  }

  return "ไม่รู้จักคำสั่งนี้ ใช้ /help";
}

async function processTelegramUpdate(env: Env, update: TelegramUpdate): Promise<void> {
  const message = update.message;
  if (!message?.chat?.id || !message.text) return;
  try {
    const reply = await handleCommand(env, message);
    await sendMessage(env, message.chat.id, reply);
  } catch (error) {
    const detail = error instanceof Error ? error.message : String(error);
    await sendMessage(env, message.chat.id, `BCO bot error\n\n${detail}`);
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
