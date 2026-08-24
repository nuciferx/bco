# log — BCO Telegram Bot

## 24 ส.ค. 2569 — กู้บอทกลับมาใช้งาน + จดความรู้เรื่อง auth

**ปัญหา:** บอทตายมาตั้งแต่ประมาณ เม.ย. เข้า BCO ไม่ได้

**ที่ทำ:**
- ตรวจแล้วพบระบบ BCO ปกติ (เว็บ 200, API ตอบ 401 ถูกต้อง) และ Worker ยังออนไลน์ — ปัญหาอยู่ที่ auth ล้วน ๆ
- ล็อกอินไม่ได้ทั้ง 3 ทาง: ไม่มี cookie ใน Chrome โปรไฟล์ไหนเลย · ไม่มี token cache · direct login ติด OTP
- ผู้ใช้ส่ง OTP มา → ล็อกอินผ่าน ได้ token อายุ 3 วัน → ดึง `/top` ได้ข้อมูลจริง
- ยัด token เข้า Cloudflare KV → Worker ใช้ได้ แต่ระหว่างทาง token ใน KV หายไปรอบหนึ่ง
- ผู้ใช้ส่ง OTP อีกครั้ง → กลับมาใช้งานได้ ผู้ใช้ยืนยัน "เข้าได้"

**ที่เรียนรู้ (ทั้งที่ได้ผลและไม่ได้ผล):**
- ❌ **ต่ออายุ token ไม่ได้** — ลอง 9 รูปแบบ ตอบ `invalid token` หมด
  ถึงขั้นโหลดโค้ดเว็บ BCO ตัวจริงมาอ่าน พบว่าเว็บเรียกเหมือนเราเป๊ะ ต่างแค่เว็บส่ง cookie `auth` ไปด้วย
- ❌ **ไม่มีทางดึง TOTP secret ออกจากระบบ** — ยิงหา 17 endpoint 404 หมด
- ❌ **ทฤษฎีที่ผิด: คิดว่า BCO ผูก token กับ IP** — ผิด ยัด token ข้ามเครื่องใช้ได้จริง
  (KV เก็บ token ตัวเดียวกับที่สร้างจากเครื่องแล้วใช้งานได้ปกติ)
- 🪤 **`/refresh` เป็นกับดัก** — ลบ token ใน KV ทิ้งแล้วบังคับ login ใหม่ ถ้าไม่มี OTP อยู่ในมือ บอทตายทันที
- 🐛 `token_manager.py` ไม่เคยเรียก `load_dotenv()` — รันเป็น CLI แล้วมองไม่เห็น `.env` เงียบ ๆ (แก้แล้ว)
- 📦 `pyotp` กับ `staticmap` ไม่เคยถูกติดตั้งในเครื่อง (ติดตั้งแล้ว)

**commit:** งานค้างจาก เม.ย. (`/polygon` + `write_map`) + งานวันนี้

## 24 ส.ค. 2569 (ต่อ) — ถอดกลไก OTP ของ BMA OTP สำเร็จ

**เป้าหมาย:** ผู้ใช้อยากให้บอท login เองไม่ต้องพิมพ์ OTP ทุก 3 วัน

**ที่ทำ (grill-with-docs → ลงมือ):**
- โหลด APK ของแอป BMA OTP (`go.th.bma.otp`) จากเซิร์ฟเวอร์ กทม. — เป็น React Native
- ถอด `index.android.bundle` + decompile `classes.dex` (androguard) จนได้ครบ:
  - verifiedca login (CTCCA022) ด้วยกุญแจประจำแอป (passphrase "jojoefarm")
  - genOtp = decryptTotpGCM (AES-GCM, AAD=deviceId) + SecretToOTP (TOTP step 180s)
- เขียน `bma_otp.py` reproduce ทั้งหมด → login BCO ผ่านจริง (OTP ที่บอทสร้างเอง)

**กำแพงที่เจอ:**
- BCO บล็อก IP ศูนย์ข้อมูล (Cloudflare Worker + GitHub Actions ได้ 403) — ทดสอบยืนยันทั้งคู่
- ⇒ login ต้องทำจากเครื่องบ้าน แล้ว push token ขึ้น KV
- seed ผูก 1 device — login verifiedca เตะแอปมือถือหลุด (แต่ self-heal ดึงคืนได้)

**เพิ่ม:** คำสั่ง `/genotp` บน Worker — ผู้ใช้ขอ OTP ไปเข้าเว็บ BCO เองได้

**ยังค้าง:** auto-refresh บนเครื่องบ้าน (ผู้ใช้เลือก "ทำแบบเดิมก่อน" = push มือ)
