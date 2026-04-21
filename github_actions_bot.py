from __future__ import annotations

import argparse
import os
import sys

import requests
from dotenv import load_dotenv

from bco_api import BCOAuthError, BCOApi
from bot import format_status_message
from token_manager import get_valid_token, set_runtime_otp_code


def _telegram_send(text: str) -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        raise RuntimeError("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are required")

    resp = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={
            "chat_id": chat_id,
            "text": text,
        },
        timeout=30,
    )
    resp.raise_for_status()


def _load_summary() -> list[dict]:
    access_token = get_valid_token()
    api = BCOApi(access_token)
    return api.get_work_summary()


def _build_auth_warning(exc: Exception) -> str:
    lines = [
        "BCO auth ใช้งานไม่ได้",
        "",
        f"สาเหตุ: {exc}",
    ]
    if not os.getenv("BCO_TOTP_SECRET", "").strip():
        lines.extend(
            [
                "",
                "ตอนนี้ GitHub Actions จะกู้ auth เองไม่ได้ถ้ายังไม่มี TOTP secret",
                "ให้ตั้ง BCO_TOTP_SECRET ใน GitHub Secrets ถ้าต้องการให้ workflow ฟื้นตัวเอง",
            ]
        )
    return "\n".join(lines)


def send_status() -> int:
    summary = _load_summary()
    _telegram_send(format_status_message(summary))
    return 0


def auth_check(*, notify_success: bool = False) -> int:
    try:
        _load_summary()
    except (BCOAuthError, RuntimeError, requests.RequestException) as exc:
        _telegram_send(_build_auth_warning(exc))
        return 1

    if notify_success:
        _telegram_send("BCO auth ปกติ")
    return 0


def main() -> int:
    load_dotenv()

    parser = argparse.ArgumentParser(description="GitHub Actions helper for BCO Telegram notifications")
    parser.add_argument("mode", choices=("status", "auth-check"))
    parser.add_argument("--otp", help="Runtime OTP for officer login")
    parser.add_argument("--notify-success", action="store_true")
    args = parser.parse_args()

    if args.otp:
        set_runtime_otp_code(args.otp)

    if args.mode == "status":
        return send_status()
    if args.mode == "auth-check":
        return auth_check(notify_success=args.notify_success)
    return 2


if __name__ == "__main__":
    sys.exit(main())
