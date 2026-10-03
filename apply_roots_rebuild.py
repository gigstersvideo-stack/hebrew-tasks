"""Вливает пересборку курса «Корни» (2026-10-03): уроки, где слова были не со своего корня.

    python apply_roots_rebuild.py <папка>            # сухой прогон
    python apply_roots_rebuild.py <папка> --write

В <папке>:
  fullout*.json   — уроки целиком: [{"ri", "root", "theory", "sentences"}]. Корень урока
                    меняется (переименован или урок заменён новым); теория заменяет старую
                    запись root_theory_all.json, фразы — весь список урока; ROOTS_ORDER в
                    index.html обновляется на том же месте.
  groupsout*.json — точечные замены: [{"ri", "root", "sentences": {"<sid>": фраза},
                    "derived": [{"old_word", "item"}], "drop_phrases": [...]}].

Прогресс по урокам с новым корнем начинается заново (ключи курса — «корень|номер»).
"""
import argparse
import glob
import json
import os
import re
import sys
import unicodedata

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
nfc = lambda s: unicodedata.normalize("NFC", s or "")
bare = lambda s: re.sub(r"[^א-ת]", "", s or "")


def dump(path, obj, raw_before):
    m = re.match(r"\[?\{?\r?\n( +)", raw_before) or re.match(r"[\[{]\r?\n( +)", raw_before)
    indent = len(m.group(1)) if m else None
    s = json.dumps(obj, ensure_ascii=False, indent=indent)
    if "\r\n" in raw_before[:500]:
        s = s.replace("\n", "\r\n")
    if raw_before.endswith("\n"):
        s += "\r\n" if raw_before.endswith("\r\n") else "\n"
    open(path, "w", encoding="utf-8", newline="").write(s)


def check_sentence(s, where, problems):
    for k in ("he", "ru", "focus_word", "cloze_token"):
        if not s.get(k):
            problems.append(f"{where}: пустое поле {k}")
    if s.get("cloze_token") and nfc(s["cloze_token"]) not in nfc(s["he"]):
        problems.append(f"{where}: cloze_token не найден во фразе")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("folder")
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args()

    p_s = os.path.join(HERE, "root_sentences_all.json")
    p_t = os.path.join(HERE, "root_theory_all.json")
    raw_s = open(p_s, encoding="utf-8", newline="").read()
    raw_t = open(p_t, encoding="utf-8", newline="").read()
    R, T = json.loads(raw_s), json.loads(raw_t)
    t_idx = {t["root"]: i for i, t in enumerate(T)}
    html = open(os.path.join(HERE, "index.html"), encoding="utf-8", newline="").read()
    problems, renames = [], []

    for f in sorted(glob.glob(os.path.join(a.folder, "fullout*.json"))):
        for L in json.load(open(f, encoding="utf-8")):
            ri, new_root = L["ri"], L["root"]
            old_root = R[ri]["root"]
            if new_root != old_root and new_root in t_idx:
                problems.append(f"ri {ri}: корень {new_root} уже есть в курсе")
            for i, s in enumerate(L["sentences"]):
                check_sentence(s, f"ri {ri} #{i}", problems)
            th = dict(L["theory"]); th["root"] = new_root
            T[t_idx[old_root]] = th
            R[ri] = {"root": new_root, "sentences": [
                {k: (nfc(v) if k != "ru" else v) for k, v in s.items()} for s in L["sentences"]]}
            renames.append((old_root, new_root))

    n_sent = n_der = n_drop = 0
    for f in sorted(glob.glob(os.path.join(a.folder, "groupsout*.json"))):
        for L in json.load(open(f, encoding="utf-8")):
            ri = L["ri"]
            if R[ri]["root"] != L["root"]:
                problems.append(f"ri {ri}: корень в замене {L['root']} ≠ {R[ri]['root']}")
                continue
            for sid, s in L.get("sentences", {}).items():
                check_sentence(s, f"ri {ri} s{sid}", problems)
                R[ri]["sentences"][int(sid)] = {k: (nfc(v) if k != "ru" else v) for k, v in s.items()}
                n_sent += 1
            th = T[t_idx[L["root"]]]
            dw = th.setdefault("derived_words", [])
            for d in L.get("derived", []):
                hit = next((k for k, x in enumerate(dw) if bare(x.get("word")) == bare(d["old_word"])), None)
                if hit is None:
                    dw.append(d["item"])
                else:
                    dw[hit] = d["item"]
                n_der += 1
            drops = {bare(x) for x in L.get("drop_phrases", [])}
            before = len(th.get("set_phrases", []))
            th["set_phrases"] = [x for x in th.get("set_phrases", []) if bare(x.get("phrase_he")) not in drops]
            n_drop += before - len(th["set_phrases"])

    m = re.search(r"const ROOTS_ORDER = (\[[^\]]*\]);", html)
    order = json.loads(m.group(1))
    for old, new in renames:
        if old in order:
            order[order.index(old)] = new
        else:
            problems.append(f"ROOTS_ORDER: нет {old}")
    order_json = "[" + ", ".join(json.dumps(x, ensure_ascii=False) for x in order) + "]"
    html2 = html[:m.start(1)] + order_json + html[m.end(1):]

    if sorted(r["root"] for r in R) != sorted(t["root"] for t in T) or len({r["root"] for r in R}) != len(R):
        problems.append("корни фраз и теории разошлись или повторяются")
    print(f"Уроков переписано целиком: {len(renames)}; фраз заменено: {n_sent}; "
          f"слов теории: {n_der}; выражений убрано: {n_drop}")
    for x in problems:
        print("  [!]", x)
    if not a.write:
        print("Сухой прогон — ничего не записано.")
        return
    if problems:
        print("Есть проблемы — не записываю.")
        return
    dump(p_s, R, raw_s)
    dump(p_t, T, raw_t)
    for fn in ("index.html", "hebrew_trainer.html"):
        open(os.path.join(HERE, fn), "w", encoding="utf-8", newline="").write(html2)
    print("Записано.")


if __name__ == "__main__":
    main()
