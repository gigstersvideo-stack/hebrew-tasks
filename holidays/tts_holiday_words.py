"""Записи главных слов курса «Праздники Израиля» (path.words всех глав) голосом Avri — тем же конвейером,
что tts_gen.py и книги читалки (edge-tts + подмены + Azure IPA для трудных форм).

    python holidays/tts_holiday_words.py            # озвучить в tts_pending/ (ещё не в сайте)
    python holidays/tts_holiday_words.py --approve  # после проверки на слух: перенести в tts/ и manifest.json

Правило из tts_gen.py: любая новая запись — только после проверки владельцем на слух, поэтому сначала
tts_pending/ + страница прослушивания, и лишь потом --approve."""
import argparse, asyncio, hashlib, importlib.util, json, os, shutil, sys, unicodedata
sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PENDING, LIVE = os.path.join(ROOT, "tts_pending"), os.path.join(ROOT, "tts")
AUDIO_TOOL = r"E:\NEW BIG PROJ\tools\3_generate_audio.py"
N = lambda s: unicodedata.normalize("NFC", s or "")
key_of = lambda t: hashlib.sha1(N(t).encode("utf-8")).hexdigest()[:16]


def words():
    sys.path.insert(0, HERE)
    from path_content import PATH
    out = []
    for ch, pc in PATH.items():
        for he, ru in pc.get("words", []):
            if N(he) not in [x[0] for x in out]:
                out.append((N(he), ru, ch))
    return out


async def record():
    spec = importlib.util.spec_from_file_location("gen_audio", AUDIO_TOOL)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    subs = m.load_text_substitutions()
    ipa = getattr(m, "_azure_ipa", None)
    os.makedirs(PENDING, exist_ok=True)
    live = json.load(open(os.path.join(LIVE, "manifest.json"), encoding="utf-8"))
    man = {}
    for he, ru, ch in words():
        if he in live:
            continue
        out = os.path.join(PENDING, key_of(he) + ".mp3")
        if not os.path.exists(out):
            tts_text = m.build_tts_text([{"t": w} for w in he.split()], subs)
            if ipa is not None and ipa.needs_ipa(tts_text):
                ipa.synthesize_ipa(tts_text, out)
            else:
                _, fell = await m.synthesize_sentence_with_retry(tts_text, "he-IL-AvriNeural", out)
                if fell:
                    print("  [!] edge-tts не справился, gTTS:", he)
        man[he] = {"file": "tts/" + key_of(he) + ".mp3", "ru": ru, "chapter": ch}
    json.dump(man, open(os.path.join(PENDING, "pending.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("Записано (ждут проверки):", len(man))


def approve():
    pend = json.load(open(os.path.join(PENDING, "pending.json"), encoding="utf-8"))
    mp = os.path.join(LIVE, "manifest.json")
    live = json.load(open(mp, encoding="utf-8"))
    for he, x in pend.items():
        shutil.copyfile(os.path.join(PENDING, os.path.basename(x["file"])), os.path.join(ROOT, x["file"].replace("/", os.sep)))
        live[he] = x["file"]
    json.dump(live, open(mp, "w", encoding="utf-8"), ensure_ascii=False, indent=0)
    print("В манифесте:", len(live))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--approve", action="store_true")
    a = ap.parse_args()
    approve() if a.approve else asyncio.run(record())
