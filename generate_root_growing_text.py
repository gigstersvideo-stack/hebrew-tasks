"""
generate_root_growing_text.py — растущие тексты курса "Корни" (см.
ROOTS_CURRICULUM.md): короткий связный текст после каждых 3 пройденных
корней, i+1 (в основном словарь уже пройденных корней + немного нового),
длина растёт с каждым чекпоинтом (15, 17, 19, ... — +2 предложения на
чекпоинт).

Выход рассчитан на прямую подачу в 3_generate_audio.py читалки без
переделки: { "sentences": [ { "id", "words": [{"t": "..."}] } ] } — этот
скрипт генерирует текст, разбивает на слова и проставляет "ttsEngine":
"gtts" там, где встречается известное проблемное слово (см.
tts_known_bad_words.json у читалки) — без настоящей лемматизации,
сравнением по буквенному скелету без огласовок (тот же принцип, что
_bare_consonants в generate_root_theory.py).

Запуск (один чекпоинт для проверки):
    export GEMINI_API_KEY=твой_ключ
    python3 generate_root_growing_text.py --checkpoint 1 \
        --theory root_theory_all.json --roots root_frequency.json \
        --out growing_texts/checkpoint_01.json

Дальше можно прогнать через читалочный пайплайн:
    cd ../hebrew-reader
    python 3_generate_audio.py <out>.json <out>_audio.json audio_gt01
"""

import argparse
import json
import os
import re
import sys
import time
import unicodedata

try:
    from google import genai
    from google.genai import types
except ImportError:
    print("Нужно: pip install google-genai --break-system-packages", file=sys.stderr)
    sys.exit(1)

from generate_root_theory import fix_ktiv_chaser, find_ktiv_chaser_violations, QuotaExhausted, _is_quota_error

DEFAULT_MODELS = [
    "gemini-3.6-flash", "gemini-3.5-flash", "gemini-3.7-flash", "gemini-3.8-flash",
    "gemini-flash-latest", "gemini-flash-lite-latest", "gemini-3-flash-preview",
    "gemini-3.1-flash-lite", "gemini-3.5-flash-lite",
]

ROOTS_PER_CHECKPOINT = 3
START_SENTENCES = 15
GROWTH_PER_CHECKPOINT = 2  # +2 предложения на каждый следующий текст

# Известные проблемные слова читалочного edge-tts (см.
# E:\hebrew-reader\tts_known_bad_words.json) — сравниваем по буквенному
# скелету без огласовок, не по точной лемме (лемматизации тут нет).
TTS_BAD_WORDS_PATH = os.path.join("..", "hebrew-reader", "tts_known_bad_words.json")

RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string", "description": "короткое название текста, по-русски"},
        "sentences": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "he": {"type": "string", "description": "предложение с огласовками, СТРОГО כתיב מלא"},
                    "ru": {"type": "string"},
                },
                "required": ["he", "ru"],
            },
        },
    },
    "required": ["title", "sentences"],
}

PROMPT_TEMPLATE = """Ты пишешь короткий связный текст (рассказ или сценка) на
иврите для курса изучения языка по методу i+1: читатель уже прошёл
следующие {n_roots} корней и знает эти слова (список ниже) — текст должен
быть построен ПРЕИМУЩЕСТВЕННО из них, чтобы читатель понимал большую
часть без словаря.

Разрешено немного нового: примерно 1 незнакомое, но частотное слово на
10-15 знакомых — не больше, и только если оно реально нужно для
естественности фразы (не выкручивайся, чтобы обойти обычное слово, но и
не вставляй новое просто так). Если совсем без нового слова фраза
получается неестественной — лучше добавить одно новое, чем писать
корявую фразу только известными словами.

Требования:
- Ровно {target_sentences} предложений, связный текст (не набор
  несвязанных фраз) — с завязкой, развитием, концовкой.
- title — короткое название по-русски.
- Огласовки — СТРОГО כתיב מלא везде: вав/יод там, где они реально
  пишутся (חוֹק не חֹק, קוּם не קֻם).
- Естественный, живой иврит — как в настоящем детском/подростковом
  рассказе, не искусственно составленные из словаря фразы.

Известные слова (используй в первую очередь их, в любых уместных формах
спряжения/числа/рода):
{known_words}
"""


def strip_niqud(text):
    return "".join(c for c in unicodedata.normalize("NFC", text) if unicodedata.category(c) != "Mn")


def load_bad_word_skeletons(path=TTS_BAD_WORDS_PATH):
    if not os.path.exists(path):
        print(f"  (нет {path} — пропускаю авто-gTTS по известным словам)", file=sys.stderr)
        return set()
    with open(path, encoding="utf-8") as f:
        items = json.load(f)
    return {strip_niqud(item["lemma"]) for item in items}


def collect_known_words(theory_entries, up_to_root_count):
    """derived_words всех корней с 1-й по up_to_root_count-ю позицию
    (theory_entries уже отсортированы по позиции вызывающим кодом)."""
    words = []
    for entry in theory_entries[:up_to_root_count]:
        for w in entry.get("derived_words", []):
            if w.get("word"):
                words.append(w["word"])
    return words


