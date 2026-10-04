"""Вливает «ядро» основной базы (2026-10-04): отобранные из карточек ульпана фразы,
огласованные и вычитанные, плюс новые фразы на пустые темы. Остальные карточки базы
становятся «Сборником ульпана» (без пометки k) — новичкам выключен по умолчанию.

    python apply_core.py <папка core>            # сухой прогон
    python apply_core.py <папка core> --write

<папка core>/proof/out*.json  — правки первого редактора [{k, h, r, c, l, drop?}]
<папка core>/ver/vout*.json   — вердикты проверки {"k<номер>": {v: accept|amend|drop, h, r, c, l}}
<папка core>/new_core.json + ver/voutnew.json — новые фразы ({"n<i>": ...})

Карточки ядра получают k: 1 (номер карточки не меняется — прогресс сохраняется);
новые фразы дописываются в КОНЕЦ основной базы (номера старых не сдвигаются).
"""
import argparse
import glob
import json
import os
import sys
import unicodedata

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
N = lambda s: unicodedata.normalize("NFC", s or "")
FIXES = {N("אֲסֵיפַת"): N("אֲסֵפַת"), N("אֲסֵיפָה"): N("אֲסֵפָה")}


def norm_h(h):
    h = N(h)
    for a, b in FIXES.items():
        h = h.replace(a, b)
    return " ".join(h.split())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("folder")
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args()
    html = open(os.path.join(HERE, "index.html"), encoding="utf-8", newline="").read()
    i0 = html.index("const DATA = ")
    i1 = html.index("\n", i0)
    line = html[i0:i1]
    D = json.loads(line[len("const DATA = "):].rstrip("\r").rstrip().rstrip(";"))
    n_before = len(D)

    edits = {}
    for f in glob.glob(os.path.join(a.folder, "proof", "out*.json")):
        for x in json.load(open(f, encoding="utf-8")):
            edits[f"k{x['k']}"] = x
    verdict = {}
    for f in glob.glob(os.path.join(a.folder, "ver", "vout*.json")):
        verdict.update(json.load(open(f, encoding="utf-8")))

    probs, cnt = [], {"core": 0, "drop": 0, "unverified": 0, "new": 0}
    for key, x in edits.items():
        v = verdict.get(key)
        if v is None:
            cnt["unverified"] += 1
            continue
        if x.get("drop") or v["v"] == "drop":
            cnt["drop"] += 1
            continue
        src = v if v["v"] == "amend" else x
        k = int(key[1:])
        h, c = norm_h(src["h"]), norm_h(src["c"]).strip()
        if c not in h:
            probs.append((key, "c не в h"))
            continue
        D[k].update(h=h, r=src["r"].strip(), c=c, l=int(src.get("l", D[k]["l"])), k=1)
        cnt["core"] += 1

    newp = json.load(open(os.path.join(a.folder, "new_core.json"), encoding="utf-8"))
    for i, x in enumerate(newp):
        v = verdict.get(f"n{i}")
        if v is None or v["v"] == "drop":
            continue
        src = v if v["v"] == "amend" else x
        h, c = norm_h(src["h"]), norm_h(src["c"]).strip()
        if c not in h:
            probs.append((f"n{i}", "c не в h"))
            continue
        D.append({"r": src["r"].strip(), "h": h, "l": int(src.get("l", x["l"])), "c": c, "k": 1})
        cnt["new"] += 1

    print(f"Ядро: из базы {cnt['core']}, новых {cnt['new']}, выброшено {cnt['drop']}, "
          f"без вердикта {cnt['unverified']}; карточек было {n_before}, стало {len(D)}")
    for p in probs:
        print("  [!]", *p)
    if not a.write or cnt["unverified"] or probs:
        print("Сухой прогон — ничего не записано." if not a.write else "Есть проблемы — не записываю.")
        return
    newline = "const DATA = " + json.dumps(D, ensure_ascii=False, separators=(",", ":")) + ";" + ("\r" if line.endswith("\r") else "")
    html2 = html[:i0] + newline + html[i1:]
    for fn in ("index.html", "hebrew_trainer.html"):
        open(os.path.join(HERE, fn), "w", encoding="utf-8", newline="").write(html2)
    print("Записано.")


if __name__ == "__main__":
    main()
