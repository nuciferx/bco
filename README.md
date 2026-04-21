# BCO Telegram Bot

บอทสำหรับสรุปงานค้างของเจ้าหน้าที่ในระบบ BCO และส่งแจ้งเตือนเข้า Telegram

## สิ่งที่ทำได้

- ดึง token จาก Chrome profile ได้ทั้ง macOS และ Windows
- ตรวจ `exp` ของ JWT จริงก่อนใช้งาน ไม่ใช้ token หมดอายุซ้ำ
- รีเฟรช token อัตโนมัติเมื่อ refresh token ยังใช้ได้
- login ตรงผ่าน API ได้ถ้าตั้งค่า credential ใน `.env`
- รับ OTP แบบชั่วคราวจาก CLI หรือ Telegram private chat เพื่อ officer login
- สรุปงานของ `วิศวกร` และ `นายตรวจ`
- ส่งรายงานประจำวันเวลา 08:00 Asia/Bangkok
- แจ้งเตือนเข้า Telegram เมื่อ auth ใช้งานไม่ได้

## ติดตั้ง

1. ติดตั้ง dependency

```bash
python3 -m pip install -r requirements.txt
```

2. สร้าง `.env`

```bash
cp .env.example .env
```

3. กรอกค่าใน `.env`

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`
- `CHROME_PROFILE`
- ถ้าต้องการ bypass ตอน token หมดอายุ ให้ใส่ `BCO_USERNAME` / `BCO_PASSWORD`
- ถ้าเป็น officer flow ให้เพิ่ม `BCO_TOTP_SECRET` หรือ `BCO_OTP_CODE`

## การใช้งาน

ตรวจ token:

```bash
python3 token_manager.py
python3 token_manager.py --force-refresh
python3 token_manager.py --force-refresh --otp 123456
```

ดึงสรุปงานจาก CLI:

```bash
python3 bco_api.py --top 5
python3 bco_api.py --otp 123456 --top 5
python3 bco_api.py --officer ปฐมรัฐ
python3 bco_api.py --officer 149
python3 bco_api.py --tasks-for ปฐมรัฐ
python3 bco_api.py --form-id 4295
python3 bco_api.py --form-attachments 347872
python3 bco_api.py --r1-form-id 347872
python3 bco_api.py --r1-for ปฐมรัฐ
```

รันบอท:

```bash
python3 bot.py
```

รันบน Linux server ด้วย Docker:

```bash
cp .env.example .env
docker compose up -d --build
docker compose logs -f
```

รันบน Linux server ด้วย systemd:

```bash
sudo mkdir -p /opt/bco
sudo git clone https://github.com/nuciferx/bco.git /opt/bco
cd /opt/bco
cp .env.example .env
python3 -m pip install -r requirements.txt
sudo cp deploy/systemd/bco-bot.service /etc/systemd/system/bco-bot.service
sudo systemctl daemon-reload
sudo systemctl enable --now bco-bot
sudo systemctl status bco-bot
```

รันแบบ `air-quality` ผ่าน GitHub Actions:

```bash
# ไม่ต้องมี server ถ้าแค่ต้องการ schedule รายงาน/เช็ค auth เข้า Telegram
# workflow จะรันจาก GitHub ตามเวลาแล้วส่งหา Telegram เอง
```

รันแบบ background บน Windows ให้ bot online ตลอด:

```powershell
powershell -ExecutionPolicy Bypass -File .\install_bot_task.ps1
powershell -ExecutionPolicy Bypass -File .\status_bot.ps1
```

สคริปต์ที่มี:

- `install_bot_task.ps1` ติดตั้ง Scheduled Task ให้รันตอนเปิดเครื่องและตอน login
- `run_bot.ps1` สตาร์ตบอทแบบ background และกันไม่ให้เปิดซ้ำ
- `stop_bot.ps1` หยุด process ของบอท
- `status_bot.ps1` เช็คสถานะ task และ process ที่กำลังรัน
- ถ้ายังไม่ได้ติดตั้ง `python-telegram-bot[job-queue]` บอทจะยังออนไลน์และตอบคำสั่งได้ แต่จะปิด daily schedule ชั่วคราว
- บอทจะเช็ค `BCO auth` ทุก 30 นาที และส่ง Telegram เตือนอัตโนมัติเมื่อ token ใช้งานไม่ได้/กลับมาใช้ได้อีกครั้ง

## คำสั่งใน Telegram

- `/status` สรุปทั้งหมด
- `/top` 5 คนที่งานเกินกำหนดมากสุด
- `/officer <id|username|ชื่อ>` ดูรายละเอียดรายคน
- `/tasks <id|username|ชื่อ>` ดูรายการงานของเจ้าหน้าที่
- `/form <form_id>` ดูรายละเอียดงานเดี่ยว
- `/files <form_id>` ดูรายการไฟล์ทั้งหมดในฟอร์ม
- `/file <form_id> <key>` ส่งไฟล์แนบจากฟอร์มเข้า Telegram โดยตรง
- `/r1 <id|username|ชื่อ>` เช็คงาน `ขร.1` ว่าช่องแนบ `ร.1` มีไฟล์จริงหรือไม่
- `/otp <รหัส>` ส่ง OTP ให้บอทใช้ login แบบ officer ใน private chat
- `/chatid` ให้บอทตอบ chat id ของห้องปัจจุบัน และ private chat จะถูกบันทึกลง `.env` อัตโนมัติ
- `/refresh` ล้าง cache token แล้วลองดึงใหม่

## หมายเหตุ

- ถ้าใช้ Chrome token อย่างเดียว เมื่อ cookie หมดอายุและ refresh token ตายแล้ว ระบบจะดึงข้อมูลไม่ได้จนกว่าจะ login ใหม่
- ถ้าต้องการให้บอทกู้ตัวเองได้ ให้ตั้ง direct login ใน `.env`
- `BCO_LOGIN_MODE=backoffice` เหมาะกับบัญชีที่ login ได้โดยไม่ต้อง OTP
- `BCO_LOGIN_MODE=officer` ต้องมี TOTP secret หรือ OTP code
- ถ้าเป็น Windows และ Chrome เปิดใช้งานอยู่ อาจดึง cookie ตรงไม่ได้ในบางจังหวะ ระบบจะ fallback ไปลอง login ตรงหรือ remote debugging ให้เอง
- ถ้าเป็น officer flow แต่ไม่มี `BCO_TOTP_SECRET` ให้ใช้ `--otp 123456` ใน CLI หรือส่ง `/otp 123456` หา bot ใน private chat
- ถ้าต้องการให้ bot online ได้ยาว ๆ แบบไม่ต้องคอยส่ง OTP ใหม่ ควรใส่ `BCO_TOTP_SECRET` ใน `.env`
- ถ้าจะย้ายขึ้น Linux server จริง อย่าพึ่ง Chrome token extraction เพราะบน server ปกติไม่มี Chrome profile ให้ใช้ ควรตั้ง `BCO_USERNAME` / `BCO_PASSWORD` และถ้าจำเป็นให้ใส่ `BCO_TOTP_SECRET`
- ถ้าจะใช้แบบ `air-quality` ให้ตั้ง GitHub Secrets: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `BCO_LOGIN_MODE`, `BCO_USERNAME`, `BCO_PASSWORD`, และถ้าจำเป็น `BCO_TOTP_SECRET`
- ถ้ามี token ที่ใช้งานได้อยู่แล้วแต่ยังไม่มี `BCO_TOTP_SECRET` ให้ตั้ง `BCO_ACCESS_TOKEN` และ `BCO_REFRESH_TOKEN` เป็น GitHub Secrets ได้ด้วย เพื่อให้ workflow ใช้ refresh token ต่ออายุไปก่อน
- workflow ที่เพิ่มไว้:
  - `.github/workflows/scheduled-report.yml` ส่งรายงานทุกวันเวลา 08:00 ไทย
  - `.github/workflows/auth-monitor.yml` เช็ค auth ทุก 30 นาที และแจ้ง Telegram เมื่อเข้า BCO ไม่ได้
