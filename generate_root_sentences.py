"""
generate_root_sentences.py — шаг 3 курса "Корни" (см. ROOTS_CURRICULUM.md):
20 частотных предложений на корень, по производным словам из уже
сгенерированной теории (generate_root_theory.py), с проверкой на
дублирование с существующим корпусом тренажёра (8239 предложений,
sentences_final.json) и той же жёсткой проверкой на כתיב מלא, что и в
теории.

Решение по дедупу (было открытым вопросом в ROOTS_CURRICULUM.md —
закрываем здесь): корпус тренажёра БЕЗ огласовок, новые предложения — С
огласовками, поэтому точное посимвольное сравнение никогда бы не
сработало. Сравниваем после снятия огласовок (см. strip_niqud) — это
ловит настоящие дубли по содержанию независимо от вокализации:
- точный дубль (после снятия никуда совпадает с корпусом дословно) —
  жёсткая проверка, как в generate_root_theory.py: считается причиной
  для повтора генерации, а если не ушло после всех попыток — громкий
  варнинг с указанием, что проверить руками.
- похожий, но не идентичный текст (пересечение слов > 70%) — мягкое
  предупреждение в лог, генерация не блокируется (спорные случаи решает
  человек, не скрипт).

Запуск:
    export GEMINI_API_KEY=твой_ключ
    python3 generate_root_sentences.py theory_output.json \
        --corpus sentences_final.json --out root_sentences_output.json
"""

import argparse
import json
import os
import sys
import time
import unicodedata

try:
    from google import genai
    from google.genai import types
except ImportError:
    print("Нужно: pip install google-genai --break-system-packages", file=sys.stderr)
    sys.exit(1)

from generate_root_theory import find_ktiv_chaser_violations, fix_ktiv_chaser, QuotaExhausted, _is_quota_error


RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "root": {"type": "string"},
        "sentences": {
            "type": "array",
            "minItems": 20,
            "maxItems": 20,
            "items": {
                "type": "object",
                "properties": {
                    "he": {
                        "type": "string",
                        "description": "Естественное частотное предложение с огласовками, СТРОГО כתיב מלא (полное написание — вав/יод там, где они реально пишутся в словах, никакого холам без вав, никакого кубуца вместо шурук).",
                    },
                    "ru": {"type": "string"},
                    "focus_word": {
                        "type": "string",
                        "description": "Словарная форма производного слова этого корня (как в списке ниже), которое практикует это предложение.",
                    },
                    "cloze_token": {
                        "type": "string",
                        "description": "ТОЧНАЯ подстрока внутри he — как focus_word реально выглядит в этом предложении (со своим спряжением/числом/родом) — то, что будет скрыто в упражнении на пропуск слова. Должна буквально встречаться в he.",
                    },
                },
                "required": ["he", "ru", "focus_word", "cloze_token"],
            },
        },
    },
    "required": ["root", "sentences"],
}

PROMPT_TEMPLATE = """Ты составляешь 20 естественных, частотных предложений на
иврите для практики КОРНЯ {root} — того самого, для которого уже готова
теоретическая карточка (ниже). Ученик уже прочитал эту теорию: каждое
предложение должно практиковать ОДНО из уже известных ему производных
слов, а не вводить новую лексику.

Производные слова этого корня (используй ТОЛЬКО их, в любых уместных
формах спряжения/числа/рода — не изобретай других производных):
{derived_list}

Правила:
- СТРОГО כתיב מלא везде — полное написание, вав/יод там, где они реально
  пишутся (חוֹק, не חֹק; קוּם, не קֻם). Огласовки обязательны.
- 20 предложений должны примерно равномерно распределиться по всем
  производным словам из списка (если слов 4 — примерно по 5 на каждое).
- Каждое предложение — законченная, естественная фраза, как в реальной
  речи или тексте, не искусственная грамматическая иллюстрация.
- Разнообразие: разные лица/числа/времена/регистры в рамках того, что
  реально употребимо для каждого слова (см. usage_note в списке) — не
  повторяй одну и ту же конструкцию 20 раз.
- cloze_token — точная подстрока из he (буква в букву, с огласовками),
  соответствующая focus_word в его форме именно в этом предложении.
- Не повторяй дословно примеры, которые уже были в теории (они даны для
  контекста слова, не для копирования).
- Ничего не выдумывай сверх задачи: если для какого-то производного
  реально нет 5 разных естественных контекстов — распредели чуть
  неравномерно, это нормально, лучше меньше, но естественно.

Теория корня (для контекста, не для копирования примеров):
{theory_context}
"""


def strip_niqud(text):
    """Снимает огласовки/кантилляцию (все непробельные комбинирующие
    знаки, Unicode-категория Mn) — используется для сравнения с
    корпусом тренажёра, который хранится без огласовок вообще."""
    return "".join(c for c in unicodedata.normalize("NFC", text) if unicodedata.category(c) != "Mn")


def load_corpus_stripped(corpus_path):
    with open(corpus_path, encoding="utf-8") as f:
        corpus = json.load(f)
    return {strip_niqud(item["h"]).strip() for item in corpus if "h" in item}


