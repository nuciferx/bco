from __future__ import annotations

import datetime as dt
import io
import logging
import os
import shutil
import subprocess
import tempfile
from collections import defaultdict
from math import ceil
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from dotenv import load_dotenv, set_key
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

from bco_api import (
    BCOApi,
    BCOAuthError,
    find_officer,
    flatten_form_detail,
    find_form_file_by_key,
    get_r1_statuses_for_officer,
    get_tasks_for_officer,
    list_form_files,
)
from token_manager import get_valid_token, invalidate_cache
from token_manager import set_runtime_otp_code

LOG_PATH = Path(__file__).with_name("bot_runtime.log")
AUTH_MONITOR_INTERVAL_MINUTES = 30
_AUTH_ALERT_ACTIVE = False
logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
    handlers=[
        logging.FileHandler(LOG_PATH, encoding="utf-8"),
        logging.StreamHandler(),
    ],
    force=True,
)
LOGGER = logging.getLogger("bco_bot")
TIMEZONE = ZoneInfo("Asia/Bangkok")
ENV_PATH = Path(__file__).with_name(".env")
TASKS_PAGE_SIZE = 8
THAI_FONT_FAMILY = "Thonburi, Tahoma, sans-serif"
MAP_DOC_HINTS = ("แผนที่ที่ตั้งอาคาร", "แผนที่", "ป้าย")
BUILDING_DOC_HINTS = ("ภาพถ่ายหน้าอาคาร", "ภาพถ่ายอาคาร", "รูปอาคาร", "หน้าอาคาร")


def _thai_buddhist_date(now: dt.datetime | None = None) -> str:
    now = now or dt.datetime.now(TIMEZONE)
    return now.strftime(f"%d/%m/{now.year + 543}")


