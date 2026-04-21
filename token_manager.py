from __future__ import annotations

"""
token_manager.py — BCO token management

Supported recovery paths, in order:
1. Reuse cached token if JWT `exp` is still valid.
2. Extract fresh token data from the Chrome cookie on macOS.
3. Refresh using the cached refresh token.
4. Login directly with credentials from env vars.
5. Fall back to a raw access token in /tmp/bco_token.txt.
"""

import argparse
import base64
import hashlib
import json
import os
import platform
import shutil
import sqlite3
import subprocess
import tempfile
import time
import urllib.request
from typing import Any
from urllib.parse import unquote

import requests

BCO_API_BASE = "https://bco-api.bangkok.go.th/api/v1"
BCO_WEB_URL = "https://bco.bangkok.go.th/officer"
TOKEN_CACHE_PATH = os.path.join(tempfile.gettempdir(), "bco_token_cache.json")
TOKEN_FALLBACK_PATH = os.path.join(tempfile.gettempdir(), "bco_token.txt")
DEFAULT_MIN_TTL = 300
RUNTIME_OTP_TTL = 120

_RUNTIME_OTP_CODE: str | None = None
_RUNTIME_OTP_SET_AT: float | None = None


def _decode_jwt_payload(token: str) -> dict[str, Any]:
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload))
    except Exception:
        return {}


