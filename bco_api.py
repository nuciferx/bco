from __future__ import annotations

import argparse
import json
from collections import defaultdict
from typing import Any

import requests

from token_manager import get_valid_token, set_runtime_otp_code


ALLOWED_ROLES = {"วิศวกร", "นายตรวจ"}
R1_ATTACHMENT_DOC_ID = 69
R1_ATTACHMENT_SEQ = 4
R1_ATTACHMENT_NAME = "สำเนาภาพถ่ายใบรับรองการตรวจสอบอาคารฉบับล่าสุด (ร.1)"
R1_FILE_NAME_HINTS = ("ร1", "ร.1", "ใบร.1", "ใบรับรอง", "ตรวจสอบอาคาร")


class BCOAuthError(RuntimeError):
    pass


class BCOApi:
    BASE = "https://bco-api.bangkok.go.th/api/v1"

    def __init__(self, token: str):
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
            }
        )

    def _request(self, path: str, *, params: dict[str, Any] | None = None) -> dict[str, Any] | list[Any]:
        url = f"{self.BASE}{path}"
        resp = self.session.get(url, params=params, timeout=30)
        if resp.status_code == 401:
            raise BCOAuthError("BCO token expired or is unauthorized")
        resp.raise_for_status()
        return resp.json()

    def _request_items(self, path: str, *, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        payload = self._request(path, params=params)
        return self._extract_items(payload)

    def _extract_items(self, payload: dict[str, Any] | list[Any]) -> list[dict[str, Any]]:
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]

        for key in ("data", "items", "results"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
            if isinstance(value, dict):
                for nested_key in ("data", "items", "results"):
                    nested_value = value.get(nested_key)
                    if isinstance(nested_value, list):
                        return [item for item in nested_value if isinstance(item, dict)]
        return []

    def _extract_total_pages(self, payload: dict[str, Any]) -> int | None:
        for container in (payload, payload.get("meta"), payload.get("pagination"), payload.get("data")):
            if not isinstance(container, dict):
                continue
            for key in ("last_page", "total_pages", "lastPage", "pageCount"):
                value = container.get(key)
                try:
                    pages = int(value)
                except (TypeError, ValueError):
                    continue
                if pages >= 1:
                    return pages
        return None

    def _paginate(self, path: str, *, params: dict[str, Any], page_param: str = "page") -> list[dict[str, Any]]:
        page = 1
        all_items: list[dict[str, Any]] = []
        total_pages = None
        max_pages = 100

        while True:
            if page > max_pages:
                raise RuntimeError(f"Pagination safeguard triggered for {path}")

            request_params = dict(params)
            request_params[page_param] = page
            payload = self._request(path, params=request_params)
            if not isinstance(payload, dict):
                items = self._extract_items(payload)
                if not items:
                    break
                all_items.extend(items)
                break

            items = self._extract_items(payload)
            if not items:
                break

            all_items.extend(items)
            total_pages = total_pages or self._extract_total_pages(payload)
            if total_pages and page >= total_pages:
                break

            per_page = request_params.get("per_page") or request_params.get("limit")
            try:
                per_page_int = int(per_page)
            except (TypeError, ValueError):
                per_page_int = None

            if total_pages is None and per_page_int and len(items) < per_page_int:
                break

            page += 1

        return all_items

    def get_all_users(self) -> list[dict[str, Any]]:
        return self._request_items("/users", params={"page": 1, "limit": 200})

    def get_all_forms(self) -> list[dict[str, Any]]:
        # The live endpoint currently returns all active forms in one response and
        # ignores `page`, so using pagination causes duplicate pages and hangs.
        return self._request_items(
            "/form",
            params={"form_status_id": 1, "per_page": 10000, "page": 1},
        )

    def get_form_detail(self, form_id: int) -> dict[str, Any]:
        payload = self._request(f"/form/{form_id}")
        if not isinstance(payload, dict):
            raise RuntimeError(f"Unexpected payload for form {form_id}")
        data = payload.get("data")
        if not isinstance(data, dict):
            raise RuntimeError(f"Form {form_id} detail not found")
        return data

    def get_form_attachments(self, form_id: int) -> dict[str, Any]:
        payload = self._request(f"/form/{form_id}/attachment")
        if not isinstance(payload, dict):
            raise RuntimeError(f"Unexpected attachment payload for form {form_id}")
        data = payload.get("data")
        if not isinstance(data, dict):
            raise RuntimeError(f"Form {form_id} attachment data not found")
        return data

    def download_file(self, url: str) -> tuple[bytes, str | None]:
        resp = self.session.get(url, timeout=60)
        if resp.status_code == 401:
            raise BCOAuthError("BCO token expired or is unauthorized")
        resp.raise_for_status()
        return resp.content, resp.headers.get("Content-Type")

    def get_work_summary(self) -> list[dict[str, Any]]:
        users = self.get_all_users()
        forms = self.get_all_forms()

        summary_by_user: dict[int, dict[str, Any]] = {}
        name_to_user_ids: dict[str, list[int]] = defaultdict(list)
        for user in users:
            roles = user.get("roles") or []
            role_names = [role.get("name", "") for role in roles if isinstance(role, dict)]
            allowed = [name for name in role_names if name in ALLOWED_ROLES]
            if not allowed:
                continue

            user_id = user.get("id")
            if not isinstance(user_id, int):
                continue

            summary_by_user[user_id] = {
                "id": user_id,
                "name": f"{user.get('first_name', '').strip()} {user.get('last_name', '').strip()}".strip(),
                "role": allowed[0],
                "total": 0,
                "overdue": 0,
                "critical": 0,
                "near": 0,
                "status": user.get("status"),
                "username": user.get("username"),
            }
            name_to_user_ids[summary_by_user[user_id]["name"]].append(user_id)

        counts: dict[int, dict[str, int]] = defaultdict(lambda: {"total": 0, "overdue": 0, "critical": 0, "near": 0})
        for form in forms:
            owners: list[int] = []

            raw_owner = form.get("user_owner")
            try:
                owner_id = int(raw_owner)
            except (TypeError, ValueError):
                owner_id = None

            if owner_id is not None and owner_id in summary_by_user:
                owners = [owner_id]
            elif isinstance(raw_owner, str):
                owners = name_to_user_ids.get(raw_owner.strip(), [])

            if not owners:
                continue
            day_remaining = form.get("day_remaining")
            if day_remaining is None:
                continue
            try:
                days = int(day_remaining)
            except (TypeError, ValueError):
                continue

            for owner in owners:
                counts[owner]["total"] += 1
                if days < 0:
                    counts[owner]["overdue"] += 1
                if days < -30:
                    counts[owner]["critical"] += 1
                if 0 <= days <= 7:
                    counts[owner]["near"] += 1

        summary = []
        for user_id, row in summary_by_user.items():
            row.update(counts[user_id])
            summary.append(row)

        summary.sort(
            key=lambda item: (
                -item["overdue"],
                -item["critical"],
                -item["total"],
                item["name"],
            )
        )
        return summary


def _user_role_names(user: dict[str, Any]) -> list[str]:
    roles = user.get("roles") or []
    return [role.get("name", "") for role in roles if isinstance(role, dict)]


def build_officer_rows(users: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for user in users:
        allowed = [name for name in _user_role_names(user) if name in ALLOWED_ROLES]
        if not allowed:
            continue
        rows.append(
            {
                "id": user.get("id"),
                "name": f"{user.get('first_name', '').strip()} {user.get('last_name', '').strip()}".strip(),
                "role": allowed[0],
                "status": user.get("status"),
                "username": user.get("username"),
            }
        )
    return rows


def find_officer(summary: list[dict[str, Any]], query: str) -> dict[str, Any] | None:
    query = query.strip()
    if not query:
        return None

    if query.isdigit():
        target_id = int(query)
        for item in summary:
            if item["id"] == target_id:
                return item

    lowered = query.casefold()
    exact_matches = [
        item
        for item in summary
        if lowered in {
            item["name"].casefold(),
            str(item.get("username", "")).casefold(),
        }
    ]
    if exact_matches:
        return exact_matches[0]

    partial_matches = [
        item
        for item in summary
        if lowered in item["name"].casefold() or lowered in str(item.get("username", "")).casefold()
    ]
    return partial_matches[0] if partial_matches else None


def format_form_list_item(form: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": form.get("id"),
        "form_number": form.get("form_number"),
        "status": form.get("ref_status_name"),
        "department": form.get("ref_department_name"),
        "owner_name": form.get("user_owner"),
        "day_remaining": form.get("day_remaining"),
        "district": form.get("district_name"),
        "subdistrict": form.get("subdistrict_name"),
        "applicant": form.get("applicant_person") or form.get("person_request_name"),
        "building_name": form.get("building_name"),
        "project_name": form.get("project_name"),
        "form_date": form.get("form_date"),
    }


def flatten_form_detail(form_id: int, data: dict[str, Any]) -> dict[str, Any]:
    form_detail = data.get("form_detail") or {}
    applicant = data.get("applicant_person") or {}
    owner = data.get("owner_person") or {}
    building = data.get("k1_form_building") or {}
    building_info_list = building.get("k1_form_building_info") or []
    building_info = building_info_list[0] if building_info_list and isinstance(building_info_list[0], dict) else {}
    latitude = building.get("latitude")
    longitude = building.get("longitude")
    google_maps_url = None
    openstreetmap_url = None
    if latitude and longitude:
        google_maps_url = f"https://www.google.com/maps?q={latitude},{longitude}"
        openstreetmap_url = f"https://www.openstreetmap.org/?mlat={latitude}&mlon={longitude}#map=18/{latitude}/{longitude}"

    address_parts = [
        building.get("addr_no"),
        f"หมู่ {building.get('addr_moo')}" if building.get("addr_moo") else None,
        f"ซอย{building.get('addr_soi')}" if building.get("addr_soi") else None,
        building.get("addr_road_name"),
    ]
    address = " ".join(part.strip() for part in address_parts if isinstance(part, str) and part.strip()) or None

    return {
        "id": form_id,
        "form_number": form_detail.get("form_number"),
        "status": form_detail.get("ref_status_name"),
        "status_id": form_detail.get("ref_status_id"),
        "day_remaining": form_detail.get("day_remaining"),
        "owner_name": form_detail.get("owner_name"),
        "assign_to_name": form_detail.get("assign_to_name"),
        "assign_to_department": form_detail.get("assign_to_deparment"),
        "assign_to_owner": form_detail.get("assign_to_owner"),
        "user_assign": form_detail.get("user_assign"),
        "send_back_status_id": form_detail.get("send_back_status_id"),
        "reason_send_back": form_detail.get("reason_send_back"),
        "authorized_number": form_detail.get("authorized_number"),
        "authorized_date": form_detail.get("authorized_date"),
        "form_date": form_detail.get("form_date"),
        "applicant_name": " ".join(
            part
            for part in [
                applicant.get("prefix_name", {}).get("name") if isinstance(applicant.get("prefix_name"), dict) else None,
                applicant.get("first_name"),
                applicant.get("last_name"),
            ]
            if part
        )
        or None,
        "applicant_mobile": applicant.get("mobile"),
        "applicant_email": applicant.get("email"),
        "applicant_card_id": applicant.get("card_id"),
        "owner_person_name": " ".join(
            part
            for part in [
                owner.get("prefix_name", {}).get("name") if isinstance(owner.get("prefix_name"), dict) else None,
                owner.get("first_name"),
                owner.get("last_name"),
            ]
            if part
        )
        or None,
        "building_name": building_info.get("building_name") or building.get("building_name"),
        "project_name": building_info.get("project_name"),
        "address": address,
        "latitude": latitude,
        "longitude": longitude,
        "the_geom": building.get("the_geom"),
        "google_maps_url": google_maps_url,
        "openstreetmap_url": openstreetmap_url,
        "raw": data,
    }


def flatten_form_attachments(form_id: int, data: dict[str, Any]) -> dict[str, Any]:
    applicant_doc = data.get("applicant_doc") or {}
    attachments = applicant_doc.get("attachment") or []
    download_docs = applicant_doc.get("dowload_doc") or []
    return {
        "id": form_id,
        "ref_request_form_type_id": data.get("ref_request_form_type_id"),
        "attachment_count": len([row for row in attachments if isinstance(row, dict)]),
        "download_doc_count": len([row for row in download_docs if isinstance(row, dict)]),
        "attachments": attachments,
        "download_docs": download_docs,
        "raw": data,
    }


def list_form_files(form_id: int, data: dict[str, Any]) -> list[dict[str, Any]]:
    applicant_doc = data.get("applicant_doc") or {}
    files: list[dict[str, Any]] = []

    def add_row(kind: str, key: str, row: dict[str, Any], *, parent_key: str | None = None) -> None:
        file_info = row.get("file") if isinstance(row.get("file"), dict) else None
        files.append(
            {
                "form_id": form_id,
                "key": key,
                "parent_key": parent_key,
                "kind": kind,
                "doc_id": row.get("id"),
                "seq": row.get("seq"),
                "doc_name": row.get("doc_name"),
                "form_attachment_id": row.get("form_attachment_id"),
                "has_file": bool(row.get("is_file") and file_info and file_info.get("url")),
                "file_name": file_info.get("name") if file_info else None,
                "file_type": file_info.get("type") if file_info else None,
                "file_url": file_info.get("url") if file_info else None,
                "file_created_at": file_info.get("created_at") if file_info else None,
            }
        )

    for row in applicant_doc.get("dowload_doc") or []:
        if not isinstance(row, dict):
            continue
        key = f"d{row.get('seq')}"
        add_row("download", key, row)

    for row in applicant_doc.get("attachment") or []:
        if not isinstance(row, dict):
            continue
        key = f"a{row.get('seq')}"
        add_row("attachment", key, row)
        for index, sub in enumerate(row.get("sub_doc") or [], start=1):
            if not isinstance(sub, dict):
                continue
            sub_key = f"{key}.{index}"
            add_row("sub_doc", sub_key, sub, parent_key=key)

    return files


def find_form_file_by_key(data: dict[str, Any], key: str) -> dict[str, Any] | None:
    wanted = key.strip().lower()
    for row in list_form_files(0, data):
        if row["key"].lower() == wanted:
            return row
    return None


def _extract_attachment_rows(data: dict[str, Any]) -> list[dict[str, Any]]:
    applicant_doc = data.get("applicant_doc") or {}
    rows = applicant_doc.get("attachment") or []
    return [row for row in rows if isinstance(row, dict)]


def find_r1_attachment_row(data: dict[str, Any]) -> dict[str, Any] | None:
    for row in _extract_attachment_rows(data):
        doc_name = str(row.get("doc_name") or "").strip()
        if row.get("id") == R1_ATTACHMENT_DOC_ID:
            return row
        if row.get("seq") == R1_ATTACHMENT_SEQ and "ร.1" in doc_name:
            return row
        if doc_name == R1_ATTACHMENT_NAME:
            return row
    return None


def r1_file_name_looks_like_r1(file_name: str | None) -> bool:
    if not file_name:
        return False
    lowered = file_name.casefold()
    return any(hint in lowered for hint in R1_FILE_NAME_HINTS)


def flatten_r1_attachment_status(
    form_id: int,
    form_number: str | None,
    day_remaining: Any,
    status: str | None,
    data: dict[str, Any],
) -> dict[str, Any]:
    row = find_r1_attachment_row(data)
    file_info = row.get("file") if isinstance(row, dict) and isinstance(row.get("file"), dict) else {}
    has_file = bool(
        row
        and row.get("form_attachment_id")
        and row.get("is_file")
        and isinstance(file_info, dict)
        and file_info.get("url")
    )
    looks_like_r1 = r1_file_name_looks_like_r1(file_info.get("name") if isinstance(file_info, dict) else None)
    return {
        "id": form_id,
        "form_number": form_number,
        "day_remaining": day_remaining,
        "status": status,
        "r1_slot_found": row is not None,
        "has_r1_file": has_file,
        "file_name_looks_like_r1": looks_like_r1 if has_file else None,
        "doc_id": row.get("id") if row else None,
        "doc_name": row.get("doc_name") if row else None,
        "seq": row.get("seq") if row else None,
        "form_attachment_id": row.get("form_attachment_id") if row else None,
        "file_name": file_info.get("name") if isinstance(file_info, dict) else None,
        "file_url": file_info.get("url") if isinstance(file_info, dict) else None,
        "file_created_at": file_info.get("created_at") if isinstance(file_info, dict) else None,
        "raw": row,
    }


def get_tasks_for_officer(api: BCOApi, officer_query: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    users = api.get_all_users()
    officer = find_officer(build_officer_rows(users), officer_query)
    if not officer:
        raise RuntimeError(f"Officer not found: {officer_query}")

    forms = []
    for form in api.get_all_forms():
        owner_name = str(form.get("user_owner") or "").strip()
        if owner_name != officer["name"]:
            continue
        forms.append(format_form_list_item(form))

    forms.sort(
        key=lambda item: (
            item["day_remaining"] is None,
            item["day_remaining"] if item["day_remaining"] is not None else 10**9,
            item["form_number"] or "",
        )
    )
    return officer, forms


def get_r1_statuses_for_officer(api: BCOApi, officer_query: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    officer, forms = get_tasks_for_officer(api, officer_query)
    r1_rows: list[dict[str, Any]] = []
    for form in forms:
        form_number = str(form.get("form_number") or "")
        if not form_number.startswith("ขร.1"):
            continue
        form_id = form.get("id")
        if not isinstance(form_id, int):
            continue
        attachment_data = api.get_form_attachments(form_id)
        r1_rows.append(
            flatten_r1_attachment_status(
                form_id,
                form.get("form_number"),
                form.get("day_remaining"),
                form.get("status"),
                attachment_data,
            )
        )
    r1_rows.sort(
        key=lambda item: (
            item["has_r1_file"],
            item["day_remaining"] is None,
            item["day_remaining"] if item["day_remaining"] is not None else 10**9,
            item["form_number"] or "",
        )
    )
    return officer, r1_rows


def _print_officer(row: dict[str, Any]) -> None:
    print(json.dumps(row, ensure_ascii=False, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch BCO work summary")
    parser.add_argument("--profile", default="Profile 3")
    parser.add_argument("--otp", help="Runtime OTP code for officer login")
    parser.add_argument("--officer", help="Officer id, username, or name")
    parser.add_argument("--tasks-for", help="List all forms for one officer")
    parser.add_argument("--form-id", type=int, help="Get detailed data for one form id")
    parser.add_argument("--form-attachments", type=int, help="Get attachment data for one form id")
    parser.add_argument("--r1-form-id", type=int, help="Check the slot 4 R.1 attachment for one form id")
    parser.add_argument("--r1-for", help="Check all ขร.1 forms for one officer for the latest R.1 attachment")
    parser.add_argument("--top", type=int, default=0, help="Show top N rows")
    args = parser.parse_args()

    if args.otp:
        set_runtime_otp_code(args.otp)

    token = get_valid_token(args.profile)
    api = BCOApi(token)

    if args.form_id is not None:
        detail = flatten_form_detail(args.form_id, api.get_form_detail(args.form_id))
        print(json.dumps(detail, ensure_ascii=False, indent=2))
        return 0

    if args.form_attachments is not None:
        attachments = flatten_form_attachments(args.form_attachments, api.get_form_attachments(args.form_attachments))
        print(json.dumps(attachments, ensure_ascii=False, indent=2))
        return 0

    if args.r1_form_id is not None:
        detail = flatten_form_detail(args.r1_form_id, api.get_form_detail(args.r1_form_id))
        attachments = api.get_form_attachments(args.r1_form_id)
        r1_status = flatten_r1_attachment_status(
            args.r1_form_id,
            detail.get("form_number"),
            detail.get("day_remaining"),
            detail.get("status"),
            attachments,
        )
        print(json.dumps(r1_status, ensure_ascii=False, indent=2))
        return 0

    if args.tasks_for:
        officer, forms = get_tasks_for_officer(api, args.tasks_for)
        print(
            json.dumps(
                {
                    "officer": officer,
                    "task_count": len(forms),
                    "forms": forms,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    if args.r1_for:
        officer, rows = get_r1_statuses_for_officer(api, args.r1_for)
        print(
            json.dumps(
                {
                    "officer": officer,
                    "r1_form_count": len(rows),
                    "r1_attached_count": sum(1 for row in rows if row["has_r1_file"]),
                    "r1_missing_count": sum(1 for row in rows if not row["has_r1_file"]),
                    "forms": rows,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    summary = api.get_work_summary()

    if args.officer:
        officer = find_officer(summary, args.officer)
        if not officer:
            print(f"Officer not found: {args.officer}")
            return 1
        _print_officer(officer)
        return 0

    rows = summary[: args.top] if args.top > 0 else summary
    print(json.dumps(rows, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
