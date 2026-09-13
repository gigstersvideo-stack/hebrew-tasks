"""
regenerate_derived_words.py — точечно перегенерирует ТОЛЬКО поле
derived_words для списка корней, у которых оно дублирует формы,
уже показанные в girzah_note.compare (найдено при разборе отзыва
пользователя про ה-י-ה: הָיָה и לִהְיוֹת показаны дважды — один раз как
сравнение форм в girzah_note, второй раз как будто "новая" лексика в
derived_words). Остальные поля записи (semantic_field, girzah_note,
comprehension_question и т.д.) не трогает.

Запуск:
    export GEMINI_API_KEY=твой_ключ
    python3 regenerate_derived_words.py
"""

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

from generate_root_theory import QuotaExhausted, _is_quota_error
from hebrew_spelling_rules import fix_ktiv_chaser, find_ktiv_chaser_violations

DEFAULT_MODELS = [
    "gemini-3.6-flash", "gemini-3.5-flash", "gemini-3.7-flash", "gemini-3.8-flash",
    "gemini-flash-latest", "gemini-flash-lite-latest", "gemini-3-flash-preview",
    "gemini-3.1-flash-lite", "gemini-3.5-flash-lite",
]

TARGET_ROOTS = [
    "ה-י-ה", "א-ח-ר", "כ-ו-ח", "נ-ס-ף", "ר-ב-ה", "מ-ו-ת",
    "נ-ק-פ", "ק-ש-ה", "נ-ש-ק", "י-צ-ב", "ר-ו-ח", "א-ו-ר",
]

DERIVED_WORDS_SCHEMA = {
    "type": "array",
    "minItems": 2, "maxItems": 4,
    "items": {
        "type": "object",
        "properties": {
            "word": {"type": "string", "description": "слово с огласовками, полное написание (כתיב מלא)"},
            "binyan": {"type": "string", "description": "название биньяна ивритом (פָּעַל/פִּעֵל/הִפְעִיל/נִפְעַל/הִתְפַּעֵל/פֻּעַל/הֻפְעַל) для глаголов, иначе пусто"},
            "pos": {"type": "string", "description": "часть речи"},
            "translation": {"type": "string"},
            "register": {
                "type": "string",
                "description": "уровень/употребимость: очень частотное / обычное / разговорное / официальное / книжное / литературное / устаревшее / редкое / сленговое",
            },
            "usage_note": {
                "type": "string",
                "description": (
                    "1-2 предложения: неочевидный нюанс употребления, стилистический "
                    "оттенок, с чем путают, или ложный друг — только если реально есть "
                    "что сказать, иначе пусто, не растягивай искусственно."
                ),
            },
            "examples": {
                "type": "array",
                "minItems": 1, "maxItems": 2,
                "items": {
                    "type": "object",
                    "properties": {
                        "he": {"type": "string", "description": "естественное предложение-пример с огласовками"},
                        "ru": {"type": "string"},
                    },
                    "required": ["he", "ru"],
                },
            },
        },
        "required": ["word", "binyan", "pos", "translation", "register", "usage_note", "examples"],
    },
}

PROMPT_TEMPLATE = """Ты работаешь как строгий, внимательный педагог иврита. Готовишь
список derived_words (реально употребимых производных слов) для КОРНЯ
{root} в теоретической карточке курса изучающих иврит.

Контекст (уже написан для этой же карточки, НЕ повторяй эти слова):
- semantic_field: {semantic_field}
- girzah_note.text: {girzah_text}
- Формы, УЖЕ показанные в сравнении форм этого корня (girzah_note.compare)
  — они НЕ считаются derived_words, ученик их уже видел на этой же
  карточке в другом контексте (сравнение грамматики, не лексика):
{banned_forms}

ЗАДАЧА: дай 2-4 РЕАЛЬНО частотных производных слова этого корня, КОТОРЫЕ
НЕ СОВПАДАЮТ ни с одной из форм выше (ни по написанию, ни как та же самая
словарная единица в другой словоформе — например, если запрещена форма
прошедшего времени глагола, не подставляй инфинитив/будущее время ТОГО ЖЕ
глагола вместо неё, это тот же лексический баг, который мы чиним). Если у
корня относительно мало реально живых производных и без повтора трудно
набрать 4 — дай 2-3 настоящих, не растягивай дублированием.

Правила честности (обязательно):
- СТРОГО полное написание (כתיב מלא) для каждого слова и примера — звук "о"
  всегда וֹ (никогда голый холам), звук "у" всегда וּ (никогда кубуц).
- Не выдумывай огласовку, если не уверен — уточни в usage_note.
- На каждое производное: огласовки, биньян (для глаголов), часть речи,
  перевод, уровень употребимости, короткий нюанс если есть что сказать по
  существу, 1-2 живых примера-предложения с переводом.
- Ищи РАЗНЫЕ по смыслу/биньяну слова этого корня — не несколько форм
  одного и того же слова.
"""