def _coerce_exp(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _extract_exp(token_data: dict[str, Any]) -> int | None:
    user = token_data.get("user")
    if isinstance(user, dict):
        exp = _coerce_exp(user.get("exp"))
        if exp:
            return exp

    access_token = token_data.get("accessToken")
    if isinstance(access_token, str) and access_token:
        return _coerce_exp(_decode_jwt_payload(access_token).get("exp"))

    return None


def _normalise_token_data(token_data: dict[str, Any], *, fetched_at: float | None = None) -> dict[str, Any]:
    data = dict(token_data)
    data["accessToken"] = data.get("accessToken") or data.get("access_token") or ""
    data["refreshToken"] = data.get("refreshToken") or data.get("refresh_token") or ""
    data["exp"] = _extract_exp(data)
    data["fetchedAt"] = fetched_at if fetched_at is not None else time.time()
    return data


def _token_is_valid(token: str | None, *, min_ttl: int = DEFAULT_MIN_TTL) -> bool:
    if not token:
        return False
    exp = _coerce_exp(_decode_jwt_payload(token).get("exp"))
    if exp is None:
        return False
    return exp > int(time.time()) + min_ttl


def _token_data_is_valid(token_data: dict[str, Any] | None, *, min_ttl: int = DEFAULT_MIN_TTL) -> bool:
    if not token_data:
        return False
    return _token_is_valid(token_data.get("accessToken"), min_ttl=min_ttl)


def _load_json(path: str) -> dict[str, Any] | None:
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            data = json.load(f)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    return _normalise_token_data(data)


def _save_cache(token_data: dict[str, Any]) -> None:
    data = _normalise_token_data(token_data)
    try:
        with open(TOKEN_CACHE_PATH, "w") as f:
            json.dump(data, f)
    except Exception as exc:
        print(f"[token_manager] Could not write token cache: {exc}")


def invalidate_cache() -> None:
    try:
        os.remove(TOKEN_CACHE_PATH)
    except FileNotFoundError:
        return
    except Exception as exc:
        print(f"[token_manager] Could not remove token cache: {exc}")


def set_runtime_otp_code(code: str) -> None:
    global _RUNTIME_OTP_CODE, _RUNTIME_OTP_SET_AT
    _RUNTIME_OTP_CODE = code.strip()
    _RUNTIME_OTP_SET_AT = time.time()


def clear_runtime_otp_code() -> None:
    global _RUNTIME_OTP_CODE, _RUNTIME_OTP_SET_AT
    _RUNTIME_OTP_CODE = None
    _RUNTIME_OTP_SET_AT = None


def _get_runtime_otp_code() -> str | None:
    global _RUNTIME_OTP_CODE, _RUNTIME_OTP_SET_AT
    if not _RUNTIME_OTP_CODE or not _RUNTIME_OTP_SET_AT:
        return None
    if time.time() - _RUNTIME_OTP_SET_AT > RUNTIME_OTP_TTL:
        clear_runtime_otp_code()
        return None
    return _RUNTIME_OTP_CODE


def _unwrap_auth_payload(payload: dict[str, Any]) -> dict[str, Any]:
    data = payload.get("data")
    if isinstance(data, dict) and (data.get("accessToken") or data.get("access_token")):
        return data
    return payload


def _chrome_user_data_root() -> str | None:
    system = platform.system()
    if system == "Darwin":
        return os.path.expanduser("~/Library/Application Support/Google/Chrome")
    if system == "Windows":
        local_appdata = os.getenv("LOCALAPPDATA", "").strip()
        if not local_appdata:
            return None
        return os.path.join(local_appdata, "Google", "Chrome", "User Data")
    return None


def _chrome_cookie_db_path(chrome_profile: str) -> str | None:
    root = _chrome_user_data_root()
    if not root:
        return None
    if platform.system() == "Darwin":
        return os.path.join(root, chrome_profile, "Cookies")
    if platform.system() == "Windows":
        return os.path.join(root, chrome_profile, "Network", "Cookies")
    return None


def _chrome_local_state_path() -> str | None:
    root = _chrome_user_data_root()
    if not root:
        return None
    return os.path.join(root, "Local State")


def _windows_cookie_key() -> bytes:
    try:
        import win32crypt
    except ImportError as exc:
        raise RuntimeError("pywin32 is required for Windows Chrome cookie extraction") from exc

    local_state_path = _chrome_local_state_path()
    if not local_state_path or not os.path.exists(local_state_path):
        raise FileNotFoundError("Chrome Local State file not found")

    with open(local_state_path, encoding="utf-8") as f:
        local_state = json.load(f)

    encrypted_key_b64 = (
        local_state.get("os_crypt", {}).get("encrypted_key") or ""
    )
    if not encrypted_key_b64:
        raise RuntimeError("Chrome encrypted key not found in Local State")

    encrypted_key = base64.b64decode(encrypted_key_b64)
    if encrypted_key.startswith(b"DPAPI"):
        encrypted_key = encrypted_key[5:]
    return win32crypt.CryptUnprotectData(encrypted_key, None, None, None, 0)[1]


def _decrypt_windows_cookie_value(enc_val: bytes, key: bytes) -> str | None:
    try:
        import win32crypt
        from Crypto.Cipher import AES

        raw = bytes(enc_val)
        if raw[:3] in (b"v10", b"v11", b"v20"):
            nonce = raw[3:15]
            ciphertext = raw[15:-16]
            tag = raw[-16:]
            return AES.new(key, AES.MODE_GCM, nonce=nonce).decrypt_and_verify(ciphertext, tag).decode("utf-8")

        return win32crypt.CryptUnprotectData(raw, None, None, None, 0)[1].decode("utf-8")
    except Exception:
        return None


def _get_bco_tokens_windows(chrome_profile: str) -> dict[str, Any]:
    cookie_db = _chrome_cookie_db_path(chrome_profile)
    if not cookie_db or not os.path.exists(cookie_db):
        raise FileNotFoundError(f"Chrome Cookies database not found at: {cookie_db}")

    tmp_db = os.path.join(tempfile.gettempdir(), "bco_chrome_windows_tmp.db")
    shutil.copy(cookie_db, tmp_db)

    conn = sqlite3.connect(tmp_db)
    row = conn.execute(
        "SELECT encrypted_value FROM cookies "
        "WHERE host_key LIKE '%bco.bangkok.go.th%' AND name='auth'"
    ).fetchone()
    conn.close()

    if row is None:
        raise RuntimeError(
            "BCO 'auth' cookie not found in Chrome. "
            "Log in to bco.bangkok.go.th in the selected Chrome profile first."
        )

    decrypted = _decrypt_windows_cookie_value(row[0], _windows_cookie_key())
    if not decrypted:
        raise RuntimeError("Could not decrypt Chrome auth cookie on Windows")

    return _normalise_token_data(json.loads(decrypted))


def _chrome_debug_port() -> int:
    value = os.getenv("BCO_CHROME_DEBUG_PORT", "").strip()
    try:
        port = int(value)
    except ValueError:
        port = 9223
    return port


def _can_connect_debug_port(port: int) -> bool:
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=1).read()
        return True
    except Exception:
        return False


