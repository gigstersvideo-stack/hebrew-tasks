"""Применяет итоговые решения сверки иврита тренажёра (Dicta Nakdan + два корректора).

    python apply_trainer_review.py <decisions.json>            # сухой прогон
    python apply_trainer_review.py <decisions.json> --write     # записать данные
    python apply_trainer_review.py <decisions.json> --write --revoice   # + переозвучить растущие тексты

decisions.json — список {corpus, sid, i, ours, value}; value == "" у дубля = удалить слово.
  corpus = roots_sentences  → root_sentences_all.json[ri].sentences[si].he   (sid "r<ri>_<si>")
           growing_texts    → growing_texts/checkpoint_NN.json и _audio.json  (sid "gtNN_sK")
           data_vocalized   → const DATA в index.html и hebrew_trainer.html   (sid "d<k>")
Правка применяется, только если слово на месте i всё ещё равно `ours` (иначе «устарело»),
пунктуация вокруг слова сохраняется. Если исправленное слово было cloze-токеном
(пропуск в упражнении) или focus_word, они обновляются тоже — иначе упражнение
ждало бы старую, ошибочную форму.

Прогресс учеников не затрагивается: курс «Корни» адресует фразы как «корень|номер»,
основная база — по номеру фразы; текст фразы в ключ не входит.
"""
import argparse
import asyncio
import glob
import importlib.util
import json
import os
import re
import sys
import unicodedata

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
HEB_ANY = re.compile(r"[\u0591-\u05C7\u05D0-\u05EA\u05F0-\u05F4]")
HEB = re.compile(r"[\u05D0-\u05EA]")
AUDIO_TOOL = r"E:\NEW BIG PROJ\tools\3_generate_audio.py"


def nfc(s):
    return unicodedata.normalize("NFC", s or "")


def heb_word(t):
    return nfc("".join(ch for ch in (t or "") if HEB_ANY.match(ch)))


def bare(t):
    return "".join(HEB.findall(t or ""))


def replace_span(t, new):
    idx = [k for k, ch in enumerate(t) if HEB_ANY.match(ch)]
    return t[:idx[0]] + new + t[idx[-1] + 1:] if idx else t


def edit_tokens(text, i, ours, value):
    """text — фраза строкой; правит токен i. Возвращает новый текст или None, если слово не на месте."""
    # i считается по непустым токенам (как split() сканера), а пробелы фразы
    # сохраняются как есть — в данных бывают двойные пробелы между фразами.
    toks = text.split(" ")
    real = [k for k, t in enumerate(toks) if t]
    if i >= len(real) or heb_word(toks[real[i]]) != nfc(ours):
        return None
    i = real[i]
    if value == "":
        tail = toks[i][len(toks[i].rstrip(".,!?:;…\"'»”")):]
        del toks[i]
        if tail and i > 0 and not toks[i - 1].endswith(tail):
            toks[i - 1] += tail
    else:
        toks[i] = replace_span(toks[i], nfc(value))
    return " ".join(toks)


