"""Проверка карточек курса «Год праздников»: Hspell-правила ктив мале + сверка огласовок с Dicta Nakdan.

    python holidays/check_cards.py holidays/hanukkah_cards.json holidays/shabbat_cards.json
"""
import json, os, subprocess, sys, tempfile
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, r"E:\hebrew-reader")
import hebrew_spelling_rules as h  # noqa: E402

bad = 0
for path in sys.argv[1:]:
    cards = json.load(open(path, encoding="utf-8"))
    name = os.path.splitext(os.path.basename(path))[0]
    texts = [c["he"] for c in cards] + [c["focus_word"] for c in cards]
    for f in ("find_ktiv_chaser_violations", "find_yod_doubling_violations",
              "find_known_misspelling_violations", "find_dictionary_spelling_violations"):
        r = getattr(h, f)({"x": texts})
        for x in r:
            print(f"{name}: {f}: {x[1]} — {x[2]}")
        bad += len(r)
    for c in cards:
        if c["cloze_token"] not in c["he"]:
            print(f"{name}: cloze_token «{c['cloze_token']}» нет в «{c['he']}»"); bad += 1
    d = os.path.join(tempfile.gettempdir(), "holiday_cards_audit", name)
    os.makedirs(d, exist_ok=True)
    book = {"sentences": [{"id": f"c{i + 1}", "literal": "", "fluent": "-", "words":
                           [{"t": w, "lemma": w, "pos": "-", "tr": "-"} for w in c["he"].split()]} for i, c in enumerate(cards)]}
    json.dump(book, open(os.path.join(d, "book-data.json"), "w", encoding="utf-8"), ensure_ascii=False)
    r = subprocess.run([sys.executable, r"E:\hebrew-reader\tools\audit_book_hebrew.py", os.path.join(d, "book-data.json"),
                        "--text-only", "--out", d], capture_output=True, text=True, encoding="utf-8")
    rep = open(os.path.join(d, name + ".md"), encoding="utf-8").read() if os.path.exists(os.path.join(d, name + ".md")) else r.stdout
    print(rep.strip())
print("ИТОГО находок Hspell/cloze:", bad)
