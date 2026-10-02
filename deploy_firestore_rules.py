"""Тесты и деплой правил Firestore (firestore.rules) проекта ivrit-progress.

    python deploy_firestore_rules.py          # только тесты (ничего не меняет)
    python deploy_firestore_rules.py --deploy # тесты, и если все зелёные — выкатить

Тесты гоняются на серверах Google через Firebase Rules API (projects:test),
то есть проверяется ровно тот движок, который потом будет применять правила.
Ключ сервис-аккаунта — тот же, что у fetch_feedback.py (вне репозитория).
"""
import argparse
import os
import sys

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

import google.auth.transport.requests
from google.oauth2 import service_account

HERE = os.path.dirname(os.path.abspath(__file__))
RULES_PATH = os.path.join(HERE, "firestore.rules")
PROJECT_ID = "ivrit-progress"
KEY_FILENAME = "ivrit-progress-firebase-adminsdk-fbsvc-156ed0e778.json"
KEY_PATH = os.environ.get("IVRIT_FIREBASE_KEY") or os.path.join(
    os.environ.get("APPDATA") or os.path.expanduser("~"), "ivrit", KEY_FILENAME)
API = "https://firebaserules.googleapis.com/v1"
OWNER = "JYkCrXPmBwhFaW5fyb6eQcWkihf1"
DOCS = "/databases/(default)/documents"


def fb(**over):
    """Валидный отзыв, как его пишут оба сайта; over — правки поверх."""
    d = {"type": "bug", "text": "кнопка не работает", "app": "reader",
         "url": "https://readivrit.pages.dev/", "uid": None,
         "userAgent": "Mozilla/5.0", "createdAt": 1790000000000,
         "status": "new", "context": None}
    for k, v in over.items():
        if v is DROP:
            d.pop(k, None)
        else:
            d[k] = v
    return d


DROP = object()


def case(expect, method, path, auth=None, data=None, existing=None, name=""):
    req = {"path": DOCS + path, "method": method,
           "auth": {"uid": auth} if auth else None}
    if data is not None:
        req["resource"] = {"data": data}
    tc = {"expectation": expect, "request": req}
    if existing is not None:
        tc["resource"] = {"data": existing}
    return name, tc


CASES = [
    case("ALLOW", "create", "/feedback/a", data=fb(), name="аноним шлёт обычный отзыв"),
    case("ALLOW", "create", "/feedback/a", data=fb(app="trainer", type="suggestion",
         context={"screen": "roots", "card": "12:ru2he"}), name="отзыв тренажёра с context"),
    case("ALLOW", "create", "/feedback/a", auth="u1", data=fb(uid="u1"), name="вошедший со своим uid"),
    case("DENY", "create", "/feedback/a", auth="u1", data=fb(uid="u2"), name="чужой uid в отзыве"),
    case("DENY", "create", "/feedback/a", data=fb(uid="u2"), name="аноним выдаёт себя за uid"),
    case("DENY", "create", "/feedback/a", data=fb(type="<img src=x onerror=1>"), name="тип вне белого списка"),
    case("DENY", "create", "/feedback/a", data=fb(text="x" * 5001), name="текст длиннее 5000"),
    case("DENY", "create", "/feedback/a", data=fb(text=""), name="пустой текст"),
    case("DENY", "create", "/feedback/a", data=fb(status="reviewed"), name="сразу «просмотрено»"),
    case("DENY", "create", "/feedback/a", data=fb(admin=True), name="лишнее поле"),
    case("DENY", "create", "/feedback/a", data=fb(app="evil"), name="неизвестное приложение"),
    case("DENY", "create", "/feedback/a", data=fb(text=DROP), name="без текста"),
    case("DENY", "get", "/feedback/a", existing=fb(), name="аноним читает отзыв"),
    case("DENY", "get", "/feedback/a", auth="u1", existing=fb(), name="не-владелец читает отзыв"),
    case("ALLOW", "get", "/feedback/a", auth=OWNER, existing=fb(), name="владелец читает отзыв"),
    case("ALLOW", "update", "/feedback/a", auth=OWNER, existing=fb(),
         data=fb(status="reviewed"), name="владелец помечает просмотренным"),
    case("DENY", "update", "/feedback/a", auth=OWNER, existing=fb(),
         data=fb(status="reviewed", text="подменил"), name="владелец не переписывает текст"),
    case("DENY", "delete", "/feedback/a", auth="u1", existing=fb(), name="не-владелец удаляет"),
    case("ALLOW", "delete", "/feedback/a", auth=OWNER, existing=fb(), name="владелец удаляет"),
    case("ALLOW", "get", "/users/u1/settings/ai", auth="u1", existing={"apiKey": "k"}, name="свой ключ Gemini"),
    case("DENY", "get", "/users/u1/settings/ai", auth="u2", existing={"apiKey": "k"}, name="чужой ключ Gemini"),
    case("DENY", "get", "/users/u1/settings/ai", existing={"apiKey": "k"}, name="аноним читает ключ"),
    case("ALLOW", "create", "/users/u1/progress/c1", auth="u1", data={"due": 1}, name="свой прогресс"),
    case("DENY", "create", "/users/u1/progress/c1", auth="u2", data={"due": 1}, name="чужой прогресс"),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--deploy", action="store_true")
    args = ap.parse_args()

    creds = service_account.Credentials.from_service_account_file(
        KEY_PATH, scopes=["https://www.googleapis.com/auth/cloud-platform"])
    s = google.auth.transport.requests.AuthorizedSession(creds)
    source = {"files": [{"name": "firestore.rules", "content": open(RULES_PATH, encoding="utf-8").read()}]}

    r = s.post(f"{API}/projects/{PROJECT_ID}:test", timeout=60,
               json={"source": source, "testSuite": {"testCases": [tc for _, tc in CASES]}})
    if r.status_code != 200:
        print("Ошибка API тестов:", r.status_code, r.text[:2000])
        sys.exit(2)
    body = r.json()
    for issue in body.get("issues", []):
        print("ПРАВИЛА:", issue.get("severity"), issue.get("description"))
    failed = 0
    for (name, tc), res in zip(CASES, body.get("testResults", [])):
        ok = res.get("state") == "SUCCESS"
        failed += not ok
        print(("OK  " if ok else "FAIL"), tc["expectation"], "—", name,
              "" if ok else " | " + "; ".join(res.get("debugMessages", [])))
    print(f"\n{len(CASES) - failed} passed, {failed} failed")
    if failed or any(i.get("severity") == "ERROR" for i in body.get("issues", [])):
        sys.exit(1)

    if not args.deploy:
        print("Только тесты. Для выката: --deploy")
        return
    rs = s.post(f"{API}/projects/{PROJECT_ID}/rulesets", json={"source": source}, timeout=60)
    rs.raise_for_status()
    ruleset = rs.json()["name"]
    rel_name = f"projects/{PROJECT_ID}/releases/cloud.firestore"
    up = s.patch(f"{API}/{rel_name}", timeout=60,
                 json={"release": {"name": rel_name, "rulesetName": ruleset}})
    up.raise_for_status()
    print("Выкачено:", ruleset)


if __name__ == "__main__":
    main()
