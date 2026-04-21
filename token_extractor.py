#!/usr/bin/env python3
from __future__ import annotations
"""
BCO Token Extractor
ดึง JWT token จาก Chrome Cookie โดยอัตโนมัติ
"""

import sqlite3
import os
import subprocess
import hashlib
import shutil
import json
import time
from urllib.parse import unquote
from Crypto.Cipher import AES


def get_keychain_password():
    result = subprocess.run(
        ['security', 'find-generic-password', '-ws', 'Chrome Safe Storage'],
        capture_output=True, text=True
    )
    return result.stdout.strip()


def derive_key(keychain_pass: str) -> bytes:
    return hashlib.pbkdf2_hmac('sha1', keychain_pass.encode('utf-8'), b'saltysalt', 1003, dklen=16)


def decrypt_cookie(enc_val: bytes, key: bytes) -> str | None:
    try:
        enc = bytes(enc_val)
        if enc[:3] == b'v10':
            enc = enc[3:]
        iv = b' ' * 16
        cipher = AES.new(key, AES.MODE_CBC, IV=iv)
        decrypted = cipher.decrypt(enc)
        raw_str = decrypted.decode('latin-1')
        if '%7B' in raw_str:
            idx = raw_str.find('%7B')
            clean = raw_str[idx:].rstrip('\x00\x01\x02\x03\x04\x05\x06\x07\x08\x09\x0a\x0b\x0c\x0d\x0e\x0f\x10\x11\x12\x13\x14\x15\x16\x17\x18\x19\x1a\x1b\x1c\x1d\x1e\x1f')
            return unquote(clean)
        return None
    except Exception:
        return None


def get_bco_tokens(chrome_profile: str = "Profile 3") -> dict | None:
    """
    ดึง accessToken + refreshToken จาก Chrome cookie
    Returns: {"accessToken": "...", "refreshToken": "...", "exp": timestamp} or None
    """
    base = os.path.expanduser(f"~/Library/Application Support/Google/Chrome/{chrome_profile}")
    cookie_db = os.path.join(base, "Cookies")

    if not os.path.exists(cookie_db):
        return None

    # Copy to avoid lock
    tmp_db = '/tmp/bco_chrome_cookies_tmp.db'
    shutil.copy(cookie_db, tmp_db)

    keychain_pass = get_keychain_password()
    key = derive_key(keychain_pass)

    conn = sqlite3.connect(tmp_db)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT name, encrypted_value FROM cookies WHERE host_key LIKE '%bco.bangkok.go.th%' AND name='auth'"
    )
    row = cursor.fetchone()
    conn.close()

    if not row:
        return None

    name, enc_val = row
    decrypted = decrypt_cookie(enc_val, key)

    if not decrypted:
        return None

    try:
        data = json.loads(decrypted)
        return {
            "accessToken": data.get("accessToken", ""),
            "refreshToken": data.get("refreshToken", ""),
            "exp": data.get("user", {}).get("exp", 0)
        }
    except Exception:
        return None


def get_valid_token(cache_file: str = "/tmp/bco_active_token.json") -> str | None:
    """
    คืน access token ที่ยังใช้ได้
    - ถ้ามี cache และยังไม่ expire -> คืน cache
    - ถ้า expire -> ดึงใหม่จาก Chrome
    """
    now = int(time.time())

    # ลอง cache ก่อน
    if os.path.exists(cache_file):
        try:
            with open(cache_file) as f:
                cached = json.load(f)
            if cached.get("exp", 0) > now + 300:  # เหลืออย่างน้อย 5 นาที
                return cached["accessToken"]
        except Exception:
            pass

    # ดึงจาก Chrome
    tokens = get_bco_tokens()
    if tokens and tokens["exp"] > now + 300:
        with open(cache_file, 'w') as f:
            json.dump(tokens, f)
        return tokens["accessToken"]

    return None


if __name__ == "__main__":
    token = get_valid_token()
    if token:
        import base64
        payload = token.split('.')[1]
        payload += '=' * (-len(payload) % 4)
        info = json.loads(base64.b64decode(payload))
        exp_str = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(info['exp']))
        print(f"Token OK - user_id={info['user_id']}, expires={exp_str}")
        print(f"Token: {token[:50]}...")
    else:
        print("ไม่พบ token ที่ใช้ได้ - กรุณาเปิด Chrome และ login ที่ bco.bangkok.go.th ก่อน")
