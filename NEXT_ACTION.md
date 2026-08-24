# NEXT_ACTION — BCO Telegram Bot

## งานค้างเร่งด่วน: ทำ auto-refresh token บนเครื่องบ้าน

login BCO ทำได้จาก IP บ้านเท่านั้น (cloud โดน 403) — ต้องตั้งให้เครื่องนี้ refresh เอง

**ที่ยังไม่ได้ทำ (ผู้ใช้ยังไม่ตัดสิน):**
- [ ] Task Scheduler บนเครื่อง: รัน bma_otp login + push token ขึ้น KV ทุก ~6 ชม เมื่อเครื่องเปิด
- [ ] หรือสคริปต์กดเองเวลาอยาก refresh (double-click)

**วิธี refresh มือ (ใช้ได้เลยตอนนี้):**
รันจากโฟลเดอร์ bco:
```
python -c "import bma_otp,json,token_manager as tm; from dotenv import dotenv_values as V; v=V('.env'); t,s=bma_otp.get_bco_token(v['BCO_USERNAME'].strip(),v['BCO_PASSWORD'].strip(),v['BMA_DEVICE_ID'].strip(),v.get('BMA_ENCODED_KEY','').strip(),v.get('BMA_SEED_ID','').strip()); td=tm._normalise_token_data(t); open('tok.json','w').write(json.dumps({'accessToken':t['accessToken'],'refreshToken':t['refreshToken'],'exp':td['exp'],'fetchedAt':td['fetchedAt']}))"
cd telegram-worker && npx wrangler kv key put "bco:tokens" --namespace-id c5c39fdb43df4fdd9b097814208dbb59 --remote --path ../tok.json
```

---

# NEXT_ACTION — BCO Telegram Bot

## งานถัดไป: ทำปุ่ม "เสนออนุญาต" ให้กดจากแชตได้

ตอนนี้บอท**อ่านอย่างเดียว** ไม่มี POST สักที่ (ตรวจแล้วทั้ง `bot.py`, `bco_api.py`, `index.ts`)
เมนู "การดำเนินการ" ที่มีอยู่เป็นแค่หน้าแสดงข้อมูล ไม่ใช่ปุ่มกดส่ง

**ของที่ research ไว้ครบแล้ว** อยู่ใน README หัวข้อ "การดำเนินการ (Action)":

```
GET  /api/v1/form/<form_id>/action/button/v2      ดูปุ่มที่กดได้ตอนนี้
GET  /api/v1/form/<form_id>/action/<action_type>  ดู assignee ของ action นั้น
POST /api/v2/form/<form_id>/action                กดส่งงาน (v2 เท่านั้น ไม่ใช่ v1)
```

payload: `{ "action_type": 6, "assign": [{ "id": 178 }] }`

| action_type | ความหมาย |
|---|---|
| 6 | เสนออนุญาต |
| 7 | เสนอไม่อนุญาต |
| 11 | ส่งกลับพิจารณามอบหมายงานใหม่ |
| 16 | ไม่เข้าข่ายตรวจสอบ |

**เงื่อนไขก่อนกดเสนออนุญาตได้ (ขร.1):**
1. วาดผังบริเวณครบ 2 โพลิกอน (type 1 = ที่ดิน, type 2 = ตัวอาคาร) — เช็คด้วย `/polygon <form_id>` ที่ทำไว้แล้ว
2. กรอกข้อมูลหน้าดำเนินการ — **ทำในเบราว์เซอร์เท่านั้น** ยังไม่มี API

### ข้อควรระวังก่อนลงมือ

- นี่คือ **การเขียนกลับเข้าระบบราชการจริง** กดพลาดแล้วแก้ยาก
  ⇒ ต้องมีขั้นยืนยันในแชตก่อนส่งเสมอ และควรทดสอบกับเรื่องที่ไม่สำคัญก่อน
- ตามกฎ AGENTS.md: **implement ใน Worker ก่อน** แล้วค่อย sync ไป `bot.py`
- deploy แล้วต้องบันทึก version ID ลง README หัวข้อ "Deploy ล่าสุด"

## งานย่อยที่ค้างอยู่

- [ ] deploy Worker รอบใหม่ (โค้ดในเครื่องใหม่กว่าที่ deploy อยู่)
- [ ] ถ้าได้ TOTP secret จากแอป OTP ของ กทม. เมื่อไหร่ → ใส่ `.env` + `npx wrangler secret put BCO_TOTP_SECRET` แล้วจะไม่ต้องป้อน OTP อีกเลย
