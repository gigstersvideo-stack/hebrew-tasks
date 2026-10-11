"""Готовая озвучка для фраз тренажёра, которые голос телефона, скорее всего, прочтёт неверно.

    python tts_gen.py            # озвучить всё из tts_risk.json, чего ещё нет в tts/
    python tts_gen.py --limit 20 # пробно

Берёт тексты из tts_risk_texts.json (см. tts_risk_scan.py), синтезирует тем же конвейером, что книги
читалки (edge-tts he-IL-AvriNeural + подмены записи + Azure IPA для известных трудных форм —
E:\\NEW BIG PROJ\\tools\\3_generate_audio.py), пишет tts/<sha1>.mp3 и tts/manifest.json
{текст NFC: "tts/<sha1>.mp3"}. В тренажёре speak() сначала ищет текст в манифесте и играет
запись; если её нет — голос устройства, как раньше.
"""
import argparse
import asyncio
import hashlib
import importlib.util
import json
import os
import re
import sys
import unicodedata

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "tts")
AUDIO_TOOL = r"E:\NEW BIG PROJ\tools\3_generate_audio.py"
N = lambda s: unicodedata.normalize("NFC", s or "")


def key_of(text):
    return hashlib.sha1(N(text).encode("utf-8")).hexdigest()[:16]


def texts_from_risk():
    return [N(t) for t in json.load(open(os.path.join(HERE, "tts_risk_texts.json"), encoding="utf-8"))]


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int)
    a = ap.parse_args()
    spec = importlib.util.spec_from_file_location("gen_audio", AUDIO_TOOL)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    subs = m.load_text_substitutions()
    os.makedirs(OUT, exist_ok=True)
    man_path = os.path.join(OUT, "manifest.json")
    man = json.load(open(man_path, encoding="utf-8")) if os.path.exists(man_path) else {}
    todo = [t for t in texts_from_risk() if t not in man]
    if a.limit:
        todo = todo[:a.limit]
    print("К озвучке:", len(todo), "| уже есть:", len(man))
    ipa = getattr(m, "_azure_ipa", None)
    for i, text in enumerate(todo):
        rel = f"tts/{key_of(text)}.mp3"
        out = os.path.join(HERE, rel.replace("/", os.sep))
        tts_text = m.build_tts_text([{"t": w} for w in text.split()], subs)
        # Строго как конвейер книг (AZURE_IPA_README.md — «библия» озвучки): Azure+IPA только для форм из
        # tts_ipa_rules.json, остальное — edge-tts с подменами. Запись «всё через Azure» мимо подмен хуже
        # (проверено на слух 2026-10-11). Любая новая запись — только после проверки владельцем на слух.
        if ipa is not None and ipa.needs_ipa(tts_text):
            ipa.synthesize_ipa(tts_text, out)
        else:
            _, fell = await m.synthesize_sentence_with_retry(tts_text, "he-IL-AvriNeural", out)
            if fell:
                print("  [!] edge-tts не справился, gTTS:", text[:50])
        man[text] = rel
        if i % 25 == 0:
            json.dump(man, open(man_path, "w", encoding="utf-8"), ensure_ascii=False, indent=0)
            print(f"  {i}/{len(todo)}")
    json.dump(man, open(man_path, "w", encoding="utf-8"), ensure_ascii=False, indent=0)
    print("Готово. В манифесте:", len(man))


if __name__ == "__main__":
    asyncio.run(main())
