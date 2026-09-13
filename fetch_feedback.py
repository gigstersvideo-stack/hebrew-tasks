"""
fetch_feedback.py — читает коллекцию Firestore "feedback" (проект
ivrit-progress) через service-account ключ и печатает отзывы читаемым
текстом. Использует REST API Firestore напрямую (не grpc-клиент
firebase-admin/firestore) — на этой машине gRPC не может договориться
о TLS с локальным перехватывающим прокси/антивирусом, а обычный HTTPS
через requests с системными сертификатами (pip-system-certs) работает.

Не трогает статус записей, если не передан --mark-reviewed — это по
умолчанию делает сам пользователь в приложении (экран "Отзывы").

Запуск:
    python fetch_feedback.py                  # только новые (status=="new")
    python fetch_feedback.py --all             # вообще все записи
    python fetch_feedback.py --mark-reviewed   # после печати помечает
                                                # показанные как status="reviewed"
"""

import argparse
import datetime
import os
import sys

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

import google.auth.transport.requests
from google.oauth2 import service_account

HERE = os.path.dirname(os.path.abspath(__file__))
KEY_PATH = os.path.join(HERE, "ivrit-progress-firebase-adminsdk-fbsvc-156ed0e778.json")
PROJECT_ID = "ivrit-progress"
SCOPES = ["https://www.googleapis.com/auth/datastore"]
BASE_URL = f"https://firestore.googleapis.com/v1/projects/{PROJECT_ID}/databases/(default)/documents"


def get_session():
    creds = service_account.Credentials.from_service_account_file(KEY_PATH, scopes=SCOPES)
    session = google.auth.transport.requests.AuthorizedSession(creds)
    return session


def parse_value(v):
    if "stringValue" in v:
        return v["stringValue"]
    if "integerValue" in v:
        return int(v["integerValue"])
    if "doubleValue" in v:
        return v["doubleValue"]
    if "booleanValue" in v:
        return v["booleanValue"]
    if "nullValue" in v:
        return None
    return v


def doc_to_dict(doc):
    fields = doc.get("fields", {})
    out = {k: parse_value(v) for k, v in fields.items()}
    out["id"] = doc["name"].rsplit("/", 1)[-1]
    return out


def fetch_all(session):
    items = []
    page_token = None
    while True:
        params = {"pageSize": 300}
        if page_token:
            params["pageToken"] = page_token
        resp = session.get(f"{BASE_URL}/feedback", params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        for doc in data.get("documents", []):
            items.append(doc_to_dict(doc))
        page_token = data.get("nextPageToken")
        if not page_token:
            break
    return items


def mark_reviewed(session, ids):
    for doc_id in ids:
        url = f"{BASE_URL}/feedback/{doc_id}"
        body = {"fields": {"status": {"stringValue": "reviewed"}}}
        resp = session.patch(url, params={"updateMask.fieldPaths": "status"}, json=body, timeout=30)
        resp.raise_for_status()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="показать все записи, не только новые")
    ap.add_argument("--mark-reviewed", action="store_true", help="пометить показанные записи status=reviewed")
    args = ap.parse_args()

    if not os.path.exists(KEY_PATH):
        print(f"Не найден ключ: {KEY_PATH}", file=sys.stderr)
        sys.exit(1)

    session = get_session()
    items = fetch_all(session)

    if not args.all:
        items = [it for it in items if it.get("status") == "new"]

    items.sort(key=lambda it: it.get("createdAt", 0))

    if not items:
        print("Новых отзывов нет.")
        return

    for it in items:
        ts = it.get("createdAt")
        date_str = (
            datetime.datetime.fromtimestamp(ts / 1000).strftime("%Y-%m-%d %H:%M")
            if ts else "?"
        )
        print(f"[{date_str}] app={it.get('app')} type={it.get('type')} status={it.get('status')}")
        print(f"  {(it.get('text') or '').strip()}")
        print(f"  id={it.get('id')} uid={it.get('uid')}")
        print()

    print(f"Всего: {len(items)}")

    if args.mark_reviewed:
        mark_reviewed(session, [it["id"] for it in items])
        print(f"Помечено status=reviewed: {len(items)}")


if __name__ == "__main__":
    main()
