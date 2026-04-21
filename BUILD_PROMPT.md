# BCO Telegram Bot — Build Prompt

## Overview

Build a Telegram notification bot for the **BCO Bangkok Building Control** system (`bco.bangkok.go.th/officer`).  
Output all files to `/Users/nucifer/bco_bot/`.

---

## Authentication

### Two Login Paths

| Path | Endpoint | Fields | OTP |
|------|----------|--------|-----|
| Officer (`/officer/auth/login`) | `POST /api/v1/auth/login/sso` | `{username, password, otp}` | ✅ Required (TOTP) |
| Backoffice (`/backoffice/auth/login`) | `POST /api/v1/auth/login` | `{username, password}` | ❌ None |

- **API base**: `https://bco-api.bangkok.go.th/api/v1/`
- Token refresh: `POST /api/v1/auth/refresh_token` with `{access_token, refresh_token}`
- Access token: ~3-day lifetime
- Token is stored in Chrome cookie `auth` on `bco.bangkok.go.th` as Pinia state JSON

### Chrome Cookie Extraction (macOS — already proven working)

```python
import sqlite3, os, subprocess, hashlib, shutil, json
from urllib.parse import unquote
from Crypto.Cipher import AES

def get_bco_tokens(chrome_profile="Profile 3"):
    keychain = subprocess.run(
        ['security', 'find-generic-password', '-ws', 'Chrome Safe Storage'],
        capture_output=True, text=True
    ).stdout.strip()
    key = hashlib.pbkdf2_hmac('sha1', keychain.encode(), b'saltysalt', 1003, dklen=16)
    db = os.path.expanduser(
        f"~/Library/Application Support/Google/Chrome/{chrome_profile}/Cookies"
    )
    shutil.copy(db, '/tmp/bco_chrome_tmp.db')
    conn = sqlite3.connect('/tmp/bco_chrome_tmp.db')
    row = conn.execute(
        "SELECT encrypted_value FROM cookies "
        "WHERE host_key LIKE '%bco.bangkok.go.th%' AND name='auth'"
    ).fetchone()
    conn.close()
    enc = bytes(row[0])[3:]  # strip 'v10' prefix
    decrypted = AES.new(key, AES.MODE_CBC, IV=b' ' * 16).decrypt(enc).decode('latin-1')
    idx = decrypted.find('%7B')
    strip_chars = ''.join(chr(i) for i in range(32))
    val = unquote(decrypted[idx:].rstrip(strip_chars))
    data = json.loads(val)
    # data keys: accessToken, refreshToken, rememberToken, user, inverval
    return data
```

The cookie JSON structure:
```json
{
  "accessToken": "eyJ...",
  "refreshToken": "eyJ...",
  "rememberToken": "",
  "user": { "user_id": 149, "exp": 1776128052, "department_id": 3, "is_officer": true },
  "inverval": { "now": "...", "exp": "...", "minutes": 3484 }
}
```

---

## API Endpoints

All require header: `Authorization: Bearer <access_token>`

### `GET /users?page=1&limit=200`
Returns all 100 officers. Relevant fields per item:
```json
{
  "id": 149,
  "first_name": "ปฐมรัฐ",
  "last_name": "ฟักสุวรรณ",
  "username": "eng117",
  "roles": [{ "id": 5, "name": "วิศวกร" }],
  "status": true
}
```

### `GET /form?form_status_id=1&per_page=500`
Returns all ~6004 active forms (สคอ.). Relevant fields:
```json
{
  "user_owner": 149,
  "day_remaining": -5,
  "form_status_id": 1
}
```
- `day_remaining < 0` → overdue
- `day_remaining >= 0` → on time
- `day_remaining == null` → inactive (exclude)
- `day_remaining < -30` → critical

### `GET /users/me`
Current logged-in user info.

---

## Files to Create

### `token_manager.py`

Functions:
- `get_bco_tokens_from_chrome(profile="Profile 3")` — extract from Chrome using code above
- `try_refresh(access_token, refresh_token)` — calls `POST /auth/refresh_token`
- `get_valid_token(cache_path="/tmp/bco_token_cache.json")` — returns valid access token:
  1. Load cache if not expired (exp > now + 300s)
  2. If expired/missing → extract from Chrome
  3. If Chrome fails → try refresh endpoint
  4. Fallback: read `/tmp/bco_token.txt`
  5. Return None if all fail

---

### `bco_api.py`

Class `BCOApi`:
```python
class BCOApi:
    BASE = "https://bco-api.bangkok.go.th/api/v1"

    def __init__(self, token: str): ...
    def get_all_users(self) -> list[dict]: ...        # GET /users?page=1&limit=200
    def get_all_forms(self) -> list[dict]: ...        # GET /form?form_status_id=1&per_page=500
    def get_work_summary(self) -> list[dict]: ...
```

`get_work_summary()` joins users + forms and returns:
```python
[
  {
    "id": 149,
    "name": "ปฐมรัฐ ฟักสุวรรณ",
    "role": "วิศวกร",           # role name from roles[]
    "total": 45,               # forms with day_remaining not null
    "overdue": 12,             # day_remaining < 0
    "critical": 3,             # day_remaining < -30
    "near": 5,                 # 0 <= day_remaining <= 7
  }
]
```

Filter to only include roles: `วิศวกร`, `นายตรวจ`  
Sort by `overdue` descending.

---

### `bot.py`

Telegram bot using `python-telegram-bot` v20+ (async).

**Config** via `.env`:
```
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
CHROME_PROFILE=Profile 3
```

**Commands**:

| Command | Action |
|---------|--------|
| `/status` | Summary table for all วิศวกร + นายตรวจ |
| `/top` | Top 5 most overdue officers |
| `/officer <id>` | Detailed info for one officer by user_id |
| `/refresh` | Force re-extract token from Chrome |

**Scheduled job**: Every day at **08:00 Asia/Bangkok**, auto-send `/status` to `TELEGRAM_CHAT_ID`.

**Message format** (Thai, Telegram MarkdownV2):
```
📊 *สรุปงานเจ้าหน้าที่ BCO*
วันที่ 11/04/2569

👷 *วิศวกร*
• ปฐมรัฐ ฟักสุวรรณ — งาน: 45 เกิน: 12 วิกฤต: 3 ใกล้: 5
• ...

🔍 *นายตรวจ*
• พัฒนเทพ เครือชะเอม — งาน: 532 เกิน: 512 วิกฤต: 268 ใกล้: 8
• ...

⚠️ *รวม* เกิน: 524 | วิกฤต \(>30วัน\): 271
```

---

### `requirements.txt`
```
python-telegram-bot[job-queue]>=20.0
pycryptodome
python-dotenv
requests
```

### `.env.example`
```
TELEGRAM_BOT_TOKEN=your_bot_token_here
TELEGRAM_CHAT_ID=your_chat_id_here
CHROME_PROFILE=Profile 3
```

### `README.md` (Thai)
Setup steps:
1. `pip install -r requirements.txt`
2. Copy `.env.example` → `.env` และใส่ค่า
3. `python bot.py`
4. ต้องเปิด Chrome ที่ login bco.bangkok.go.th ไว้ก่อน (สำหรับ token auto-extract)

---

## Notes

- macOS only — uses `security` CLI for Keychain
- `pycryptodome` → `from Crypto.Cipher import AES`
- Use `requests` (sync), not aiohttp
- Handle token expiry gracefully with warning messages
- Date display in Thai Buddhist calendar (พ.ศ. = ค.ศ. + 543)
- If token extraction fails, send Telegram alert: "⚠️ กรุณา login BCO ใน Chrome ก่อน"
