"""Слова, которые голос телефона (speechSynthesis), скорее всего, прочитает неверно.

    python tts_risk_scan.py            # скан + отчёт tts_risk.json
    python tts_risk_scan.py --limit 50 # пробный прогон на первых N текстах

Почему так: голос устройства почти не смотрит на огласовки и читает написание самым частым
способом (כִּיתֵּב → «кьютеф», как כְּתָב/כָּתַב). Модель этого «самого частого способа» — Dicta Nakdan:
снимаем огласовки, отдаём текст в Nakdan и сравниваем его вариант по умолчанию с нашей огласовкой.
Сравнение — по «звучанию» (согласные с б/в, к/х, п/ф, ш/с + классы гласных; шва не учитывается),
а не по точной записи, чтобы не ловить расхождения, которых на слух нет.

Источники (всё, что произносит speak() в тренажёре): фразы ядра основной базы (DATA, k:1),
фразы курса «Корни» (root_sentences_all.json), слова и примеры теории корней (root_theory_all.json),
слова вступления «Почему корни» (ROOTS_INTRO в index.html).
"""
import argparse
import json
import os
import re
import sys
import unicodedata

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, r"E:\hebrew-reader\tools")
from audit_book_hebrew import Nakdan  # noqa: E402  (кэшированный клиент Dicta)

N = lambda s: unicodedata.normalize("NFC", s or "")
NIQQUD = re.compile(r"[\u0591-\u05C7]")
HEB = re.compile(r"[\u05D0-\u05EA]")
DAGESH, SHIN, SIN, HOLAM, SHURUQ = "\u05BC", "\u05C1", "\u05C2", "\u05B9", "\u05BC"
# \u0445\u0430\u0442\u0430\u0444\u044B (\u05B1 \u05B2 \u05B3) \u043D\u0435 \u0440\u0430\u0437\u043B\u0438\u0447\u0430\u0435\u043C: \u043D\u0430 \u0441\u043B\u0443\u0445 \u044D\u0442\u043E \u043F\u043E\u0447\u0442\u0438 \u0448\u0432\u0430, \u0430 Dicta \u0441\u0442\u0430\u0432\u0438\u0442 \u0438\u0445 \u043F\u043E \u043D\u043E\u0440\u043C\u0435 \u0442\u0430\u043C, \u0433\u0434\u0435 \u0443 \u043D\u0430\u0441 \u0448\u0432\u0430
VOWEL = {"\u05B7": "a", "\u05B8": "a", "\u05B6": "e", "\u05B5": "e",
         "\u05B4": "i", "\u05B9": "o", "\u05BA": "o", "\u05BB": "u"}
BKP = {"ב": "b", "כ": "k", "ך": "k", "פ": "p", "ף": "p"}


def clusters(w):
    out = []
    for ch in unicodedata.normalize("NFD", w):
        if HEB.match(ch):
            out.append([ch, ""])
        elif NIQQUD.match(ch) and out:
            out[-1][1] += ch
    return out


QAMATS, HATAF_QAMATS = "ָ", "ֳ"


def sound(w):
    """«Звучание» слова: список (согласная, гласная). Согласная — буква (+б/в, к/х, п/ф, ш/с),
    гласная — класс; камац помечается «A» (может быть и «а», и камац катан «о»).
    Шурук и холам малэ отдают гласную предыдущей согласной; немые йуд/вав полного письма
    (Dicta их опускает) выбрасываются, а йуд/вав перед шуруком/холамом — согласная."""
    seq = []  # [согласная, гласная, немая?]
    for letter, marks in clusters(w):
        v = "".join("A" if m == QAMATS else VOWEL[m] for m in marks if m in VOWEL or m == QAMATS)
        if HATAF_QAMATS in marks:
            v += "o"
        if letter == "ו" and not v and DAGESH in marks:     # шурук
            if seq: seq[-1][1] += "u"; seq[-1][2] = False
            else: seq.append(["", "u", False])
            continue
        if letter == "ו" and HOLAM in marks and not v.replace("o", ""):  # холам малэ
            if seq: seq[-1][1] += "o"; seq[-1][2] = False
            else: seq.append(["", "o", False])
            continue
        c = letter
        if letter in BKP:
            c = BKP[letter] + ("!" if DAGESH in marks else "~")
        elif letter == "ש":
            c = "s" if SIN in marks else "sh"
        seq.append([c, v, letter in "יו" and not v])
    return [(c, v) for c, v, mute in seq if not (mute and not v)]


