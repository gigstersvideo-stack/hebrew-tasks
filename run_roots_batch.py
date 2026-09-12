"""
run_roots_batch.py — пункт 4 из ROOTS_CURRICULUM.md: прогнать
generate_root_theory.py + generate_root_sentences.py на всех 200 корнях
базы 1 (сейчас готов только ק-ו-ם). Обёртка вокруг обоих скриптов,
которая:

- берёт корни из root_frequency.json по позиции, топ --count (200);
- для каждого корня, которого ещё нет в --theory-out — генерирует теорию,
  сразу же (если теория удалась) — 20 предложений для него, дописывает
  оба результата в файлы НЕМЕДЛЕННО (не в конце) — прерывание в любой
  момент не теряет уже сделанное;
- ротирует модели при исчерпании суточной квоты (see ROOTS_CURRICULUM.md,
  "Риски" — квота per-model, не per-key): при 3 неудачных попытках на
  одной модели (process_one внутри уже ретраит) — модель считается
  "мёртвой" на этот прогон, берём следующую из списка;
- рассчитан на то, что 200 корней не влезут в одни сутки — просто
  запускать повторно на следующий день, уже готовые корни не трогает.

Запуск:
    export GEMINI_API_KEY=твой_ключ
    python3 run_roots_batch.py --count 200 \
        --theory-out root_theory_all.json --sentences-out root_sentences_all.json
"""

import argparse
import json
import os
import sys
import time

try:
    from google import genai
except ImportError:
    print("Нужно: pip install google-genai --break-system-packages", file=sys.stderr)
    sys.exit(1)

from generate_root_theory import process_one as generate_theory_one, QuotaExhausted
from generate_root_sentences import process_one as generate_sentences_one, load_corpus_stripped

DEFAULT_MODELS = [
    "gemini-3.6-flash", "gemini-3.5-flash", "gemini-3.7-flash", "gemini-3.8-flash",
    "gemini-flash-latest", "gemini-flash-lite-latest", "gemini-3-flash-preview",
    "gemini-3.1-flash-lite", "gemini-3.5-flash-lite",
]


def load_json_list(path):
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return []


def save_json_list(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def with_model_rotation(models, dead_models, fn, *args):
    """Пробует fn(client, model, *args) по очереди моделей, пропуская уже
    помеченные dead_models. Модель помечается мёртвой (на весь остаток
    ЭТОГО прогона) ТОЛЬКО при настоящем исчерпании суточной квоты
    (QuotaExhausted) — если модель просто не смогла дать чистый
    результат для ЭТОГО ОДНОГО корня (например, у корня оказалось много
    форм с кубуцем, и модель раз за разом воспроизводила старое
    написание), это НЕ значит, что она сломана для остальных 199 —
    просто пробуем следующую модель для этого корня и идём дальше.
    (Реальный случай, пойманный при первом прогоне на 200 корнях: без
    этого различия один упрямый корень ב-נ-ה вычеркнул несколько рабочих
    моделей из ротации для всех последующих корней без всякой причины.)
    Возвращает (result, model_used) или (None, None), если либо все
    модели реально исчерпаны, либо ни одна не справилась с этим корнем."""
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
        print(f"    модель {model} не справилась именно с этим корнем — пробую следующую (модель НЕ помечена мёртвой)", file=sys.stderr)
    return None, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--roots", default="root_frequency.json")
    ap.add_argument("--count", type=int, default=200, help="сколько верхних по частоте корней брать из --roots")
    ap.add_argument("--corpus", default="sentences_final.json")
    ap.add_argument("--theory-out", default="root_theory_all.json")
    ap.add_argument("--sentences-out", default="root_sentences_all.json")
    ap.add_argument("--models", default=",".join(DEFAULT_MODELS))
    ap.add_argument("--limit-roots", type=int, default=None, help="ограничить кол-во корней в ЭТОМ запуске (для теста)")
    args = ap.parse_args()

    if not os.environ.get("GEMINI_API_KEY"):
        print("Нужен GEMINI_API_KEY в окружении.", file=sys.stderr)
        sys.exit(1)

    models = [m.strip() for m in args.models.split(",") if m.strip()]

    with open(args.roots, encoding="utf-8") as f:
        all_roots = json.load(f)
    all_roots.sort(key=lambda r: r["position"])
    target_roots = all_roots[: args.count]

    theory_results = load_json_list(args.theory_out)
    sentences_results = load_json_list(args.sentences_out)
    theory_by_root = {r["root"]: r for r in theory_results}
    sentences_done = {r["root"] for r in sentences_results}

    print(f"Цель: {len(target_roots)} корней. Уже есть теория: {len(theory_by_root)}, предложения: {len(sentences_done)}.", file=sys.stderr)

    print("Загружаю корпус для дедупа предложений...", file=sys.stderr)
    corpus_stripped = load_corpus_stripped(args.corpus)

    dead_theory_models = set()
    dead_sentences_models = set()
    processed_this_run = 0

    for i, root_entry in enumerate(target_roots):
        root = root_entry["root"]
        if root in theory_by_root and root in sentences_done:
            continue  # полностью готов с прошлого запуска
        if args.limit_roots is not None and processed_this_run >= args.limit_roots:
            print(f"Достигнут --limit-roots={args.limit_roots} для этого запуска, останавливаюсь.", file=sys.stderr)
            break

        print(f"[{i + 1}/{len(target_roots)}] {root}", file=sys.stderr)

        theory = theory_by_root.get(root)
        if theory is None:
            if len(dead_theory_models) >= len(models):
                print("  все модели исчерпаны для теории — останавливаюсь на сегодня.", file=sys.stderr)
                break
            theory, used = with_model_rotation(models, dead_theory_models, generate_theory_one, root_entry)
            if theory is None:
                print(f"  !! не удалось сгенерировать теорию для {root} — пропускаю до следующего запуска", file=sys.stderr)
                continue
            print(f"  теория готова (модель {used})", file=sys.stderr)
            theory_by_root[root] = theory
            save_json_list(args.theory_out, list(theory_by_root.values()))

        if root not in sentences_done:
            if len(dead_sentences_models) >= len(models):
                print("  все модели исчерпаны для предложений — останавливаюсь на сегодня.", file=sys.stderr)
                break
            sentences, used = with_model_rotation(models, dead_sentences_models, generate_sentences_one, theory, corpus_stripped)
            if sentences is None:
                print(f"  !! не удалось сгенерировать предложения для {root} — теория есть, предложения возьмём в следующий раз", file=sys.stderr)
                continue
            print(f"  предложения готовы (модель {used})", file=sys.stderr)
            sentences_results.append(sentences)
            sentences_done.add(root)
            save_json_list(args.sentences_out, sentences_results)

        processed_this_run += 1

    print(f"\nИтого в этом запуске обработано полностью: {processed_this_run}.", file=sys.stderr)
    print(f"Всего готово: теория {len(theory_by_root)}/{len(target_roots)}, предложения {len(sentences_done)}/{len(target_roots)}.", file=sys.stderr)


if __name__ == "__main__":
    main()
