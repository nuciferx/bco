# CONTEXT — คำศัพท์ระบบ auth ของบอท BCO

Glossary เท่านั้น ไม่ใช่สเปกหรือที่จดรายละเอียดโค้ด

## ระบบ login ของ BCO

- **BCO API** — `https://bco-api.bangkok.go.th/api/v1` (v2 สำหรับ action) แหล่งข้อมูลงานค้าง
- **access token** — token เข้า BCO API อายุ ~3 วัน (JWT) · **ต่ออายุไม่ได้** (`/auth/refresh_token` ตอบ invalid token)
- **officer flow** — login แบบ `POST /auth/login/sso` ต้องแนบ OTP · บัญชีของผู้ใช้เป็นแบบนี้
- **backoffice flow** — login แบบ `POST /auth/login` ไม่ต้อง OTP · บัญชีของผู้ใช้ **ใช้ไม่ได้** (400)

## ระบบ OTP ของ กทม.

- **BMA OTP** — แอปมือถือของ กทม. (package `go.th.bma.otp`) สร้างรหัส OTP 6 หลักในเครื่อง
  ใช้ account เดียวกับที่ login BCO · เขียนด้วย React Native
- **seed** — รหัสลับต่อผู้ใช้ที่ใช้สร้าง OTP (คือ TOTP secret) เก็บใน secure storage ของมือถือ
  โครงสร้าง: `{encodedKey, seedId, keyType}`
- **keyType / otpType** — seed มี 3 ชนิด: **sso** (login), **approve** (อนุมัติรายการ), **esign** (เซ็นเอกสาร)
- **cmsSeedDtoList** — รายการ seed ที่ **server ส่งกลับมาตอน login สำเร็จ** (ผ่าน service CTCCA022)
- **verifiedca** — เซิร์ฟเวอร์ CA ของ กทม. (`https://verifiedca.bangkok.go.th`) เบื้องหลังแอป BMA OTP
  คนละตัวกับ BCO API
- **ESB** — ทุกคำขอของแอป OTP ยิงเข้า endpoint เดียว `/esb` แยกด้วย header `serviceCode`
  (CTCCA022 = login, CTCCA021 = checkSumSeed, CTCCA002/003/006 = จัดการลายเซ็น)
- **deviceId** — แอปผูก seed กับรหัสอุปกรณ์ (`getUniqueId`) ส่งไปตอน login และ checkSumSeed
- **genOtp** — ฟังก์ชัน native (โค้ด Java ใน classes.dex ไม่ใช่ JS) รับ (คีย์ประจำแอป, เวลา, encodedKey, seedId)
  แล้วคืนรหัส OTP · **ยังไม่ได้ถอดกลไกภายใน**

## การสร้าง OTP เอง (ถอดจากแอป BMA OTP — 24 ส.ค. 2569)

- **bma_otp.py** — module สร้าง OTP ของ BMA เองโดยไม่ต้องเปิดแอปมือถือ
- **verifiedca login (CTCCA022)** — POST `/esb` ด้วย username/password ที่เข้ารหัสด้วยกุญแจประจำแอป → คืน seed
- **decryptTotpGCM** — ถอด TOTP secret จาก seed ด้วย AES-GCM (key=encodedKey, nonce=seedId[:16], AAD=deviceId)
- **SecretToOTP** — TOTP: HMAC-SHA1, **step 180 วินาที** (ไม่ใช่ 30), ตัด 6 หลักท้าย
- **1 account = 1 active seed** — ใคร login verifiedca ทีหลังได้ seed (เตะเครื่องก่อนหน้าหลุด) · seed ไม่มีวันหมดอายุแต่ถูก revoke เมื่อมีอุปกรณ์อื่น login
- **self-heal** — `get_bco_token()` ดึง seed ใหม่อัตโนมัติเมื่อ seed เดิมถูก revoke

## 🧱 ข้อจำกัดสำคัญ: BCO บล็อก IP ศูนย์ข้อมูล

- **login BCO (`/auth/login/sso`) ทำได้จาก IP บ้าน/มือถือเท่านั้น** — Cloudflare Worker + GitHub Actions โดน 403 (Cloudflare "Just a moment" challenge) ทดสอบยืนยันแล้ว 24 ส.ค.
- **อ่านข้อมูล (data endpoint) จาก cloud ได้** ถ้ามี Bearer token
- ⇒ ต้อง login จากเครื่องที่ IP เป็น residential แล้ว push token ขึ้น Cloudflare KV (`bco:tokens`)
- **/genotp บน Worker ยังทำงาน** (แค่สร้าง OTP ไม่ได้ยิง BCO) — ผู้ใช้ขอ OTP ไปเข้าเว็บเองได้