def tokenize_for_tts(he_sentence):
    """Простое разбиение по пробелам — 3_generate_audio.py реконструирует
    текст как ' '.join(w['t'] for w in words), так что границы токенов
    здесь и есть границы синтеза; знаки препинания остаются приклеенными
    к соседнему слову, как и в реальных книгах читалки."""
    return [t for t in he_sentence.split(" ") if t]


def process_checkpoint(client, model, known_words, checkpoint, target_sentences, retries=3):
    prompt = PROMPT_TEMPLATE.format(
        n_roots=checkpoint * ROOTS_PER_CHECKPOINT,
        target_sentences=target_sentences,
        known_words=", ".join(known_words),
    )
    last_result, last_violations = None, None
    for attempt in range(retries):
        try:
            response = client.models.generate_content(
                model=model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=RESPONSE_SCHEMA,
                ),
            )
            result = json.loads(response.text)
            result = fix_ktiv_chaser(result)
        except Exception as e:
            if _is_quota_error(e):
                raise QuotaExhausted(str(e)) from e
            print(f"  попытка {attempt + 1}/{retries} не удалась ({e}), жду и повторяю...", file=sys.stderr)
            time.sleep(3)
            continue

        violations = find_ktiv_chaser_violations(result)
        got = len(result.get("sentences", []))
        if got != target_sentences:
            violations = violations + [("sentences", str(got), f"ожидалось {target_sentences} предложений")]

        if not violations:
            return result

        last_result, last_violations = result, violations
        print(f"  попытка {attempt + 1}/{retries}: {len(violations)} нарушени(й), повторяю...", file=sys.stderr)
        for path, word, reason in violations:
            print(f"    - [{path}] {word!r}: {reason}", file=sys.stderr)
        time.sleep(1)

    if last_result is not None:
        print("  !! после всех попыток остались нарушения, результат ОТБРОШЕН:", file=sys.stderr)
        for path, word, reason in last_violations:
            print(f"    - [{path}] {word!r}: {reason}", file=sys.stderr)
    return None


def to_book_data(result, checkpoint, bad_skeletons):
    """Преобразует {title, sentences:[{he,ru}]} в формат, который прямо
    съедает 3_generate_audio.py читалки."""
    sentences = []
    gtts_count = 0
    for i, s in enumerate(result["sentences"]):
        words = [{"t": t} for t in tokenize_for_tts(s["he"])]
        needs_gtts = any(strip_niqud(w["t"]).strip(".,!?;:\"'()«»") in bad_skeletons for w in words)
        sent = {"id": f"gt{checkpoint:02d}_s{i + 1}", "ru": s["ru"], "words": words}
        if needs_gtts:
            sent["ttsEngine"] = "gtts"
            gtts_count += 1
        sentences.append(sent)
    return {"title": result["title"], "checkpoint": checkpoint, "sentences": sentences}, gtts_count


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--checkpoint", type=int, required=True, help="1 = после корней 1-3, 2 = после 4-6, и т.д.")
    ap.add_argument("--theory", default="root_theory_all.json")
    ap.add_argument("--roots", default="root_frequency.json")
    ap.add_argument("--out", required=True)
    ap.add_argument("--models", default=",".join(DEFAULT_MODELS))
    args = ap.parse_args()

    if not os.environ.get("GEMINI_API_KEY"):
        print("Нужен GEMINI_API_KEY в окружении.", file=sys.stderr)
        sys.exit(1)
    models = [m.strip() for m in args.models.split(",") if m.strip()]

    with open(args.roots, encoding="utf-8") as f:
        rf = json.load(f)
    rf.sort(key=lambda r: r["position"])
    ordered_roots = [r["root"] for r in rf[:200]]

    with open(args.theory, encoding="utf-8") as f:
        theory_list = json.load(f)
    theory_by_root = {t["root"]: t for t in theory_list}
    theory_ordered = [theory_by_root[r] for r in ordered_roots if r in theory_by_root]

    up_to = args.checkpoint * ROOTS_PER_CHECKPOINT
    known_words = collect_known_words(theory_ordered, up_to)
    target_sentences = START_SENTENCES + (args.checkpoint - 1) * GROWTH_PER_CHECKPOINT
    bad_skeletons = load_bad_word_skeletons()

    print(f"Чекпоинт {args.checkpoint}: корни 1-{up_to} ({len(known_words)} известных слов), цель {target_sentences} предложений.", file=sys.stderr)

    dead_models = set()
    result = None
    for model in models:
        if model in dead_models:
            continue
        client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
        try:
            result = process_checkpoint(client, model, known_words, args.checkpoint, target_sentences)
        except QuotaExhausted:
            print(f"  модель {model}: квота исчерпана, следующая", file=sys.stderr)
            dead_models.add(model)
            continue
        if result is not None:
            print(f"  готово (модель {model})", file=sys.stderr)
            break

    if result is None:
        print("!! не удалось сгенерировать текст ни на одной модели", file=sys.stderr)
        sys.exit(1)

    book_data, gtts_count = to_book_data(result, args.checkpoint, bad_skeletons)
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(book_data, f, ensure_ascii=False, indent=2)
    print(f"\nГотово: «{book_data['title']}», {len(book_data['sentences'])} предложений "
          f"({gtts_count} через gTTS по известным проблемным словам) -> {args.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