def matres_lost(old, new):
    c = lambda s: sum(bare(s).count(x) for x in "וי")
    return new != "" and c(new) < c(old)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("decisions")
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--revoice", action="store_true")
    ap.add_argument("--rewrites", help="JSON-список переписанных целиком фраз (перекрывает пословные правки тех же фраз)")
    args = ap.parse_args()
    dec = json.load(open(args.decisions, encoding="utf-8"))
    rw = [r for r in json.load(open(args.rewrites, encoding="utf-8")) if not r.get("keep")] if args.rewrites else []
    rw_sids = {(r["corpus"], r["sid"]) for r in rw}
    dec = [d for d in dec if (d["corpus"], d["sid"]) not in rw_sids]
    rep = {"ok": 0, "stale": [], "skipped": [], "rewritten": 0}

    # --- курс «Корни»
    roots_path = os.path.join(HERE, "root_sentences_all.json")
    R = json.load(open(roots_path, encoding="utf-8"))
    # --- растущие тексты
    gt = {}
    for f in sorted(glob.glob(os.path.join(HERE, "growing_texts", "checkpoint_*.json"))):
        gt[os.path.basename(f)] = json.load(open(f, encoding="utf-8"))
    # --- основная база
    html = open(os.path.join(HERE, "index.html"), encoding="utf-8", newline="").read()
    a = html.index("const DATA = ")
    b = html.index("\n", a)
    line = html[a:b]
    body = line[len("const DATA = "):].rstrip("\r").rstrip()
    D = json.loads(body[:-1])
    revoice = set()

    # с конца предложения к началу, чтобы удаление дубля не сдвигало индексы других правок
    for d in sorted(dec, key=lambda x: (x["corpus"], x["sid"], -x["i"])):
        c, sid, i, ours, val = d["corpus"], d["sid"], d["i"], d["ours"], d["value"]
        if matres_lost(ours, val) and not d.get("force"):
            rep["skipped"].append((c, sid, ours, val, "теряются ו/י ктив мале"))
            continue
        if c == "roots_sentences":
            ri, si = map(int, sid[1:].split("_"))
            s = R[ri]["sentences"][si]
            new = edit_tokens(s["he"], i, ours, val)
            if new is None:
                rep["stale"].append((c, sid, ours)); continue
            s["he"] = new
            for fld in ("cloze_token", "focus_word"):
                if s.get(fld) and heb_word(s[fld]) == nfc(ours) and val:
                    s[fld] = replace_span(s[fld], nfc(val))
        elif c == "growing_texts":
            nn = sid[2:4]
            hit = False
            for fn in (f"checkpoint_{nn}.json", f"checkpoint_{nn}_audio.json"):
                g = gt.get(fn)
                if not g:
                    continue
                s = next((x for x in g["sentences"] if x["id"] == sid), None)
                if not s or i >= len(s["words"]) or heb_word(s["words"][i]["t"]) != nfc(ours):
                    continue
                if val == "":
                    del s["words"][i]
                else:
                    s["words"][i]["t"] = replace_span(s["words"][i]["t"], nfc(val))
                hit = True
            if not hit:
                rep["stale"].append((c, sid, ours)); continue
            revoice.add(sid)
        elif c == "data_vocalized":
            k = int(sid[1:])
            new = edit_tokens(D[k]["h"], i, ours, val)
            if new is None:
                rep["stale"].append((c, sid, ours)); continue
            D[k]["h"] = new
            if D[k].get("c") and heb_word(D[k]["c"]) == nfc(ours) and val:
                D[k]["c"] = replace_span(D[k]["c"], nfc(val))
        rep["ok"] += 1

    # --- фразы, переписанные целиком (ошибка не исправлялась заменой одного слова)
    for r in rw:
        c, sid = r["corpus"], r["sid"]
        if c == "roots_sentences":
            ri, si = map(int, sid[1:].split("_"))
            s = R[ri]["sentences"][si]
            s["he"], s["ru"] = nfc(r["he"]), r["ru"]
            for fld in ("cloze_token", "focus_word"):
                if r.get(fld):
                    s[fld] = nfc(r[fld])
            if s.get("cloze_token") and nfc(s["cloze_token"]) not in [heb_word(t) for t in s["he"].split(" ")] + s["he"].split(" "):
                print("  [!] cloze_token не найден во фразе:", sid)
        elif c == "data_vocalized":
            k = int(sid[1:])
            D[k]["h"], D[k]["r"] = nfc(r["h"]), r["r"]
            if r.get("c"):
                D[k]["c"] = nfc(r["c"])
            if r.get("s"):
                D[k]["s"] = r["s"]
            if D[k].get("c") and D[k]["c"] not in D[k]["h"]:
                print("  [!] c не найден во фразе:", sid)
        elif c == "growing_texts":
            nn = sid[2:4]
            new_t = nfc(r["he"]).split()
            ga = gt.get(f"checkpoint_{nn}_audio.json")
            sa = ga and next((x for x in ga["sentences"] if x["id"] == sid), None)
            if sa and [nfc(w["t"]) for w in sa["words"]] == new_t and all("start" in w for w in sa["words"]):
                # уже переписано и озвучено прошлым (прерванным) прогоном — не трогаем
                rep["rewritten"] += 1
                continue
            for fn in (f"checkpoint_{nn}.json", f"checkpoint_{nn}_audio.json"):
                g = gt.get(fn)
                s = g and next((x for x in g["sentences"] if x["id"] == sid), None)
                if s:
                    s["words"] = [{"t": w} for w in nfc(r["he"]).split()]
                    s["ru"] = r["ru"]
            revoice.add(sid)
        rep["rewritten"] += 1

    print(f"Переписано фраз: {rep['rewritten']}")
    print(f"Применено: {rep['ok']}, устарело: {len(rep['stale'])}, пропущено: {len(rep['skipped'])}, "
          f"к переозвучке (растущие тексты): {len(revoice)}")
    for x in rep["stale"][:20]:
        print("  УСТАРЕЛО", *x)
    for x in rep["skipped"][:20]:
        print("  ПРОПУСК", *x)
    if not args.write:
        print("Сухой прогон — ничего не записано.")
        return

    out = json.dumps(R, ensure_ascii=False, indent=1).replace("\n", "\r\n")
    open(roots_path, "w", encoding="utf-8", newline="").write(out)
    newline = "const DATA = " + json.dumps(D, ensure_ascii=False, separators=(",", ":")) + ";" + ("\r" if line.endswith("\r") else "")
    html2 = html[:a] + newline + html[b:]
    for fn in ("index.html", "hebrew_trainer.html"):
        open(os.path.join(HERE, fn), "w", encoding="utf-8", newline="").write(html2)

    def save_gt(fn):
        g = gt[fn]
        p = os.path.join(HERE, "growing_texts", fn)
        raw = open(p, encoding="utf-8", newline="").read()
        m_ind = re.match(r"\{\r?\n( +)", raw)  # файлы бывают с отступом 1 и 2 — сохраняем свой
        indent = len(m_ind.group(1)) if m_ind else None
        s = json.dumps(g, ensure_ascii=False, indent=indent)
        if "\r\n" in raw[:200]:
            s = s.replace("\n", "\r\n")
        if raw.endswith("\n"):
            s += "\r\n" if raw.endswith("\r\n") else "\n"
        open(p, "w", encoding="utf-8", newline="").write(s)

    if args.revoice and revoice:
        spec = importlib.util.spec_from_file_location("gen_audio", AUDIO_TOOL)
        m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
        subs = m.load_text_substitutions()

        async def run():
            # Каждый текст сохраняется сразу после своей переозвучки: прерванный прогон
            # (лимит времени) оставляет согласованные mp3+тайминги, повторный — доделывает.
            for fn, g in gt.items():
                if not fn.endswith("_audio.json"):
                    continue
                todo = [s for s in g["sentences"] if s["id"] in revoice]
                for s in g["sentences"]:
                    if s["id"] not in revoice:
                        continue
                    outp = os.path.join(HERE, "growing_texts", s["audio"])
                    text = m.build_tts_text(s["words"], subs)
                    for w in s["words"]:
                        w.pop("start", None); w.pop("end", None)
                    if getattr(m, "_azure_ipa", None) is not None and m._azure_ipa.needs_ipa(text):
                        bounds = m._azure_ipa.synthesize_ipa(text, outp)
                    else:
                        bounds, _ = await m.synthesize_sentence_with_retry(text, "he-IL-AvriNeural", outp)
                    if not m.assign_word_timings(s["words"], bounds):
                        print("  [!] тайминги не сошлись:", s["id"])
                if todo:
                    save_gt(fn)
                    save_gt(fn.replace("_audio.json", ".json"))
                    print("  озвучено и сохранено:", fn, len(todo), flush=True)
        asyncio.run(run())
    for fn in gt:
        save_gt(fn)
    print("Записано.")


if __name__ == "__main__":
    main()
