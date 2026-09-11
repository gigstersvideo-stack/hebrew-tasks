"""
build_root_frequency_list.py — строит частотный список корней иврита из
реального ранжированного списка слов, для курса "Корни" (см.
ROOTS_CURRICULUM.md). По духу и структуре — как 5_gemini_pipeline.py
читалки (structured output, батчи, --resume), только задача проще:
на каждое слово нужен только его корень (или "нет корня" для служебных
слов/местоимений/предлогов) — суммирование по корням делается потом
локально в Python, не нейронкой (детерминированный подсчёт, не то, для
чего нужен LLM).

Вход: hebrew_word_frequency_1_650.json — 650 слов современного иврита,
ранжированных по частоте (взято с teachmehebrew.com/hebrew-frequency-list.html,
верхние ~10000 слов сайта, здесь только первые 650 — этого достаточно,
чтобы набрать топ-200 корней: самые частотные корни встречаются уже в
первой тысяче слов по закону Ципфа, а хвост длинный и редкий).

Метрика ранжирования корня: сумма 1/rank по всем словам этого корня в
исходном списке — не просто "сколько раз встретился", а
частотно-взвешенно (слово на 5-м месте значит для скора корня заметно
больше, чем слово на 500-м).

Установка:
    pip install google-genai --break-system-packages

Получить бесплатный API-ключ:
    https://aistudio.google.com/apikey

Запуск:
    export GEMINI_API_KEY=твой_ключ
    python3 build_root_frequency_list.py hebrew_word_frequency_1_650.json --out root_frequency.json

Если прервётся — просто повтори с --resume, работает так же, как в
5_gemini_pipeline.py.
"""

import argparse
import json
import os
import sys
import time
from collections import defaultdict
from pathlib import Path

try:
    from google import genai
    from google.genai import types
except ImportError:
    print("Нужно: pip install google-genai --break-system-packages", file=sys.stderr)
    sys.exit(1)


RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "words": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "he": {"type": "string", "description": "то же ивритское слово, точно как на входе (для сверки порядка в ответе)"},
                    "root": {
                        "type": "string",
                        "description": (
                            "Корень слова ивритскими буквами через дефис, ПОЛНОЕ написание "
                            "(тот же формат, что в остальных данных проекта, напр. ק-ט-ן, "
                            "ש-מ-ר). Если у слова нет корня в обычном смысле — служебное "
                            "слово, местоимение, предлог, союз, частица (של, את, על, לא, "
                            "הוא, עם...) — пустая строка."
                        ),
                    },
                },
                "required": ["he", "root"],
            },
        },
    },
    "required": ["words"],
}

PROMPT_TEMPLATE = """Ты — лингвист-эксперт по ивриту. Для каждого из следующих
слов современного иврита определи его корень (שורש).

Правила:
- Формат корня: буквы иврита через дефис, ПОЛНОЕ написание (כתיב מלא) — с
  буквами вав/יод там, где они реально пишутся в современном написании,
  как в словарях для изучающих язык. Примеры: ק-ט-ן, ש-מ-ר, כ-ת-ב.
- Если слово — служебное (предлог, союз, местоимение, частица, наречие
  без ясного трёхбуквенного корня — של, את, על, לא, הוא, עם, זה, גם, ב,
  כל, ה и т.п.) — верни ПУСТУЮ строку "" в поле root, не выдумывай корень.
- Если слово — реальное существительное/глагол/прилагательное с корнем,
  всегда его укажи, даже если корень необычный (слабый, полый и т.п.).
- Верни слова СТРОГО в том же порядке, что и на входе, каждое ровно один раз.

Слова (по одному на строку, с транслитерацией и переводом для контекста):
{words_block}
"""