def find_dup_violations(result, corpus_stripped):
    """Жёсткие нарушения (точный дубль после снятия огласовок, или
    cloze_token отсутствует в he буквально) — причина для повтора
    генерации, как в find_ktiv_chaser_violations."""
    violations = []
    for i, s in enumerate(result.get("sentences", [])):
        he = s.get("he", "")
        stripped = strip_niqud(he).strip()
        if stripped in corpus_stripped:
            violations.append((f"sentences[{i}].he", he, "точный дубль с существующим корпусом тренажёра (после снятия огласовок)"))
        cloze = s.get("cloze_token", "")
        if cloze and cloze not in he:
            violations.append((f"sentences[{i}].cloze_token", cloze, "не найден буквально внутри he"))
    return violations


def find_near_dup_warnings(result, corpus_stripped, threshold=0.7):
    """Мягкие предупреждения (высокое пересечение слов с чем-то в
    корпусе) — НЕ блокирует генерацию, только лог для ручной проверки."""
    warnings = []
    corpus_word_sets = [set(c.split()) for c in corpus_stripped]
    for i, s in enumerate(result.get("sentences", [])):
        words = set(strip_niqud(s.get("he", "")).split())
        if not words:
            continue
        for cwords in corpus_word_sets:
            if not cwords:
                continue
            overlap = len(words & cwords) / len(words | cwords)
            if overlap >= threshold:
                warnings.append((f"sentences[{i}].he", s.get("he", ""), f"похоже на существующее предложение в корпусе (пересечение слов {overlap:.0%})"))
                break
    return warnings


def process_one(client, model, theory_entry, corpus_stripped, retries=3):
    derived_list = "\n".join(
        f"- {w['word']} ({w.get('binyan') or w['pos']}) — {w['translation']}"
        + (f"; {w['usage_note']}" if w.get("usage_note") else "")
        for w in theory_entry["derived_words"]
    )
    theory_context = theory_entry.get("semantic_field", "")
    prompt = PROMPT_TEMPLATE.format(root=theory_entry["root"], derived_list=derived_list, theory_context=theory_context)

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
        except Exception as e:
            if _is_quota_error(e):
                raise QuotaExhausted(str(e)) from e
            print(f"  попытка {attempt + 1}/{retries} не удалась ({e}), жду и повторяю...", file=sys.stderr)
            time.sleep(3)
            continue

        result = fix_ktiv_chaser(result)  # механический фикс כתיב חסר — см. generate_root_theory.process_one
        violations = find_ktiv_chaser_violations(result) + find_dup_violations(result, corpus_stripped)
        if not violations:
            near_dups = find_near_dup_warnings(result, corpus_stripped)
            if near_dups:
                print(f"  ⚠ {len(near_dups)} похожих (не идентичных) предложений для {theory_entry['root']} — не блокирует, но стоит взглянуть:", file=sys.stderr)
                for path, text, reason in near_dups:
                    print(f"    - [{path}] {text!r}: {reason}", file=sys.stderr)
            return result

        last_result, last_violations = result, violations
        print(f"  попытка {attempt + 1}/{retries}: {len(violations)} нарушени(й), повторяю генерацию...", file=sys.stderr)
        for path, word, reason in violations:
            print(f"    - [{path}] {word!r}: {reason}", file=sys.stderr)
        time.sleep(1)

    if last_result is not None:
        # См. тот же комментарий в generate_root_theory.process_one: не
        # отдаём дефектный результат дальше как черновик — None сигналит
        # run_roots_batch.py попробовать другую модель вместо того, чтобы
        # тихо сохранить нарушение при автопрогоне без наблюдателя.
        print(f"  !! после {retries} попыток для {theory_entry['root']} остались нарушения, результат ОТБРОШЕН:", file=sys.stderr)
        for path, word, reason in last_violations:
            print(f"    - [{path}] {word!r}: {reason}", file=sys.stderr)
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("theory_path", help="JSON-файл — результат generate_root_theory.py (список записей)")
    ap.add_argument("--corpus", default="sentences_final.json", help="существующий корпус тренажёра, для дедупа")
    ap.add_argument("--out", default="root_sentences_output.json")
    ap.add_argument("--model", default="gemini-3.6-flash")
    ap.add_argument("--api-key", default=None)
    args = ap.parse_args()

    api_key = args.api_key or os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("Нужен API-ключ: --api-key или GEMINI_API_KEY.", file=sys.stderr)
        sys.exit(1)

    client = genai.Client(api_key=api_key)

    with open(args.theory_path, encoding="utf-8") as f:
        theory_entries = json.load(f)

    print(f"Загружаю корпус для дедупа: {args.corpus}...", file=sys.stderr)
    corpus_stripped = load_corpus_stripped(args.corpus)
    print(f"  {len(corpus_stripped)} предложений в корпусе (без огласовок, для сравнения).", file=sys.stderr)

    results = []
    for i, entry in enumerate(theory_entries):
        print(f"[{i + 1}/{len(theory_entries)}] {entry['root']}...", file=sys.stderr)
        res = process_one(client, args.model, entry, corpus_stripped)
        if res is None:
            print(f"  !! не удалось для {entry['root']}", file=sys.stderr)
            continue
        results.append(res)

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\nГотово: {len(results)}/{len(theory_entries)} -> {args.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