def build_prompt(entry):
    banned = entry.get("girzah_note", {}).get("compare", [])
    banned_lines = "\n".join(f"  - {b.get('form','')} ({b.get('binyan','')})" for b in banned) or "  (сравнения форм нет)"
    return PROMPT_TEMPLATE.format(
        root=entry["root"],
        semantic_field=entry.get("semantic_field", ""),
        girzah_text=entry.get("girzah_note", {}).get("text", ""),
        banned_forms=banned_lines,
    )


def strip_niqud(s):
    return re.sub(r"[֑-ׇ]", "", s or "")


def process_one(client, model, entry, retries=3):
    banned_stripped = {strip_niqud(b.get("form", "")) for b in entry.get("girzah_note", {}).get("compare", [])}
    prompt = build_prompt(entry)
    last_words = None
    for attempt in range(retries):
        try:
            response = client.models.generate_content(
                model=model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=DERIVED_WORDS_SCHEMA,
                ),
            )
            derived_words = json.loads(response.text)
        except Exception as e:
            if _is_quota_error(e):
                raise QuotaExhausted(str(e)) from e
            print(f"    попытка {attempt + 1}/{retries} не удалась ({e}), повторяю...", file=sys.stderr)
            time.sleep(3)
            continue

        derived_words = fix_ktiv_chaser(derived_words)
        violations = find_ktiv_chaser_violations(derived_words)
        overlap = [d["word"] for d in derived_words if strip_niqud(d.get("word", "")) in banned_stripped]

        if not violations and not overlap:
            return derived_words

        last_words = derived_words
        if violations:
            print(f"    попытка {attempt + 1}/{retries}: {len(violations)} нарушени(й) כתיב חסר после автофикса, повторяю...", file=sys.stderr)
        if overlap:
            print(f"    попытка {attempt + 1}/{retries}: всё ещё повторяет запрещённые формы: {overlap}, повторяю...", file=sys.stderr)
        time.sleep(1)

    print(f"  !! после {retries} попыток остались проблемы для {entry['root']}, результат ОТБРОШЕН", file=sys.stderr)
    return None


def main():
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("Нужен GEMINI_API_KEY в окружении.", file=sys.stderr)
        sys.exit(1)

    theory_path = "root_theory_all.json"
    with open(theory_path, encoding="utf-8") as f:
        theory_list = json.load(f)
    by_root = {e["root"]: e for e in theory_list}

    dead_models = set()
    updated = 0
    for root in TARGET_ROOTS:
        entry = by_root.get(root)
        if entry is None:
            print(f"!! корень {root} не найден в {theory_path}, пропускаю", file=sys.stderr)
            continue

        print(f"[{root}] старые derived_words: {[d['word'] for d in entry['derived_words']]}", file=sys.stderr)
        result = None
        for model in DEFAULT_MODELS:
            if model in dead_models:
                continue
            client = genai.Client(api_key=api_key)
            try:
                result = process_one(client, model, entry)
            except QuotaExhausted:
                print(f"  модель {model}: квота исчерпана — мёртвая до конца прогона", file=sys.stderr)
                dead_models.add(model)
                continue
            if result is not None:
                print(f"  новые derived_words ({model}): {[d['word'] for d in result]}", file=sys.stderr)
                break
            print(f"  модель {model} не справилась — пробую следующую", file=sys.stderr)

        if result is None:
            print(f"!! не удалось перегенерировать {root}, оставляю как было", file=sys.stderr)
            continue

        entry["derived_words"] = result
        updated += 1

    with open(theory_path, "w", encoding="utf-8") as f:
        json.dump(theory_list, f, ensure_ascii=False, indent=2)
    print(f"\nОбновлено {updated}/{len(TARGET_ROOTS)} корней, сохранено в {theory_path}.", file=sys.stderr)


if __name__ == "__main__":
    main()
