# AGENTS.md — BCO Telegram Bot

คำแนะนำสำหรับ AI agent ที่ทำงานกับโปรเจกต์นี้

---

## บอทที่ใช้งานจริง

**Cloudflare Worker คือบอทหลัก** — ออนไลน์ตลอดเวลา ไม่ต้องพึ่ง process หรือ server

| | ไฟล์ | สถานะ |
|---|---|---|
| **Worker (primary)** | `telegram-worker/src/index.ts` | production — always-on |
| Python bot | `bot.py` | สำรอง / local |

ทุกฟีเจอร์ใหม่ต้อง implement ใน Worker ก่อนเสมอ แล้วค่อย sync ไป Python bot

---

## กฎการพัฒนา

### การเขียนโค้ด

- **Worker ก่อนเสมอ** — implement ใน `index.ts` แล้วค่อย sync `bot.py`
- **ไม่เพิ่มโค้ดเกินความจำเป็น** — ไม่มี feature flag, ไม่มี abstraction ที่ยังไม่ใช้, ไม่มี fallback ที่ไม่มีทางเกิด
- **ไม่เขียน comment อธิบาย what** — เขียน comment เฉพาะ why ที่ไม่ชัดเจน
- **ตั้งชื่อให้อ่านออก** — ไม่ต้องการ comment ถ้าชื่อ function/variable บอกอยู่แล้ว
- **ไม่เพิ่ม error handling ที่ไม่มีทางเกิด** — validate เฉพาะ boundary (input จาก user / response จาก API)
- **ไม่สร้างไฟล์ใหม่** ถ้าแก้ไฟล์เดิมได้

### TypeScript (Worker)

- ใช้ `unknown` แทน `any` สำหรับ API response แล้ว narrow type ก่อนใช้
- `Dict = Record<string, unknown>` ใช้สำหรับ BCO API payload
- ไม่ใช้ external npm package — Workers runtime เท่านั้น (fetch, crypto, KV)
- async/await ทุกที่ที่ fetch หรือ KV

### Python (bot.py / bco_api.py)

- type hint ทุก function signature
- ใช้ `from __future__ import annotations`
- error ที่ผ่าน boundary ให้ raise เป็น `BCOAuthError` หรือ `RuntimeError` ที่อ่านออก
- ไม่ใช้ `except: pass` ในโค้ด production

### การเพิ่มคำสั่ง Telegram ใหม่

1. เพิ่ม handler ใน `index.ts` → `handleCommand()`
2. เพิ่มในตาราง help text (`/help`)
3. sync ไป `bot.py` — เพิ่ม command function + register ใน `build_application()`
4. deploy Worker
5. อัปเดต README + AGENTS.md

### การเพิ่ม API endpoint ใหม่

1. เพิ่ม function ใน `index.ts` (ใกล้ `getFormDetail` / `getFormAttachments`)
2. เพิ่ม method ใน `bco_api.py` (class `BCOApi`)
3. บันทึก endpoint ใน README section "BCO API" และใน AGENTS.md

### Deploy

```bash
cd telegram-worker
npx wrangler deploy
```

- ห้าม deploy จาก root directory
- หลัง deploy บันทึก version ID + วันที่ + changelog ลง README section "Deploy ล่าสุด"
- ถ้าเปลี่ยน wrangler.toml (KV / cron) ให้ระบุใน changelog ด้วย

### README และ AGENTS.md

- README = สำหรับคนและ agent อ่านเพื่อเข้าใจโปรเจกต์
- AGENTS.md = กฎสำหรับ agent โดยเฉพาะ
- อัปเดตทั้งคู่เมื่อมีการเปลี่ยนแปลงที่กระทบภาพรวม

---

## กฎการทำงาน

### 1. ฟีเจอร์ใหม่
- implement ใน `telegram-worker/src/index.ts` ก่อน
- sync ไป `bot.py` ถัดไป
- deploy Worker ทันทีหลัง implement: `cd telegram-worker && npx wrangler deploy`
- บันทึก version ID + วันที่ + สิ่งที่เพิ่มลง README section "Deploy ล่าสุด"

### 2. Deploy
```bash
cd telegram-worker
npx wrangler deploy
```
ห้าม deploy จาก root directory

### 3. README เป็น source of truth
อัปเดต README ทุกครั้งที่:
- deploy Worker (บันทึก version ID)
- เพิ่มคำสั่ง Telegram ใหม่
- เพิ่ม API endpoint ใหม่
- เปลี่ยนสถาปัตยกรรม

---

## โครงสร้างไฟล์สำคัญ

```
bco_bot/
├── telegram-worker/          ← Worker (บอทหลัก)
│   ├── src/index.ts          ← logic ทั้งหมด (TypeScript)
│   ├── wrangler.toml         ← KV binding + cron config
│   └── .webhook_secret       ← Telegram webhook secret
├── bot.py                    ← Python bot (สำรอง)
├── bco_api.py                ← BCO API client
├── token_manager.py          ← token extraction + refresh + login
├── deploy/systemd/           ← Linux systemd service
├── Dockerfile / docker-compose.yml
└── *.ps1                     ← Windows Task Scheduler scripts
```