def process_batch(client, model, batch, retries=3):
    words_block = "\n".join(f"{w['he']} ({w['translit']}) — {w['en']}" for w in batch)
    prompt = PROMPT_TEMPLATE.format(words_block=words_block)
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
            return json.loads(response.text)
        except Exception as e:
            print(f"  попытка {attempt + 1}/{retries} не удалась ({e}), жду и повторяю...", file=sys.stderr)
            time.sleep(3)
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("words_path", help="JSON-файл вида hebrew_word_frequency_1_650.json")
    ap.add_argument("--out", default="root_frequency_raw.json", help="куда писать промежуточный результат (слово->корень)")
    ap.add_argument("--final-out", default="root_frequency.json", help="куда писать итоговый агрегированный список корней")
    ap.add_argument("--batch-size", type=int, default=40)
    ap.add_argument("--model", default="gemini-3.6-flash")
    ap.add_argument("--api-key", default=None, help="или задай переменную окружения GEMINI_API_KEY")
    ap.add_argument("--resume", action="store_true")
    args = ap.parse_args()

    api_key = args.api_key or os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("Нужен API-ключ: флаг --api-key или переменная окружения GEMINI_API_KEY.", file=sys.stderr)
        print("Получить бесплатно: https://aistudio.google.com/apikey", file=sys.stderr)
        sys.exit(1)

    client = genai.Client(api_key=api_key)

    with open(args.words_path, encoding="utf-8") as f:
        words = json.load(f)
    print(f"Слов на входе: {len(words)}", file=sys.stderr)

    results = []
    start_idx = 0
    out_path = Path(args.out)
    if args.resume and out_path.exists():
        with open(out_path, encoding="utf-8") as f:
            existing = json.load(f)
        results = existing.get("results", [])
        start_idx = existing.get("next_index", len(results))
        print(f"Продолжаю с слова {start_idx + 1} (готово: {len(results)}).", file=sys.stderr)

    i = start_idx
    incomplete = False
    while i < len(words):
        batch = words[i:i + args.batch_size]
        print(f"[{i + 1}-{i + len(batch)} / {len(words)}] обрабатываю...", file=sys.stderr)

        batch_result = process_batch(client, args.model, batch)
        if batch_result is None:
            print(f"\n!! Не удалось обработать этот кусок. Прогресс до слова {i} сохранён.\n"
                  f"Перезапусти с --resume.", file=sys.stderr)
            incomplete = True
            break

        got = batch_result.get("words", [])
        if len(got) != len(batch):
            print(f"  ⚠ ожидал {len(batch)} слов в ответе, получил {len(got)} — "
                  f"стоит сверить этот участок вручную", file=sys.stderr)

        # merge rank/translit/en back in (model only echoes "he" + "root")
        for orig, resp in zip(batch, got):
            results.append({
                "rank": orig["rank"], "he": orig["he"], "translit": orig["translit"],
                "en": orig["en"], "root": resp.get("root", ""),
            })

        i += args.batch_size
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump({"results": results, "next_index": i}, f, ensure_ascii=False, indent=2)
        time.sleep(1)

    if incomplete:
        sys.exit(1)

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"results": results}, f, ensure_ascii=False, indent=2)

    # ---- aggregate by root: sum(1/rank) as the frequency score ----
    scores = defaultdict(float)
    members = defaultdict(list)
    for r in results:
        root = (r.get("root") or "").strip()
        if not root:
            continue
        scores[root] += 1.0 / r["rank"]
        members[root].append({"he": r["he"], "translit": r["translit"], "en": r["en"], "rank": r["rank"]})

    ranked_roots = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    final = [
        {
            "position": i + 1,
            "root": root,
            "score": round(score, 5),
            "member_count": len(members[root]),
            "members": sorted(members[root], key=lambda m: m["rank"]),
        }
        for i, (root, score) in enumerate(ranked_roots)
    ]

    with open(args.final_out, "w", encoding="utf-8") as f:
        json.dump(final, f, ensure_ascii=False, indent=2)

    print(f"\nГотово. Слов обработано: {len(results)}. Уникальных корней: {len(final)}.", file=sys.stderr)
    print(f"Промежуточный результат: {args.out}", file=sys.stderr)
    print(f"Итоговый частотный список корней: {args.final_out}", file=sys.stderr)
    if len(final) < 200:
        print(f"\n⚠ Получилось только {len(final)} корней, а нужно 200 — "
              f"этого исходного списка (650 слов) не хватило. Нужно либо взять "
              f"больше слов из teachmehebrew (следующие ранги), либо принять "
              f"меньший размер первой базы.", file=sys.stderr)


if __name__ == "__main__":
    main()