def _chrome_executable_path() -> str | None:
    candidates = [
        os.path.join(os.getenv("PROGRAMFILES", r"C:\Program Files"), "Google", "Chrome", "Application", "chrome.exe"),
        os.path.join(os.getenv("PROGRAMFILES(X86)", r"C:\Program Files (x86)"), "Google", "Chrome", "Application", "chrome.exe"),
    ]
    for path in candidates:
        if os.path.exists(path):
            return path
    return None


def _read_bco_auth_from_debugger(port: int) -> dict[str, Any] | None:
    try:
        import websocket
    except ImportError:
        return None

    try:
        tabs = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=5).read())
    except Exception:
        return None

    bco_tab = next((tab for tab in tabs if "bco.bangkok.go.th" in tab.get("url", "")), None)
    if not bco_tab:
        return None

    ws = websocket.create_connection(bco_tab["webSocketDebuggerUrl"], timeout=10)
    try:
        ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate", "params": {"expression": "localStorage.getItem('auth')"}}))
        response = json.loads(ws.recv())
    finally:
        ws.close()

    auth_raw = response.get("result", {}).get("result", {}).get("value")
    if not auth_raw:
        return None

    try:
        return _normalise_token_data(json.loads(auth_raw))
    except Exception:
        return None


def _get_bco_tokens_via_remote_debugging(chrome_profile: str) -> dict[str, Any]:
    if platform.system() != "Windows":
        raise RuntimeError("Remote debugging fallback is only configured for Windows")

    port = _chrome_debug_port()
    if not _can_connect_debug_port(port):
        chrome_exe = _chrome_executable_path()
        root = _chrome_user_data_root()
        if not chrome_exe or not root:
            raise RuntimeError("Chrome executable or profile root not found")

        subprocess.Popen(
            [
                chrome_exe,
                f"--remote-debugging-port={port}",
                "--remote-allow-origins=*",
                f"--user-data-dir={root}",
                f"--profile-directory={chrome_profile}",
                "--no-first-run",
                "--no-default-browser-check",
                BCO_WEB_URL,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    deadline = time.time() + 20
    while time.time() < deadline:
        token_data = _read_bco_auth_from_debugger(port)
        if token_data and token_data.get("accessToken"):
            return token_data
        time.sleep(1)

    raise RuntimeError("Could not read BCO auth from Chrome localStorage via remote debugging")


def get_bco_tokens(chrome_profile: str = "Profile 3") -> dict[str, Any]:
    """
    Extract BCO auth tokens directly from the Chrome browser profile.
    Returns a dict with at least accessToken + refreshToken.
    """
    if platform.system() == "Windows":
        windows_errors: list[str] = []
        for extractor in (_get_bco_tokens_windows, _get_bco_tokens_via_remote_debugging):
            try:
                return extractor(chrome_profile)
            except Exception as exc:
                windows_errors.append(str(exc))
        raise RuntimeError("; ".join(windows_errors))

    try:
        from Crypto.Cipher import AES
    except ImportError as exc:
        raise ImportError("pycryptodome is required: pip install pycryptodome") from exc

    result = subprocess.run(
        ["security", "find-generic-password", "-ws", "Chrome Safe Storage"],
        capture_output=True,
        text=True,
    )
    keychain_password = result.stdout.strip()
    if not keychain_password:
        raise RuntimeError(
            "Could not retrieve Chrome Safe Storage password from Keychain. "
            "Ensure Chrome has launched and Keychain access is granted."
        )

    key = hashlib.pbkdf2_hmac("sha1", keychain_password.encode(), b"saltysalt", 1003, dklen=16)

    db_path = os.path.expanduser(
        f"~/Library/Application Support/Google/Chrome/{chrome_profile}/Cookies"
    )
    if not os.path.exists(db_path):
        raise FileNotFoundError(
            f"Chrome Cookies database not found at: {db_path}\n"
            f"Check that CHROME_PROFILE='{chrome_profile}' is correct."
        )

    tmp_db = "/tmp/bco_chrome_tmp.db"
    shutil.copy(db_path, tmp_db)

    conn = sqlite3.connect(tmp_db)
    row = conn.execute(
        "SELECT encrypted_value FROM cookies "
        "WHERE host_key LIKE '%bco.bangkok.go.th%' AND name='auth'"
    ).fetchone()
    conn.close()

    if row is None:
        raise RuntimeError(
            "BCO 'auth' cookie not found in Chrome. "
            "Log in to bco.bangkok.go.th in the selected Chrome profile first."
        )

    enc = bytes(row[0])[3:]
    decrypted = AES.new(key, AES.MODE_CBC, IV=b" " * 16).decrypt(enc).decode("latin-1")

    idx = decrypted.find("%7B")
    if idx == -1:
        raise ValueError("Could not locate JSON payload inside decrypted cookie.")

    raw = decrypted[idx:]
    raw = raw.rstrip("".join(chr(i) for i in range(0, 32)))
    return _normalise_token_data(json.loads(unquote(raw)))


def _post_json(url: str, payload: dict[str, Any]) -> requests.Response:
    return requests.post(url, json=payload, timeout=15)


def try_refresh_token(access_token: str, refresh_token: str) -> dict[str, Any] | None:
    url = f"{BCO_API_BASE}/auth/refresh_token"
    try:
        resp = _post_json(
            url,
            {"access_token": access_token, "refresh_token": refresh_token},
        )
    except Exception as exc:
        print(f"[token_manager] refresh_token request failed: {exc}")
        return None

    if resp.status_code != 200:
        print(f"[token_manager] refresh_token failed: HTTP {resp.status_code}")
        return None

    return _normalise_token_data(_unwrap_auth_payload(resp.json()))


def _generate_totp(secret: str) -> str:
    try:
        import pyotp
    except ImportError as exc:
        raise RuntimeError(
            "pyotp is required for officer auto-login. Install with: pip install pyotp"
        ) from exc
    return pyotp.TOTP(secret).now()


def login_with_password(
    username: str,
    password: str,
    *,
    mode: str = "backoffice",
    otp: str | None = None,
) -> dict[str, Any] | None:
    mode = mode.lower().strip()
    if mode == "officer":
        endpoint = "/auth/login/sso"
        payload = {"username": username, "password": password, "otp": otp or ""}
    elif mode == "backoffice":
        endpoint = "/auth/login"
        payload = {"username": username, "password": password}
    else:
        raise ValueError("mode must be 'backoffice' or 'officer'")

    url = f"{BCO_API_BASE}{endpoint}"
    try:
        resp = _post_json(url, payload)
    except Exception as exc:
        print(f"[token_manager] direct login failed: {exc}")
        return None

    if resp.status_code != 200:
        print(f"[token_manager] direct login failed: HTTP {resp.status_code}")
        return None

    return _normalise_token_data(_unwrap_auth_payload(resp.json()))


def _login_from_env() -> dict[str, Any] | None:
    username = os.getenv("BCO_USERNAME", "").strip()
    password = os.getenv("BCO_PASSWORD", "").strip()
    if not username or not password:
        return None

    otp_secret = os.getenv("BCO_TOTP_SECRET", "").strip()
    otp_code = _get_runtime_otp_code() or os.getenv("BCO_OTP_CODE", "").strip()
    mode = os.getenv("BCO_LOGIN_MODE", "").strip().lower()
    if not mode:
        mode = "officer" if (otp_secret or otp_code) else "backoffice"

    otp = None
    if mode == "officer":
        if otp_secret:
            otp = _generate_totp(otp_secret)
        elif otp_code:
            otp = otp_code
        else:
            print("[token_manager] Officer flow requested but OTP is unavailable; will try backoffice fallback.")

    attempts: list[tuple[str, str | None]] = []
    if mode == "officer":
        if otp:
            attempts.append(("officer", otp))
        attempts.append(("backoffice", None))
    elif mode == "backoffice":
        attempts.append(("backoffice", None))
        if otp:
            attempts.append(("officer", otp))
    else:
        if otp:
            attempts.append(("officer", otp))
        attempts.append(("backoffice", None))

    seen: set[str] = set()
    for attempt_mode, attempt_otp in attempts:
        if attempt_mode in seen:
            continue
        seen.add(attempt_mode)
        print(f"[token_manager] Attempting credential login via {attempt_mode} flow...")
        token_data = login_with_password(username, password, mode=attempt_mode, otp=attempt_otp)
        if _token_data_is_valid(token_data):
            return token_data

    return None


def _load_fallback_token() -> str | None:
    if not os.path.exists(TOKEN_FALLBACK_PATH):
        return None
    try:
        token = open(TOKEN_FALLBACK_PATH).read().strip()
    except Exception:
        return None
    if token:
        print(f"[token_manager] Using fallback token from {TOKEN_FALLBACK_PATH}")
        return token
    return None


def get_bco_token(chrome_profile: str = "Profile 3") -> dict[str, Any]:
    data = get_bco_tokens(chrome_profile)
    _save_cache(data)
    return data


def get_valid_token(
    chrome_profile: str = "Profile 3",
    *,
    min_ttl: int = DEFAULT_MIN_TTL,
    force_refresh: bool = False,
) -> str:
    """
    Return a valid BCO access token string.

    Strategy:
      1. Return cached token if JWT exp is still valid.
      2. Extract fresh token from Chrome cookie.
      3. Refresh using cached access + refresh token.
      4. Login directly with env credentials, if configured.
      5. Fall back to /tmp/bco_token.txt if it is still valid.
    """
    cached = None if force_refresh else _load_json(TOKEN_CACHE_PATH)
    if _token_data_is_valid(cached, min_ttl=min_ttl):
        return cached["accessToken"]

    try:
        chrome_data = get_bco_tokens(chrome_profile)
        _save_cache(chrome_data)
        if _token_data_is_valid(chrome_data, min_ttl=min_ttl):
            print("[token_manager] Token extracted from Chrome cookie.")
            return chrome_data["accessToken"]
        print("[token_manager] Chrome token found but it is already expired.")
    except Exception as exc:
        print(f"[token_manager] Chrome extraction failed: {exc}")

    stale = _load_json(TOKEN_CACHE_PATH)
    if stale and stale.get("accessToken") and stale.get("refreshToken"):
        print("[token_manager] Attempting token refresh...")
        refreshed = try_refresh_token(stale["accessToken"], stale["refreshToken"])
        if _token_data_is_valid(refreshed, min_ttl=min_ttl):
            _save_cache(refreshed)
            print("[token_manager] Token refreshed successfully.")
            return refreshed["accessToken"]

    credential_login = _login_from_env()
    if _token_data_is_valid(credential_login, min_ttl=min_ttl):
        _save_cache(credential_login)
        clear_runtime_otp_code()
        print("[token_manager] Token obtained via direct login.")
        return credential_login["accessToken"]

    fallback = _load_fallback_token()
    if _token_is_valid(fallback, min_ttl=min_ttl):
        return fallback

    raise RuntimeError(
        "Could not obtain a valid BCO token.\n"
        "• Log in to bco.bangkok.go.th in Chrome again.\n"
        "• Or configure direct login via env: BCO_USERNAME / BCO_PASSWORD "
        "(plus BCO_TOTP_SECRET for officer flow).\n"
        f"• Or place a fresh raw access token in {TOKEN_FALLBACK_PATH}."
    )


def _format_exp(token: str | None) -> str:
    exp = _coerce_exp(_decode_jwt_payload(token or "").get("exp"))
    if exp is None:
        return "unknown"
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(exp))


def main() -> int:
    parser = argparse.ArgumentParser(description="BCO token helper")
    parser.add_argument("profile", nargs="?", default="Profile 3")
    parser.add_argument("--force-refresh", action="store_true")
    parser.add_argument("--otp", help="Runtime OTP code for officer login")
    args = parser.parse_args()

    if args.otp:
        set_runtime_otp_code(args.otp)

    try:
        token = get_valid_token(args.profile, force_refresh=args.force_refresh)
    except Exception as exc:
        print(f"ERROR: {exc}")
        return 1

    print(f"Access token exp: {_format_exp(token)}")
    print(f"Access token (first 40 chars): {token[:40]}...")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