---

## BCO API

Base: `https://bco-api.bangkok.go.th/api/v1`  
**POST action ต้องใช้ v2**: `https://bco-api.bangkok.go.th/api/v2/form/<id>/action`

| Endpoint | หน้าที่ |
|---|---|
| `GET /form?form_status_id=1&per_page=10000` | รายการงานทั้งหมด |
| `GET /form/<id>` | รายละเอียดฟอร์ม |
| `GET /form/<id>/attachment` | ไฟล์แนบ |
| `GET /form/<id>/history` | ประวัติการดำเนินการ |
| `GET /form/<id>/write_map` | โพลิกอนผังบริเวณ |
| `GET /form/<id>/action/button/v2` | ปุ่ม action ที่กดได้ตอนนี้ |
| `POST /api/v2/form/<id>/action` | กดส่งงาน (v2 เท่านั้น) |
| `GET /users?page=1&limit=200` | รายชื่อเจ้าหน้าที่ |

action_type: 6=เสนออนุญาต, 7=เสนอไม่อนุญาต, 11=ส่งกลับ, 16=ไม่เข้าข่าย

---

## Worker — Production Info

- URL: `https://bco-telegram-bot.ideaplanstudio.workers.dev`
- KV: `BCO_BOT_KV` (id: `c5c39fdb43df4fdd9b097814208dbb59`)
- Cron: `0 1 * * *` (08:00 Bangkok daily report), `*/30 * * * *` (auth monitor)
- Webhook: `POST /telegram/webhook`

---

## Token Strategy (Worker)

1. KV cache → ถ้า valid ใช้เลย
2. env vars (`BCO_ACCESS_TOKEN`) → ถ้า valid ใช้เลย
3. refresh token (KV หรือ env)
4. direct login (`BCO_USERNAME` + `BCO_PASSWORD` + TOTP/OTP)

## Token Strategy (Python bot)

1. cache file `/tmp/bco_token_cache.json`
2. Chrome cookie (macOS: Keychain decrypt, Windows: DPAPI)
3. refresh token
4. direct login from `.env`
5. fallback `/tmp/bco_token.txt`

---

## ข้อจำกัดของ Worker vs Python bot

| ฟีเจอร์ | Worker | Python bot |
|---|---|---|
| render PNG polygon map | ไม่ได้ (ไม่มี staticmap/Pillow) | ได้ (`/polygon` ส่งรูป) |
| Chrome cookie extract | ไม่ได้ | ได้ |
| `/polygon` | ส่ง text + OSM link | ส่งรูป PNG |

---

## คำสั่ง Telegram ทั้งหมด

`/status` `/top` `/officer` `/tasks` `/form` `/map` `/polygon` `/building` `/files` `/file` `/r1` `/otp` `/refresh` `/chatid`

Worker มีเมนูเพิ่ม (inline buttons): ประวัติการดำเนินการ / เอกสารแนบ / การดำเนินการ

---

## ⚠️ เรื่อง auth ที่ต้องรู้ก่อนแตะ (พิสูจน์แล้ว 24 ส.ค. 2569)

1. **token อายุ 3 วัน ต่ออายุไม่ได้** — `/auth/refresh_token` ตอบ `invalid token` ทุกรูปแบบ (ลอง 9 แบบ)
   อย่าเสียเวลาไล่ซ้ำ รายละเอียดอยู่ใน README หัวข้อ "ระบบล็อกอิน — สิ่งที่พิสูจน์แล้ว"
2. **ไม่มี endpoint ให้ดึง TOTP secret** — ยิงหา 17 ที่ 404 หมด ตั้ง `BCO_TOTP_SECRET` ได้ต่อเมื่อ
   ได้ secret จากตอนตั้งค่าแอป OTP ของ กทม. เท่านั้น
3. **ห้ามแนะนำให้กด `/refresh` ตอนไม่มี OTP** — มันลบ token ใน KV ทิ้งแล้วบังคับ login ใหม่ → บอทตาย
4. **ยัด token จากเครื่องเข้า KV ได้** ไม่มีการผูก IP — คำสั่งอยู่ใน README
5. `token_manager.py` เรียก `load_dotenv()` เองแล้ว (แก้ 24 ส.ค. 2569) — เดิมรัน CLI แล้วมองไม่เห็น
   `.env` เงียบ ๆ ทำให้ direct login ไม่ทำงานโดยไม่ฟ้องอะไรเลย

### dependency ที่ต้องมีฝั่ง Python

`pyotp` (สร้าง OTP จาก TOTP secret) และ `staticmap` + `Pillow` (วาดรูป `/polygon`)
ทั้งคู่เป็น lazy import — ถ้าไม่ได้ติดตั้งจะพังตอนใช้งานจริงเท่านั้น ไม่พังตอน start