def _group_by_role(summary: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in summary:
        grouped[row["role"]].append(row)
    return grouped


def format_status_message(summary: list[dict[str, Any]]) -> str:
    grouped = _group_by_role(summary)
    overdue_total = sum(item["overdue"] for item in summary)
    critical_total = sum(item["critical"] for item in summary)

    lines = [
        "สรุปงานเจ้าหน้าที่ BCO",
        f"วันที่ {_thai_buddhist_date()}",
        "",
    ]

    for role in ("วิศวกร", "นายตรวจ"):
        rows = grouped.get(role, [])
        if not rows:
            continue
        lines.append(f"{'วิศวกร' if role == 'วิศวกร' else 'นายตรวจ'}")
        for item in rows:
            lines.append(
                "• "
                f"{item['name']} - "
                f"งาน: {item['total']} "
                f"เกิน: {item['overdue']} "
                f"วิกฤต: {item['critical']} "
                f"ใกล้: {item['near']}"
            )
        lines.append("")

    lines.append(
        f"รวม เกิน: {overdue_total} | วิกฤต (>30วัน): {critical_total}"
    )
    return "\n".join(lines).strip()


def format_top_message(rows: list[dict[str, Any]]) -> str:
    lines = [
        "อันดับงานเกินกำหนดสูงสุด",
        f"วันที่ {_thai_buddhist_date()}",
        "",
    ]
    for index, item in enumerate(rows, start=1):
        lines.append(
            f"{index}. {item['name']} | เกิน: {item['overdue']} | "
            f"วิกฤต: {item['critical']} | งานทั้งหมด: {item['total']}"
        )
    return "\n".join(lines)


def _xml_escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _build_top_chart_svg(rows: list[dict[str, Any]]) -> str:
    width = 1200
    row_height = 120
    header_height = 170
    height = header_height + max(1, len(rows)) * row_height + 80
    max_overdue = max((item["overdue"] for item in rows), default=1) or 1
    bar_left = 390
    bar_max_width = 640
    svg: list[str] = [
        f"<svg xmlns='http://www.w3.org/2000/svg' width='{width}' height='{height}'>",
        "  <defs>",
        "    <linearGradient id='bg' x1='0' y1='0' x2='1' y2='1'>",
        "      <stop offset='0%' stop-color='#fff8ef'/>",
        "      <stop offset='100%' stop-color='#fff1de'/>",
        "    </linearGradient>",
        "  </defs>",
        "  <rect width='100%' height='100%' fill='url(#bg)'/>",
        f"  <text x='54' y='68' font-family='{THAI_FONT_FAMILY}' font-size='42' font-weight='700' fill='#302116'>งานเกินกำหนดสูงสุด 5 อันดับ</text>",
        f"  <text x='54' y='112' font-family='{THAI_FONT_FAMILY}' font-size='24' fill='#6b5646'>วันที่ {_thai_buddhist_date()}</text>",
        "  <text x='54' y='150' font-family='Helvetica, Arial, sans-serif' font-size='20' fill='#8a6d58'>bar length = overdue tasks</text>",
    ]

    for index, item in enumerate(rows, start=1):
        y = header_height + (index - 1) * row_height
        bar_width = int((item["overdue"] / max_overdue) * bar_max_width) if max_overdue else 0
        label = _xml_escape(item["name"])
        svg.extend(
            [
                f"  <rect x='42' y='{y - 40}' width='1116' height='88' rx='24' fill='white' opacity='0.78'/>",
                f"  <text x='68' y='{y - 2}' font-family='{THAI_FONT_FAMILY}' font-size='30' font-weight='700' fill='#2a1f17'>{index}. {label}</text>",
                f"  <text x='68' y='{y + 30}' font-family='{THAI_FONT_FAMILY}' font-size='20' fill='#7b6555'>ทั้งหมด {item['total']} | วิกฤต {item['critical']} | ใกล้ครบ {item['near']}</text>",
                f"  <rect x='{bar_left}' y='{y - 18}' width='{bar_max_width}' height='34' rx='17' fill='#ead7c3'/>",
                f"  <rect x='{bar_left}' y='{y - 18}' width='{max(bar_width, 10)}' height='34' rx='17' fill='#d9485f'/>",
                f"  <text x='{bar_left + bar_max_width + 26}' y='{y + 8}' font-family='Helvetica, Arial, sans-serif' font-size='30' font-weight='700' fill='#7d2232'>{item['overdue']}</text>",
            ]
        )

    svg.append("</svg>")
    return "\n".join(svg)


def _render_svg_to_png(svg_text: str) -> bytes:
    qlmanage_path = shutil.which("qlmanage")
    if not qlmanage_path:
        raise RuntimeError("qlmanage is not available on this host")

    with tempfile.TemporaryDirectory(prefix="bco_chart_") as tmpdir:
        svg_path = Path(tmpdir) / "top_chart.svg"
        svg_path.write_text(svg_text, encoding="utf-8")
        subprocess.run(
            [qlmanage_path, "-t", "-s", "1400", "-o", tmpdir, str(svg_path)],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        png_path = Path(tmpdir) / f"{svg_path.name}.png"
        return png_path.read_bytes()


def _render_top_chart_png(rows: list[dict[str, Any]]) -> bytes:
    return _render_svg_to_png(_build_top_chart_svg(rows))


def _find_special_file(files: list[dict[str, Any]], doc_name_hints: tuple[str, ...], key_hints: tuple[str, ...]) -> dict[str, Any] | None:
    for key in key_hints:
        for row in files:
            if row.get("has_file") and str(row.get("key") or "").lower() == key.lower():
                return row

    lowered_hints = tuple(hint.casefold() for hint in doc_name_hints)
    for row in files:
        if not row.get("has_file"):
            continue
        doc_name = str(row.get("doc_name") or "").casefold()
        if any(hint in doc_name for hint in lowered_hints):
            return row
    return None


def format_officer_message(item: dict[str, Any]) -> str:
    return "\n".join(
        [
            item["name"],
            f"รหัส: {item['id']}",
            f"บทบาท: {item['role']}",
            f"username: {item.get('username') or '-'}",
            f"งานทั้งหมด: {item['total']}",
            f"เกินกำหนด: {item['overdue']}",
            f"วิกฤต (>30วัน): {item['critical']}",
            f"ใกล้ครบกำหนด (0-7วัน): {item['near']}",
        ]
    )


def format_tasks_message(officer: dict[str, Any], forms: list[dict[str, Any]]) -> str:
    lines = [
        f"งานของ {officer['name']}",
        f"บทบาท: {officer['role']}",
        f"จำนวนงาน: {len(forms)}",
        "",
    ]

    if not forms:
        lines.append("ไม่พบงานในสถานะ active")
        return "\n".join(lines)

    for form in forms:
        lines.append(
            f"• {form['id']} {form.get('form_number') or '-'} | "
            f"คงเหลือ: {form.get('day_remaining')} | "
            f"{form.get('status') or '-'}"
        )
    return "\n".join(lines)


def format_tasks_picker_message(officer: dict[str, Any], forms: list[dict[str, Any]], page: int) -> str:
    total_pages = max(1, ceil(len(forms) / TASKS_PAGE_SIZE))
    page = max(0, min(page, total_pages - 1))
    start = page * TASKS_PAGE_SIZE
    end = start + TASKS_PAGE_SIZE
    page_items = forms[start:end]

    lines = [
        f"รายการค้างของ {officer['name']}",
        f"บทบาท: {officer['role']}",
        f"จำนวนงานทั้งหมด: {len(forms)}",
        f"หน้า {page + 1}/{total_pages}",
        "",
        "กดเลือกเรื่องเพื่อดูรายการไฟล์แนบ",
    ]

    if page_items:
        lines.append("")
        for form in page_items:
            lines.append(
                f"• {form.get('form_number') or form['id']} | คงเหลือ: {form.get('day_remaining')} | {form.get('status') or '-'}"
            )
    else:
        lines.append("")
        lines.append("ไม่พบงานในหน้านี้")
    return "\n".join(lines)


def build_tasks_keyboard(officer_id: int, forms: list[dict[str, Any]], page: int) -> InlineKeyboardMarkup:
    total_pages = max(1, ceil(len(forms) / TASKS_PAGE_SIZE))
    page = max(0, min(page, total_pages - 1))
    start = page * TASKS_PAGE_SIZE
    end = start + TASKS_PAGE_SIZE
    page_items = forms[start:end]

    rows: list[list[InlineKeyboardButton]] = []
    for form in page_items:
        text = f"{form.get('form_number') or form['id']} ({form.get('day_remaining')})"
        rows.append(
            [
                InlineKeyboardButton(
                    text=text[:64],
                    callback_data=f"form:{officer_id}:{form['id']}:{page}",
                )
            ]
        )

    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(InlineKeyboardButton("ก่อนหน้า", callback_data=f"tasks:{officer_id}:{page - 1}"))
    if page + 1 < total_pages:
        nav.append(InlineKeyboardButton("ถัดไป", callback_data=f"tasks:{officer_id}:{page + 1}"))
    if nav:
        rows.append(nav)
    return InlineKeyboardMarkup(rows)


def format_form_detail_message(detail: dict[str, Any]) -> str:
    lines = [
        f"{detail.get('form_number') or '-'}",
        f"id: {detail['id']}",
        f"สถานะ: {detail.get('status') or '-'}",
        f"คงเหลือ: {detail.get('day_remaining')}",
        f"ผู้รับผิดชอบ: {detail.get('assign_to_name') or '-'}",
        f"หน่วยงาน: {detail.get('assign_to_department') or '-'}",
        f"owner: {detail.get('assign_to_owner') or '-'}",
    ]

    if detail.get("building_name"):
        lines.append(f"อาคาร: {detail['building_name']}")
    if detail.get("project_name"):
        lines.append(f"โครงการ: {detail['project_name']}")
    if detail.get("address"):
        lines.append(f"ที่ตั้ง: {detail['address']}")
    if detail.get("latitude") and detail.get("longitude"):
        lines.append(f"พิกัด: {detail['latitude']}, {detail['longitude']}")
    if detail.get("google_maps_url"):
        lines.append(f"Google Maps: {detail['google_maps_url']}")
    if detail.get("openstreetmap_url"):
        lines.append(f"OpenStreetMap: {detail['openstreetmap_url']}")
    if detail.get("applicant_name"):
        lines.append(f"ผู้ยื่น: {detail['applicant_name']}")
    if detail.get("applicant_mobile"):
        lines.append(f"มือถือ: {detail['applicant_mobile']}")
    if detail.get("applicant_email"):
        lines.append(f"อีเมล: {detail['applicant_email']}")
    if detail.get("authorized_number"):
        lines.append(f"เลขอนุมัติ: {detail['authorized_number']}")
    if detail.get("reason_send_back"):
        lines.append(f"เหตุผลส่งกลับ: {detail['reason_send_back']}")
    if detail.get("user_assign"):
        lines.append("ผู้เกี่ยวข้อง:")
        for entry in detail["user_assign"]:
            lines.append(f"• {entry}")

    return "\n".join(lines)


def format_map_message(detail: dict[str, Any], row: dict[str, Any] | None) -> str:
    lines = [
        f"แผนที่ของ {detail.get('form_number') or detail['id']}",
        f"id: {detail['id']}",
    ]
    if detail.get("building_name"):
        lines.append(f"อาคาร: {detail['building_name']}")
    if detail.get("address"):
        lines.append(f"ที่ตั้ง: {detail['address']}")
    if detail.get("latitude") and detail.get("longitude"):
        lines.append(f"พิกัด: {detail['latitude']}, {detail['longitude']}")
    if detail.get("google_maps_url"):
        lines.append(f"Google Maps: {detail['google_maps_url']}")
    if detail.get("openstreetmap_url"):
        lines.append(f"OpenStreetMap: {detail['openstreetmap_url']}")
    if row:
        lines.append(f"ไฟล์แผนที่: {row.get('key')} | {row.get('file_name') or '-'}")
    else:
        lines.append("ไม่พบไฟล์แผนที่แนบในฟอร์มนี้")
    return "\n".join(lines)


def format_building_photo_message(detail: dict[str, Any], row: dict[str, Any] | None) -> str:
    lines = [
        f"รูปหน้าอาคารของ {detail.get('form_number') or detail['id']}",
        f"id: {detail['id']}",
    ]
    if detail.get("building_name"):
        lines.append(f"อาคาร: {detail['building_name']}")
    if detail.get("address"):
        lines.append(f"ที่ตั้ง: {detail['address']}")
    if row:
        lines.append(f"ไฟล์รูปอาคาร: {row.get('key')} | {row.get('file_name') or '-'}")
    else:
        lines.append("ไม่พบไฟล์รูปหน้าอาคารในฟอร์มนี้")
    return "\n".join(lines)


def format_r1_message(officer: dict[str, Any], rows: list[dict[str, Any]]) -> str:
    attached = sum(1 for row in rows if row["has_r1_file"])
    missing = len(rows) - attached
    lines = [
        f"ไฟล์ ร.1 ของ {officer['name']}",
        f"จำนวน ขร.1: {len(rows)}",
        f"แนบแล้ว: {attached}",
        f"ยังไม่แนบ: {missing}",
        "",
    ]

    if not rows:
        lines.append("ไม่พบงาน ขร.1")
        return "\n".join(lines)

    for row in rows:
        if not row["has_r1_file"]:
            state = "ไม่มีไฟล์"
        elif row.get("file_name_looks_like_r1"):
            state = "มีไฟล์"
        else:
            state = "มีไฟล์/ต้องเปิดดู"
        file_name = row.get("file_name") or "-"
        lines.append(
            f"• {row['id']} {row.get('form_number') or '-'} | "
            f"{state} | "
            f"คงเหลือ: {row.get('day_remaining')} | "
            f"{file_name}"
        )
    return "\n".join(lines)


def format_help_message() -> str:
    return "\n".join(
        [
            "คำสั่งที่ใช้ได้",
            "/help - แสดงรายการคำสั่ง",
            "/start - แสดงรายการคำสั่ง",
            "/status - สรุปงานทั้งหมด",
            "/top - กราฟ 5 คนที่งานเกินกำหนดมากสุด",
            "/officer <ชื่อ|id|username> - ดูสรุปรายคน",
            "/tasks <ชื่อ|id|username> - ดูรายการค้างและกดเลือกเรื่อง/ไฟล์ได้เลย",
            "/form <form_id> - ดูรายละเอียดงานเดี่ยว",
            "/map <form_id> - ดูพิกัดและภาพแผนที่ของเรื่อง",
            "/building <form_id> - ดูภาพหน้าอาคารของเรื่อง",
            "/files <form_id> - ดูรายการไฟล์ทั้งหมดในฟอร์ม",
            "/file <form_id> <key> - ส่งไฟล์หนึ่งตัวเข้าแชต เช่น a4 หรือ a5.1",
            "/r1 <ชื่อ|id|username> - เช็คไฟล์ ร.1 ของงาน ขร.1",
            "/otp <รหัส> - ส่ง OTP เพื่อให้บอท login BCO",
            "/chatid - ดู chat id ห้องปัจจุบัน",
            "/refresh - ล้าง token cache แล้วลองใหม่",
            "",
            "ตัวอย่าง",
            "/officer ปฐมรัฐ",
            "/tasks ปฐมรัฐ",
            "/form 349968",
            "/map 347872",
            "/building 347872",
            "/files 347872",
            "/file 347872 a4",
            "/r1 ปฐมรัฐ",
            "/otp 123456",
        ]
    )


def format_form_files_message(form_id: int, files: list[dict[str, Any]]) -> str:
    lines = [
        f"ไฟล์ในฟอร์ม {form_id}",
        f"จำนวนไฟล์ที่มีจริง: {sum(1 for row in files if row['has_file'])}",
        "",
    ]

    real_files = [row for row in files if row["has_file"]]
    if not real_files:
        lines.append("ไม่พบไฟล์ที่แนบจริง")
        return "\n".join(lines)

    for row in real_files:
        lines.append(f"• {row['key']} | {row.get('doc_name') or '-'} | {row.get('file_name') or '-'}")
    lines.append("")
    lines.append("ใช้ /file <form_id> <key> เพื่อให้บอทส่งไฟล์ต้นฉบับ")
    return "\n".join(lines)


def format_one_file_message(form_id: int, row: dict[str, Any]) -> str:
    return "\n".join(
        [
            f"ไฟล์ในฟอร์ม {form_id}",
            f"key: {row['key']}",
            f"เอกสาร: {row.get('doc_name') or '-'}",
            f"ไฟล์: {row.get('file_name') or '-'}",
            f"ประเภท: {row.get('file_type') or '-'}",
            f"อัปโหลดเมื่อ: {row.get('file_created_at') or '-'}",
            f"ลิงก์: {row.get('file_url') or '-'}",
        ]
    )


def build_file_caption(form_id: int, row: dict[str, Any]) -> str:
    return "\n".join(
        [
            f"ฟอร์ม {form_id}",
            f"key: {row['key']}",
            f"เอกสาร: {row.get('doc_name') or '-'}",
            f"ไฟล์: {row.get('file_name') or '-'}",
        ]
    )


def format_form_files_picker_message(
    form_id: int,
    detail: dict[str, Any],
    files: list[dict[str, Any]],
) -> str:
    real_files = [row for row in files if row["has_file"]]
    map_row = _find_special_file(files, MAP_DOC_HINTS, ("a5.6",))
    building_row = _find_special_file(files, BUILDING_DOC_HINTS, ("a5.5",))
    lines = [
        f"{detail.get('form_number') or form_id}",
        f"id: {form_id}",
        f"สถานะ: {detail.get('status') or '-'}",
        f"คงเหลือ: {detail.get('day_remaining')}",
        f"ไฟล์ที่มีจริง: {len(real_files)}",
        f"มีแผนที่แนบ: {'มี' if map_row else 'ไม่มี'}",
        f"มีรูปหน้าอาคาร: {'มี' if building_row else 'ไม่มี'}",
    ]
    if detail.get("latitude") and detail.get("longitude"):
        lines.append(f"พิกัด: {detail['latitude']}, {detail['longitude']}")
    lines.extend(
        [
            "",
            "กดปุ่มดูแผนที่/รูปอาคาร หรือเลือกไฟล์ต้นฉบับให้บอทส่งเข้าแชต",
        ]
    )
    return "\n".join(lines)


def build_form_files_keyboard(
    form_id: int,
    files: list[dict[str, Any]],
    *,
    officer_id: int | None = None,
    page: int = 0,
) -> InlineKeyboardMarkup:
    real_files = [row for row in files if row["has_file"]]
    rows: list[list[InlineKeyboardButton]] = []
    action_row: list[InlineKeyboardButton] = []
    if _find_special_file(files, MAP_DOC_HINTS, ("a5.6",)):
        action_row.append(InlineKeyboardButton("ดูแผนที่", callback_data=f"preview:{form_id}:map"))
    if _find_special_file(files, BUILDING_DOC_HINTS, ("a5.5",)):
        action_row.append(InlineKeyboardButton("ดูรูปอาคาร", callback_data=f"preview:{form_id}:building"))
    if action_row:
        rows.append(action_row)

    for row in real_files:
        text = f"{row['key']} {row.get('doc_name') or '-'}"
        rows.append(
            [
                InlineKeyboardButton(
                    text=text[:64],
                    callback_data=f"file:{form_id}:{row['key']}",
                )
            ]
        )

    nav: list[InlineKeyboardButton] = []
    if officer_id is not None:
        nav.append(InlineKeyboardButton("กลับไปรายการเรื่อง", callback_data=f"tasks:{officer_id}:{page}"))
    if nav:
        rows.append(nav)
    return InlineKeyboardMarkup(rows)


def _build_auth_warning(error: Exception) -> str:
    return "\n".join(
        [
            "BCO auth ใช้งานไม่ได้",
            str(error),
            "",
            "ให้ทำอย่างใดอย่างหนึ่ง:",
            "• login BCO ใน Chrome ใหม่",
            "• หรือใส่ BCO_USERNAME / BCO_PASSWORD ใน .env",
            "• ถ้าใช้ officer flow ให้เพิ่ม BCO_TOTP_SECRET หรือ BCO_OTP_CODE",
            "• หรือส่ง /otp <รหัส> มาที่ private chat ของบอท",
        ]
    )


def _has_direct_login_credentials() -> bool:
    return bool(os.getenv("BCO_USERNAME", "").strip() and os.getenv("BCO_PASSWORD", "").strip())


def _persist_chat_id(chat_id: int) -> None:
    chat_id_str = str(chat_id)
    os.environ["TELEGRAM_CHAT_ID"] = chat_id_str
    if ENV_PATH.exists():
        set_key(str(ENV_PATH), "TELEGRAM_CHAT_ID", chat_id_str)


def _maybe_store_chat_id(update: Update) -> None:
    chat = update.effective_chat
    if not chat:
        return
    current = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    chat_id = str(chat.id)
    if current == chat_id:
        return
    if not current and chat.type == "private":
        _persist_chat_id(chat.id)
        LOGGER.info("Stored TELEGRAM_CHAT_ID=%s from private chat", chat_id)


def _load_summary(profile: str, *, force_refresh: bool = False) -> list[dict[str, Any]]:
    token = get_valid_token(profile, force_refresh=force_refresh)
    api = BCOApi(token)
    return api.get_work_summary()


async def _safe_load_summary(
    profile: str,
    context: ContextTypes.DEFAULT_TYPE,
) -> tuple[list[dict[str, Any]] | None, str | None]:
    try:
        return _load_summary(profile), None
    except BCOAuthError:
        invalidate_cache()
        try:
            return _load_summary(profile, force_refresh=True), None
        except Exception as exc:
            LOGGER.exception("Auth recovery failed")
            return None, _build_auth_warning(exc)
    except Exception as exc:
        LOGGER.exception("Could not load BCO summary")
        return None, _build_auth_warning(exc)


def _load_form_bundle(profile: str, form_id: int) -> tuple[BCOApi, dict[str, Any], list[dict[str, Any]]]:
    token = get_valid_token(profile)
    api = BCOApi(token)
    detail = flatten_form_detail(form_id, api.get_form_detail(form_id))
    files = list_form_files(form_id, api.get_form_attachments(form_id))
    return api, detail, files


def _render_pdf_preview(pdf_content: bytes, prefix: str) -> bytes:
    with tempfile.TemporaryDirectory(prefix="bco_preview_") as tmpdir:
        pdf_path = Path(tmpdir) / f"{prefix}.pdf"
        output_prefix = Path(tmpdir) / f"{prefix}_page1"
        pdf_path.write_bytes(pdf_content)
        subprocess.run(
            [
                "pdftoppm",
                "-png",
                "-singlefile",
                "-f",
                "1",
                "-scale-to",
                "1400",
                str(pdf_path),
                str(output_prefix),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        png_path = Path(f"{output_prefix}.png")
        return png_path.read_bytes()


async def _send_photo_or_document_preview(
    message: Any,
    api: BCOApi,
    form_id: int,
    row: dict[str, Any],
    *,
    caption: str,
) -> None:
    content, content_type = api.download_file(row["file_url"])
    lowered_type = str(content_type or "").lower()
    buffer = io.BytesIO(content)
    buffer.name = row.get("file_name") or f"{form_id}_{row['key']}"

    if lowered_type.startswith("image/"):
        await message.reply_photo(photo=buffer, caption=caption, read_timeout=120, write_timeout=120)
        return

    if "pdf" in lowered_type or buffer.name.casefold().endswith(".pdf"):
        try:
            preview_bytes = _render_pdf_preview(content, f"{form_id}_{row['key'].replace('.', '_')}")
        except Exception:
            LOGGER.exception("Could not render PDF preview for form=%s key=%s", form_id, row["key"])
        else:
            preview = io.BytesIO(preview_bytes)
            preview.name = f"{form_id}_{row['key'].replace('.', '_')}.png"
            await message.reply_photo(photo=preview, caption=caption, read_timeout=120, write_timeout=120)
            return

    await message.reply_document(
        document=buffer,
        filename=buffer.name,
        caption=caption,
        read_timeout=120,
        write_timeout=120,
    )


async def _send_map_preview(message: Any, api: BCOApi, detail: dict[str, Any], files: list[dict[str, Any]]) -> None:
    row = _find_special_file(files, MAP_DOC_HINTS, ("a5.6",))
    await message.reply_text(format_map_message(detail, row))
    if row:
        await _send_photo_or_document_preview(
            message,
            api,
            detail["id"],
            row,
            caption=f"แผนที่ {detail.get('form_number') or detail['id']}\nkey: {row['key']}",
        )


async def _send_building_preview(message: Any, api: BCOApi, detail: dict[str, Any], files: list[dict[str, Any]]) -> None:
    row = _find_special_file(files, BUILDING_DOC_HINTS, ("a5.5",))
    await message.reply_text(format_building_photo_message(detail, row))
    if row:
        await _send_photo_or_document_preview(
            message,
            api,
            detail["id"],
            row,
            caption=f"รูปหน้าอาคาร {detail.get('form_number') or detail['id']}\nkey: {row['key']}",
        )


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _maybe_store_chat_id(update)
    profile = os.getenv("CHROME_PROFILE", "Profile 3")
    summary, error_message = await _safe_load_summary(profile, context)
    if error_message:
        await update.effective_message.reply_text(error_message)
        return
    await update.effective_message.reply_text(
        format_status_message(summary or []),
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _maybe_store_chat_id(update)
    await update.effective_message.reply_text(format_help_message())


async def top_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _maybe_store_chat_id(update)
    profile = os.getenv("CHROME_PROFILE", "Profile 3")
    summary, error_message = await _safe_load_summary(profile, context)
    if error_message:
        await update.effective_message.reply_text(error_message)
        return
    top_rows = sorted(summary or [], key=lambda item: (-item["overdue"], -item["critical"]))[:5]
    if not top_rows:
        await update.effective_message.reply_text("ไม่พบข้อมูลเจ้าหน้าที่")
        return

    try:
        chart_bytes = _render_top_chart_png(top_rows)
    except Exception:
        LOGGER.exception("Could not render /top chart")
        await update.effective_message.reply_text(format_top_message(top_rows))
        return

    chart = io.BytesIO(chart_bytes)
    chart.name = "bco_top_overdue.png"
    await update.effective_message.reply_photo(
        photo=chart,
        caption=format_top_message(top_rows),
        read_timeout=120,
        write_timeout=120,
    )


async def officer_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _maybe_store_chat_id(update)
    query = " ".join(context.args).strip()
    if not query:
        await update.effective_message.reply_text("ใช้คำสั่ง /officer <id|username|ชื่อ>")
        return

    profile = os.getenv("CHROME_PROFILE", "Profile 3")
    summary, error_message = await _safe_load_summary(profile, context)
    if error_message:
        await update.effective_message.reply_text(error_message)
        return

    officer = find_officer(summary or [], query)
    if not officer:
        await update.effective_message.reply_text(f"ไม่พบเจ้าหน้าที่: {query}")
        return

    await update.effective_message.reply_text(format_officer_message(officer))


async def refresh_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _maybe_store_chat_id(update)
    invalidate_cache()
    profile = os.getenv("CHROME_PROFILE", "Profile 3")
    summary, error_message = await _safe_load_summary(profile, context)
    if error_message:
        await update.effective_message.reply_text(error_message)
        return
    await update.effective_message.reply_text("รีเฟรช token แล้ว\n\n" + format_status_message(summary or []))


async def tasks_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _maybe_store_chat_id(update)
    query = " ".join(context.args).strip()
    if not query:
        await update.effective_message.reply_text("ใช้คำสั่ง /tasks <id|username|ชื่อ>")
        return

    profile = os.getenv("CHROME_PROFILE", "Profile 3")
    summary, error_message = await _safe_load_summary(profile, context)
    if error_message:
        await update.effective_message.reply_text(error_message)
        return

    # Reuse the existing summary lookup first so typos fail fast.
    officer = find_officer(summary or [], query)
    if not officer:
        await update.effective_message.reply_text(f"ไม่พบเจ้าหน้าที่: {query}")
        return

    try:
        token = get_valid_token(profile)
        api = BCOApi(token)
        officer_row, forms = get_tasks_for_officer(api, str(officer["id"]))
    except Exception as exc:
        await update.effective_message.reply_text(_build_auth_warning(exc))
        return
    await update.effective_message.reply_text(
        format_tasks_picker_message(officer_row, forms, page=0),
        reply_markup=build_tasks_keyboard(int(officer["id"]), forms, page=0),
    )


async def form_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _maybe_store_chat_id(update)
    query = " ".join(context.args).strip()
    if not query or not query.isdigit():
        await update.effective_message.reply_text("ใช้คำสั่ง /form <form_id>")
        return

    profile = os.getenv("CHROME_PROFILE", "Profile 3")
    try:
        _, detail, files = _load_form_bundle(profile, int(query))
    except Exception as exc:
        await update.effective_message.reply_text(_build_auth_warning(exc))
        return
    await update.effective_message.reply_text(
        format_form_detail_message(detail) + "\n\nใช้ /map เพื่อดูแผนที่ และ /building เพื่อดูรูปหน้าอาคาร",
        reply_markup=build_form_files_keyboard(
            detail["id"],
            files,
        ),
    )


async def map_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _maybe_store_chat_id(update)
    query = " ".join(context.args).strip()
    if not query or not query.isdigit():
        await update.effective_message.reply_text("ใช้คำสั่ง /map <form_id>")
        return

    profile = os.getenv("CHROME_PROFILE", "Profile 3")
    try:
        api, detail, files = _load_form_bundle(profile, int(query))
    except Exception as exc:
        await update.effective_message.reply_text(_build_auth_warning(exc))
        return
    await _send_map_preview(update.effective_message, api, detail, files)


async def building_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _maybe_store_chat_id(update)
    query = " ".join(context.args).strip()
    if not query or not query.isdigit():
        await update.effective_message.reply_text("ใช้คำสั่ง /building <form_id>")
        return

    profile = os.getenv("CHROME_PROFILE", "Profile 3")
    try:
        api, detail, files = _load_form_bundle(profile, int(query))
    except Exception as exc:
        await update.effective_message.reply_text(_build_auth_warning(exc))
        return
    await _send_building_preview(update.effective_message, api, detail, files)


async def files_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _maybe_store_chat_id(update)
    query = " ".join(context.args).strip()
    if not query or not query.isdigit():
        await update.effective_message.reply_text("ใช้คำสั่ง /files <form_id>")
        return

    form_id = int(query)
    profile = os.getenv("CHROME_PROFILE", "Profile 3")
    try:
        token = get_valid_token(profile)
        api = BCOApi(token)
        attachment_data = api.get_form_attachments(form_id)
        files = list_form_files(form_id, attachment_data)
    except Exception as exc:
        await update.effective_message.reply_text(_build_auth_warning(exc))
        return

    await update.effective_message.reply_text(format_form_files_message(form_id, files))


async def file_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _maybe_store_chat_id(update)
    if len(context.args) < 2 or not context.args[0].isdigit():
        await update.effective_message.reply_text("ใช้คำสั่ง /file <form_id> <key>")
        return

    form_id = int(context.args[0])
    key = context.args[1].strip()
    profile = os.getenv("CHROME_PROFILE", "Profile 3")
    try:
        token = get_valid_token(profile)
        api = BCOApi(token)
        attachment_data = api.get_form_attachments(form_id)
        row = find_form_file_by_key(attachment_data, key)
    except Exception as exc:
        await update.effective_message.reply_text(_build_auth_warning(exc))
        return

    if not row:
        await update.effective_message.reply_text(f"ไม่พบ key {key} ในฟอร์ม {form_id}")
        return
    if not row.get("has_file"):
        await update.effective_message.reply_text(f"key {key} มีช่องเอกสาร แต่ยังไม่มีไฟล์แนบ")
        return
    await _send_row_file(update.effective_message, api, form_id, row)


async def _send_row_file(message: Any, api: BCOApi, form_id: int, row: dict[str, Any]) -> None:
    try:
        content, content_type = api.download_file(row["file_url"])
        filename = row.get("file_name") or f"{form_id}_{row['key']}"
        buffer = io.BytesIO(content)
        buffer.name = filename
    except Exception as exc:
        await message.reply_text(
            "โหลดไฟล์ไม่สำเร็จ\n\n"
            + format_one_file_message(form_id, row)
            + f"\ncontent_type: {content_type if 'content_type' in locals() else '-'}\nerror: {exc}"
        )
        return

    await message.reply_document(
        document=buffer,
        filename=filename,
        caption=build_file_caption(form_id, row),
        read_timeout=120,
        write_timeout=120,
    )


async def callback_query_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data:
        return

    data = query.data
    await query.answer()

    if data.startswith("tasks:"):
        _, officer_id, page_text = data.split(":", 2)
        profile = os.getenv("CHROME_PROFILE", "Profile 3")
        try:
            token = get_valid_token(profile)
            api = BCOApi(token)
            officer_row, forms = get_tasks_for_officer(api, officer_id)
        except Exception as exc:
            await query.edit_message_text(_build_auth_warning(exc))
            return

        page = int(page_text)
        await query.edit_message_text(
            format_tasks_picker_message(officer_row, forms, page=page),
            reply_markup=build_tasks_keyboard(int(officer_id), forms, page=page),
        )
        return

    if data.startswith("form:"):
        _, officer_id, form_id_text, page_text = data.split(":", 3)
        form_id = int(form_id_text)
        page = int(page_text)
        profile = os.getenv("CHROME_PROFILE", "Profile 3")
        try:
            token = get_valid_token(profile)
            api = BCOApi(token)
            detail = flatten_form_detail(form_id, api.get_form_detail(form_id))
            attachment_data = api.get_form_attachments(form_id)
            files = list_form_files(form_id, attachment_data)
        except Exception as exc:
            await query.edit_message_text(_build_auth_warning(exc))
            return

        await query.edit_message_text(
            format_form_files_picker_message(form_id, detail, files),
            reply_markup=build_form_files_keyboard(form_id, files, officer_id=int(officer_id), page=page),
        )
        return

    if data.startswith("file:"):
        _, form_id_text, key = data.split(":", 2)
        form_id = int(form_id_text)
        profile = os.getenv("CHROME_PROFILE", "Profile 3")
        try:
            token = get_valid_token(profile)
            api = BCOApi(token)
            attachment_data = api.get_form_attachments(form_id)
            row = find_form_file_by_key(attachment_data, key)
        except Exception as exc:
            await query.message.reply_text(_build_auth_warning(exc))
            return

        if not row:
            await query.message.reply_text(f"ไม่พบ key {key} ในฟอร์ม {form_id}")
            return
        if not row.get("has_file"):
            await query.message.reply_text(f"key {key} มีช่องเอกสาร แต่ยังไม่มีไฟล์แนบ")
            return

        await query.answer("กำลังส่งไฟล์...", show_alert=False)
        await _send_row_file(query.message, api, form_id, row)
        return

    if data.startswith("preview:"):
        _, form_id_text, preview_type = data.split(":", 2)
        form_id = int(form_id_text)
        profile = os.getenv("CHROME_PROFILE", "Profile 3")
        try:
            api, detail, files = _load_form_bundle(profile, form_id)
        except Exception as exc:
            await query.message.reply_text(_build_auth_warning(exc))
            return

        if preview_type == "map":
            await query.answer("กำลังส่งแผนที่...", show_alert=False)
            await _send_map_preview(query.message, api, detail, files)
            return
        if preview_type == "building":
            await query.answer("กำลังส่งรูปอาคาร...", show_alert=False)
            await _send_building_preview(query.message, api, detail, files)
            return


async def r1_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _maybe_store_chat_id(update)
    query = " ".join(context.args).strip()
    if not query:
        await update.effective_message.reply_text("ใช้คำสั่ง /r1 <id|username|ชื่อ>")
        return

    profile = os.getenv("CHROME_PROFILE", "Profile 3")
    summary, error_message = await _safe_load_summary(profile, context)
    if error_message:
        await update.effective_message.reply_text(error_message)
        return

    officer = find_officer(summary or [], query)
    if not officer:
        await update.effective_message.reply_text(f"ไม่พบเจ้าหน้าที่: {query}")
        return

    try:
        token = get_valid_token(profile)
        api = BCOApi(token)
        officer_row, rows = get_r1_statuses_for_officer(api, str(officer["id"]))
    except Exception as exc:
        await update.effective_message.reply_text(_build_auth_warning(exc))
        return

    await update.effective_message.reply_text(format_r1_message(officer_row, rows))


async def otp_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _maybe_store_chat_id(update)
    if update.effective_chat and update.effective_chat.type != "private":
        await update.effective_message.reply_text("คำสั่ง /otp ใช้ได้เฉพาะใน private chat กับบอท")
        return

    code = "".join(context.args).strip()
    if not (code.isdigit() and len(code) in {6, 8}):
        await update.effective_message.reply_text("ใช้คำสั่ง /otp <รหัส OTP 6 หรือ 8 หลัก>")
        return

    if not _has_direct_login_credentials():
        await update.effective_message.reply_text("ยังไม่มี BCO_USERNAME / BCO_PASSWORD ใน .env จึงใช้ OTP จาก Telegram ต่อไม่ได้")
        return

    set_runtime_otp_code(code)
    invalidate_cache()
    profile = os.getenv("CHROME_PROFILE", "Profile 3")
    summary, error_message = await _safe_load_summary(profile, context)
    if error_message:
        await update.effective_message.reply_text(error_message)
        return

    await update.effective_message.reply_text("รับ OTP แล้วและลอง login เรียบร้อย\n\n" + format_status_message(summary or []))


async def chatid_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _maybe_store_chat_id(update)
    if not update.effective_chat:
        return
    await update.effective_message.reply_text(f"chat_id: {update.effective_chat.id}")


async def send_daily_status(context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = context.job.data["chat_id"]
    profile = os.getenv("CHROME_PROFILE", "Profile 3")
    summary, error_message = await _safe_load_summary(profile, context)
    message = error_message or format_status_message(summary or [])
    await context.bot.send_message(chat_id=chat_id, text=message)


async def monitor_auth_health(context: ContextTypes.DEFAULT_TYPE) -> None:
    global _AUTH_ALERT_ACTIVE

    chat_id = context.job.data["chat_id"]
    profile = os.getenv("CHROME_PROFILE", "Profile 3")
    summary, error_message = await _safe_load_summary(profile, context)

    if error_message:
        if not _AUTH_ALERT_ACTIVE:
            _AUTH_ALERT_ACTIVE = True
            await context.bot.send_message(
                chat_id=chat_id,
                text="แจ้งเตือน BCO auth\n\n" + error_message,
            )
        return

    if _AUTH_ALERT_ACTIVE:
        _AUTH_ALERT_ACTIVE = False
        await context.bot.send_message(
            chat_id=chat_id,
            text="BCO auth กลับมาใช้งานได้แล้ว\n\n" + format_status_message(summary or []),
        )


def build_application() -> Application:
    load_dotenv()

    bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not bot_token:
        raise RuntimeError("Missing TELEGRAM_BOT_TOKEN")

    application = Application.builder().token(bot_token).build()
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("start", help_command))
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(CommandHandler("top", top_command))
    application.add_handler(CommandHandler("officer", officer_command))
    application.add_handler(CommandHandler("tasks", tasks_command))
    application.add_handler(CommandHandler("form", form_command))
    application.add_handler(CommandHandler("map", map_command))
    application.add_handler(CommandHandler("building", building_command))
    application.add_handler(CommandHandler("files", files_command))
    application.add_handler(CommandHandler("file", file_command))
    application.add_handler(CommandHandler("r1", r1_command))
    application.add_handler(CommandHandler("otp", otp_command))
    application.add_handler(CommandHandler("chatid", chatid_command))
    application.add_handler(CommandHandler("refresh", refresh_command))
    application.add_handler(CallbackQueryHandler(callback_query_command))

    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if chat_id and application.job_queue:
        application.job_queue.run_daily(
            send_daily_status,
            time=dt.time(hour=8, minute=0, tzinfo=TIMEZONE),
            data={"chat_id": chat_id},
            name="daily_status",
        )
        application.job_queue.run_repeating(
            monitor_auth_health,
            interval=dt.timedelta(minutes=AUTH_MONITOR_INTERVAL_MINUTES),
            first=dt.timedelta(minutes=1),
            data={"chat_id": chat_id},
            name="auth_monitor",
        )
        LOGGER.info("Scheduled daily status to chat_id=%s at 08:00 Asia/Bangkok", chat_id)
        LOGGER.info("Scheduled auth monitor every %s minutes", AUTH_MONITOR_INTERVAL_MINUTES)
    elif chat_id:
        LOGGER.warning("JobQueue unavailable; daily schedule disabled")
    else:
        LOGGER.warning("TELEGRAM_CHAT_ID not configured; daily schedule disabled")

    return application


def main() -> None:
    application = build_application()
    application.run_polling()


if __name__ == "__main__":
    main()
