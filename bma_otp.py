"""
bma_otp.py — สร้างรหัส OTP ของ BMA (BMA OTP app) เองโดยไม่ต้องเปิดแอปมือถือ

กลไกทั้งหมดถอดมาจากแอป BMA OTP จริง (package go.th.bma.otp, React Native + native CipherUtil):
  1. login verifiedca.bangkok.go.th (service CTCCA022) ด้วย username/password → ได้ seed
  2. ถอด seed ด้วย AES-GCM (key=encodedKey, nonce=seedId[:16], AAD=deviceId) → TOTP secret (base32)
  3. TOTP: HMAC-SHA1, step = 180 วินาที, ตัด 6 หลักท้าย

seed ผูกกับ deviceId ทีละเครื่อง — ใคร login verifiedca ทีหลังได้ seed (เตะเครื่องก่อนหน้า)
seed ไม่มีวันหมดอายุ แต่ถูก revoke เมื่อมีอุปกรณ์อื่น login ด้วย account เดียวกัน
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import time
from typing import Any

import requests
from Crypto.Cipher import AES

BCO_API_BASE = "https://bco-api.bangkok.go.th/api/v1"
VERIFIEDCA_BASE = "https://verifiedca.bangkok.go.th"
OTP_STEP_SECONDS = 180

# ค่าคงที่ประจำแอป (ฝังใน APK สาธารณะ — ทุกผู้ใช้ใช้ตัวเดียวกัน ไม่ใช่ความลับส่วนตัว)
_APP_CIPHER_BLOB = "U2FsdGVkX1/IuXNYUR1UdePS/EjPYr+Wd27lcRwDBi0="
_APP_CIPHER_PASS = b"HMWFsjZQDW2rEn0Q+PqykV4HR1XMdH+4iFBnpIYw/yE="
_ESB_GATEWAY_TOKEN = (
    "eyJhbGciOiJIUzI1NiJ9.eyJqdGkiOiIxMDU3IiwiaWF0IjoxNjY3MjEwNjUzLCJzdWIiOiJJbnRlZ3JhdGVk"
    "IFJlcXVlc3QgU2VydmljZSIsImlzcyI6ImNhdGFsb2ciLCJleHAiOjE2NjcyMTA2NTN9."
    "sTQ-lhw8myV-umzN7_8glHUBnyFWZ-_D_mwz2YcsCR0"
)


def _b64(value: str) -> bytes:
    return base64.b64decode(value + "=" * (-len(value) % 4))


def _evp_bytes_to_key(password: bytes, salt: bytes, key_len: int, iv_len: int) -> tuple[bytes, bytes]:
    data = b""
    prev = b""
    while len(data) < key_len + iv_len:
        prev = hashlib.md5(prev + password + salt).digest()
        data += prev
    return data[:key_len], data[key_len : key_len + iv_len]


def _app_key() -> bytes:
    raw = base64.b64decode(_APP_CIPHER_BLOB)
    key, iv = _evp_bytes_to_key(_APP_CIPHER_PASS, raw[8:16], 32, 16)
    passphrase = AES.new(key, AES.MODE_CBC, iv).decrypt(raw[16:])
    passphrase = passphrase[: -passphrase[-1]]
    return bytes.fromhex(hashlib.sha1(passphrase).hexdigest()[:32])


def _encrypt_with_key(plaintext: str) -> str:
    key = _app_key()
    data = plaintext.encode()
    pad = 16 - len(data) % 16
    data += bytes([pad]) * pad
    return base64.b64encode(AES.new(key, AES.MODE_ECB).encrypt(data)).decode()


def _base32_to_key(secret: str) -> bytes:
    table: dict[str, int] = {}
    for i, ch in enumerate("ABCDEFGHIJKLMNOPQRSTUVWXYZ"):
        table[ch] = i
    for i, ch in enumerate("234567"):
        table[ch] = 26 + i
    n = len(secret) * 5 // 8
    buf = [0] * n
    index = 0
    offset = 0
    for ch in secret:
        if ch not in table:
            continue
        val = table[ch]
        if index <= 3:
            index = (index + 5) % 8
            if index == 0:
                buf[offset] |= val
                offset += 1
            else:
                buf[offset] |= (val << (8 - index)) & 0xFF
        else:
            index = (index + 5) % 8
            buf[offset] |= val >> index
            offset += 1
            if offset < n:
                buf[offset] |= (val << (8 - index)) & 0xFF
    return bytes(buf)


def decrypt_totp_secret(encoded_key: str, seed_id: str, device_id: str) -> str:
    key = _b64(encoded_key)
    seed = _b64(seed_id)
    nonce, ct_tag = seed[:16], seed[16:]
    cipher = AES.new(key, AES.MODE_GCM, nonce=nonce)
    cipher.update(device_id.encode())
    plain = cipher.decrypt_and_verify(ct_tag[:-16], ct_tag[-16:])
    return plain.decode()


def generate_otp(encoded_key: str, seed_id: str, device_id: str, when: float | None = None) -> str:
    secret = decrypt_totp_secret(encoded_key, seed_id, device_id)
    counter = int((when or time.time()) // OTP_STEP_SECONDS)
    message = bytes.fromhex(("%x" % counter).rjust(16, "0"))
    digest = hmac.new(_base32_to_key(secret), message, hashlib.sha1).hexdigest().upper()
    offset = int(digest[-1], 16)
    value = int(digest[offset * 2 : offset * 2 + 8], 16) & 0x7FFFFFFF
    return str(value)[-6:]


def otp_seconds_left(when: float | None = None) -> int:
    return OTP_STEP_SECONDS - int(when or time.time()) % OTP_STEP_SECONDS


def fetch_fresh_seed(username: str, password: str, device_id: str) -> dict[str, Any]:
    """login verifiedca เพื่อดึง seed ชุดใหม่ (เตะอุปกรณ์อื่นที่ถือ seed อยู่)"""
    resp = requests.post(
        f"{VERIFIEDCA_BASE}/esb",
        json={
            "userUsername": _encrypt_with_key(username),
            "password": _encrypt_with_key(password),
            "deviceId": device_id,
        },
        headers={
            "serviceCode": "CTCCA022",
            "Content-Type": "application/json",
            "Accept-Language": "th",
            "token": _ESB_GATEWAY_TOKEN,
        },
        timeout=40,
    )
    resp.raise_for_status()
    result = resp.json().get("result") or {}
    seeds = {s.get("keyType"): s for s in result.get("cmsSeedDtoList", []) if isinstance(s, dict)}
    if "S" not in seeds:
        raise RuntimeError("verifiedca ไม่คืน SSO seed (keyType S)")
    sso = seeds["S"]
    return {"encodedKey": sso["encodedKey"], "seedId": sso["seedId"], "deviceId": device_id}


def login_bco(username: str, password: str, otp: str) -> dict[str, Any] | None:
    resp = requests.post(
        f"{BCO_API_BASE}/auth/login/sso",
        json={"username": username, "password": password, "otp": otp},
        headers={"Content-Type": "application/json"},
        timeout=40,
    )
    if resp.status_code != 200:
        return None
    payload = resp.json()
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    access = data.get("accessToken") or data.get("access_token")
    if not access:
        return None
    return {
        "accessToken": access,
        "refreshToken": data.get("refreshToken") or data.get("refresh_token", ""),
    }


def get_bco_token(
    username: str,
    password: str,
    device_id: str,
    encoded_key: str = "",
    seed_id: str = "",
) -> tuple[dict[str, Any], dict[str, str]]:
    """
    Login BCO ด้วย OTP ที่สร้างเอง คืน (token_data, seed_used)
    ถ้า seed เดิมใช้ไม่ได้ (ถูก revoke) จะดึง seed ใหม่ให้อัตโนมัติ
    """
    if encoded_key and seed_id:
        try:
            otp = generate_otp(encoded_key, seed_id, device_id)
            token = login_bco(username, password, otp)
            if token:
                return token, {"encodedKey": encoded_key, "seedId": seed_id, "deviceId": device_id}
        except Exception:
            pass

    seed = fetch_fresh_seed(username, password, device_id)
    otp = generate_otp(seed["encodedKey"], seed["seedId"], seed["deviceId"])
    token = login_bco(username, password, otp)
    if not token:
        raise RuntimeError("login BCO ด้วย OTP ที่สร้างเองไม่สำเร็จ (seed สดก็ยังไม่ผ่าน)")
    return token, seed