def same_sound(a, b):
    """Совпадает ли звучание; камац (A) равен и «а», и «о» (камац катан ↔ холам малэ)."""
    if len(a) != len(b):
        return False
    for (c1, v1), (c2, v2) in zip(a, b):
        if c1 != c2:
            return False
        if v1 == v2:
            continue
        norm = lambda v, x: v.replace("A", x)
        if not any(norm(v1, x) == norm(v2, y) for x in "ao" for y in "ao"):
            return False
    return True


def words_of(text):
    return [w for w in re.split(r"[\s\u05BE\-–—]+", N(text)) if HEB.search(w)]


def load_sources():
    src = []  # (откуда, текст)
    html = open(os.path.join(HERE, "index.html"), encoding="utf-8").read()
    i0 = html.index("const DATA = "); i1 = html.index("\n", i0)
    data = json.loads(html[i0 + 13:i1].rstrip("\r").rstrip().rstrip(";"))
    src += [(f"ядро #{k}", d["h"]) for k, d in enumerate(data) if d.get("k")]
    for r in json.load(open(os.path.join(HERE, "root_sentences_all.json"), encoding="utf-8")):
        src += [(f"корни {r['root']} фраза", s["he"]) for s in r.get("sentences", [])]
    for t in json.load(open(os.path.join(HERE, "root_theory_all.json"), encoding="utf-8")):
        root = t["root"]
        for w in t.get("derived_words") or []:
            src.append((f"корни {root} слово", w["word"]))
            src += [(f"корни {root} пример", e["he"]) for e in w.get("examples") or []]
        for p in t.get("set_phrases") or []:
            src.append((f"корни {root} выражение", p["phrase_he"]))
            if p.get("example_he"):
                src.append((f"корни {root} пример", p["example_he"]))
        for f in ("modern_usage_note", "place_or_name_note"):
            n = t.get(f)
            if isinstance(n, dict) and n.get("example_he"):
                src.append((f"корни {root} пример", n["example_he"]))
    j0 = html.index("const ROOTS_INTRO = {"); j1 = html.index("const ROOTS_INTRO_SEEN_KEY", j0)
    for w in re.findall(r'\["([\u0590-\u05FF ]+)", "', html[j0:j1]):
        src.append(("«Почему корни»", w))
    return src


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int)
    a = ap.parse_args()
    nak = Nakdan(os.path.join(HERE, ".cache", "nakdan_tts.json"))
    src = load_sources()
    if a.limit:
        src = src[:a.limit]
    print("Текстов к проверке:", len(src))
    risks = {}  # наша форма → {nakdan, alt_ok, n, where[]}
    checked = 0
    risky_texts = []  # все фразы с рискованными словами — для озвучки (tts_gen.py)
    for k, (where, text) in enumerate(src):
        ours = words_of(text)
        if not ours:
            continue
        toks = nak.analyze(NIQQUD.sub("", " ".join(ours)))
        toks = [t for t in (toks or []) if HEB.search(t.get("word", ""))]
        if len(toks) != len(ours):
            continue  # Nakdan разбил иначе — пропускаем, без ложных тревог
        for w, t in zip(ours, toks):
            opts = [N(o[0].replace("|", "")) for o in t.get("options") or [] if o]
            if not opts:
                continue
            checked += 1
            mine = sound(w)
            if same_sound(sound(opts[0]), mine):
                continue
            r = risks.setdefault(re.sub(r"[^֐-׿]", "", N(w)), {"nakdan": opts[0], "ours_in_options": any(same_sound(sound(o), mine) for o in opts[1:]),
                                        "n": 0, "where": []})
            r["n"] += 1
            if N(text) not in risky_texts:
                risky_texts.append(N(text))
            if len(r["where"]) < 3:
                r["where"].append(f"{where}: {text}")
        if k % 200 == 0:
            nak.save()
            print(f"  {k}/{len(src)}  рискованных форм: {len(risks)}")
    nak.save()
    out = sorted(({"word": w, **r} for w, r in risks.items()), key=lambda x: -x["n"])
    json.dump(out, open(os.path.join(HERE, "tts_risk.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    json.dump(risky_texts, open(os.path.join(HERE, "tts_risk_texts.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=0)
    print("Фраз с рисками:", len(risky_texts))
    amb = sum(1 for x in out if x["ours_in_options"])
    print(f"Проверено слов: {checked}; рискованных форм: {len(out)} "
          f"(из них омографы — наша форма у Nakdan во 2+ варианте: {amb}; остальные — Nakdan нашей формы не предлагает)")


if __name__ == "__main__":
    main()
