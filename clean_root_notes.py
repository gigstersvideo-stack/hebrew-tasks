"""
clean_root_notes.py — шаг 6 из ROOTS_CURRICULUM.md: пересоздать
`place_or_name_note`/`song_or_quote_note` сильной моделью пачками, вместо
полной регенерации всех 200 корней.

Почему именно эти два поля: они единственные требуют настоящего
фактического знания (реальный топоним/имя, реальная песня), а не работы
с уже заданным материалом (в отличие от derived_words/girzah_note,
которые привязаны к списку известных слов корня). ~95% корней сделаны
дешёвой моделью (gemini-flash-lite-latest) из-за суточной квоты сильных
моделей — и на реальном примере (ע-ו-ר) там пойманы: перепутанное слово
(רְחוֹק "далеко" вместо רְחוֹב "улица"), самопризнанная выдумка под
честным на вид хеджем ("по созвучию с корнем"), вероятно придуманное
слово. См. ROOTS_CURRICULUM.md.

Запуск:
    export GEMINI_API_KEY=твой_ключ
    python3 clean_root_notes.py root_theory_all.json --batch-size 12
"""

import argparse
import json
import os
import sys
import time

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

RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "roots": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "root": {"type": "string"},
                    "place_or_name_note": {
                        "type": "object",
                        "properties": {
                            "text": {"type": "string"},
                            "example_he": {"type": "string"},
                            "example_ru": {"type": "string"},
                        },
                        "required": ["text", "example_he", "example_ru"],
                    },
                    "song_or_quote_note": {"type": "string"},
                },
                "required": ["root", "place_or_name_note", "song_or_quote_note"],
            },
        },
    },
    "required": ["roots"],
}

PROMPT_TEMPLATE = """Ты честный педагог иврита. Для каждого из корней ниже нужно
заново подготовить ДВА поля — и только их (остальное уже готово и не
трогается):

- place_or_name_note: реальный, узнаваемый топоним (улица/город/мошав/
  кибуц/район) или личное имя, ПОСТРОЕННОЕ НА ЭТОМ КОРНЕ — не просто
  похожее по звучанию слово, а РЕАЛЬНО существующее место/имя, которое
  ты уверенно знаешь. Объект {{text, example_he, example_ru}}. Если не
  уверен на 100% — text ПУСТАЯ СТРОКА (и example_he/example_ru тоже
  пустые). НЕ изобретай "похожее по звучанию" название и не прикрывайся
  оговорками вроде "например, по созвучию" — это то же самое, что
  выдумать факт, только вежливой формулировкой. Честно пусто лучше, чем
  красиво неверно.
- song_or_quote_note: строчка из ДЕЙСТВИТЕЛЬНО известной израильской
  песни или расхожей цитаты с этим корнем — только если уверен, что она
  реальная и узнаваемая (можешь назвать исполнителя/автора). Если
  сомневаешься — пустая строка.

ВАЖНО, реальный прошлый случай: для корня ע-ו-ר модель уже один раз
написала "רְחוֹק הַעֲרָה" как название улицы, перепутав רְחוֹב (улица) и
רְחוֹק (далеко), и сама же пометила это как "по созвучию с корнем" — то
есть заведомо неточный факт под честным на вид хеджем. Не повторяй эту
ошибку: если не уверен — пусто, без оговорок.

Огласовки — СТРОГО כתיב מלא (полное написание): вав/יод там, где они
реально пишутся (חוֹק не חֹק, קוּם не קֻם).

Корни (с производными словами для контекста — не копировать их дословно,
они уже есть в готовой карточке):
{roots_block}
"""


def build_roots_block(entries):
    lines = []
    for e in entries:
        words = ", ".join(w["word"] for w in e.get("derived_words", [])[:4])
        lines.append(f"- {e['root']}: {words}")
    return "\n".join(lines)


def with_model_rotation(models, dead_models, fn, *args):
    for model in models:
        if model in dead_models:
            continue
        client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
        try:
            result = fn(client, model, *args)
        except QuotaExhausted:
            print(f"    модель {model}: суточная квота исчерпана — мёртвая до конца этого прогона", file=sys.stderr)
            dead_models.add(model)
            continue
        if result is not None:
            return result, model
        print(f"    модель {model} не справилась именно с этой пачкой — пробую следующую", file=sys.stderr)
    return None, None


def process_batch(client, model, batch, retries=3):
    prompt = PROMPT_TEMPLATE.format(roots_block=build_roots_block(batch))
    expected_roots = {e["root"] for e in batch}
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

        result = fix_ktiv_chaser(result)
        violations = find_ktiv_chaser_violations(result)
        got_roots = {r["root"] for r in result.get("roots", [])}
        missing = expected_roots - got_roots
        if missing:
            violations = violations + [("roots", str(missing), "корни отсутствуют в ответе пачки")]

        if not violations:
            return {r["root"]: r for r in result["roots"]}

        last_result, last_violations = result, violations
        print(f"  попытка {attempt + 1}/{retries}: {len(violations)} нарушени(й), повторяю...", file=sys.stderr)
        for path, word, reason in violations:
            print(f"    - [{path}] {word!r}: {reason}", file=sys.stderr)
        time.sleep(1)

    if last_result is not None:
        print("  !! после всех попыток остались нарушения, пачка ОТБРОШЕНА:", file=sys.stderr)
        for path, word, reason in last_violations:
            print(f"    - [{path}] {word!r}: {reason}", file=sys.stderr)
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("theory_path", help="root_theory_all.json")
    ap.add_argument("--out", default=None, help="по умолчанию — перезаписать theory_path")
    ap.add_argument("--batch-size", type=int, default=12)
    ap.add_argument("--models", default=",".join(DEFAULT_MODELS))
    args = ap.parse_args()
    out_path = args.out or args.theory_path

    if not os.environ.get("GEMINI_API_KEY"):
        print("Нужен GEMINI_API_KEY в окружении.", file=sys.stderr)
        sys.exit(1)
    models = [m.strip() for m in args.models.split(",") if m.strip()]

    with open(args.theory_path, encoding="utf-8") as f:
        entries = json.load(f)
    by_root = {e["root"]: e for e in entries}

    batches = [entries[i:i + args.batch_size] for i in range(0, len(entries), args.batch_size)]
    print(f"{len(entries)} корней -> {len(batches)} пачек по {args.batch_size}.", file=sys.stderr)

    dead_models = set()
    cleaned = 0
    for bi, batch in enumerate(batches):
        print(f"[пачка {bi + 1}/{len(batches)}] {', '.join(e['root'] for e in batch)}", file=sys.stderr)
        if len(dead_models) >= len(models):
            print("  все модели исчерпаны — останавливаюсь на сегодня.", file=sys.stderr)
            break
        cleaned_batch, used = with_model_rotation(models, dead_models, process_batch, batch)
        if cleaned_batch is None:
            print(f"  !! пачка {bi + 1} не удалась ни на одной модели — пропускаю, останется как есть", file=sys.stderr)
            continue
        for root, cleaned_fields in cleaned_batch.items():
            if root not in by_root:
                continue
            by_root[root]["place_or_name_note"] = cleaned_fields["place_or_name_note"]
            by_root[root]["song_or_quote_note"] = cleaned_fields["song_or_quote_note"]
            cleaned += 1
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(list(by_root.values()), f, ensure_ascii=False, indent=2)
        print(f"  пачка готова (модель {used}), сохранено.", file=sys.stderr)

    print(f"\nГотово: {cleaned}/{len(entries)} корней почищено -> {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
