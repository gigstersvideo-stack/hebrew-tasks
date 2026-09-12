"""
run_growing_texts_batch.py — прогоняет все чекпоинты растущих текстов
(каждые 3 корня, roots 1-198 из 200 — последняя пара 199-200 не
образует полного тройного чекпоинта, это ожидаемо, не баг). Для каждого:
генерирует текст (generate_root_growing_text.py, с ротацией моделей по
суточной квоте), затем сразу прогоняет через реальный аудио-пайплайн
читалки (3_generate_audio.py — соседний репозиторий, вызывается как
subprocess).

Resume: пропускает чекпоинты, у которых уже есть и текст, и аудио-JSON.

Запуск:
    export GEMINI_API_KEY=твой_ключ
    python3 run_growing_texts_batch.py
"""

import argparse
import json
import os
import subprocess
import sys

try:
    from google import genai
except ImportError:
    print("Нужно: pip install google-genai --break-system-packages", file=sys.stderr)
    sys.exit(1)

from generate_root_theory import QuotaExhausted
from generate_root_growing_text import (
    DEFAULT_MODELS, ROOTS_PER_CHECKPOINT, START_SENTENCES, GROWTH_PER_CHECKPOINT,
    collect_known_words, process_checkpoint, to_book_data, load_bad_word_skeletons,
)

HERE = os.path.dirname(os.path.abspath(__file__))
READER_AUDIO_SCRIPT = os.path.join(HERE, "..", "hebrew-reader", "3_generate_audio.py")


def generate_text_with_rotation(models, dead_models, known_words, checkpoint, target_sentences):
    for model in models:
        if model in dead_models:
            continue
        client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
        try:
            result = process_checkpoint(client, model, known_words, checkpoint, target_sentences)
        except QuotaExhausted:
            print(f"    модель {model}: квота исчерпана — мёртвая до конца этого прогона", file=sys.stderr)
            dead_models.add(model)
            continue
        if result is not None:
            return result, model
        print(f"    модель {model} не справилась с этим чекпоинтом — пробую следующую", file=sys.stderr)
    return None, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--theory", default="root_theory_all.json")
    ap.add_argument("--roots", default="root_frequency.json")
    ap.add_argument("--out-dir", default="growing_texts")
    ap.add_argument("--total-roots", type=int, default=200)
    ap.add_argument("--models", default=",".join(DEFAULT_MODELS))
    ap.add_argument("--limit-checkpoints", type=int, default=None, help="ограничить кол-во чекпоинтов в ЭТОМ запуске")
    args = ap.parse_args()

    if not os.environ.get("GEMINI_API_KEY"):
        print("Нужен GEMINI_API_KEY в окружении.", file=sys.stderr)
        sys.exit(1)
    models = [m.strip() for m in args.models.split(",") if m.strip()]

    with open(args.roots, encoding="utf-8") as f:
        rf = json.load(f)
    rf.sort(key=lambda r: r["position"])
    ordered_roots = [r["root"] for r in rf[: args.total_roots]]

    with open(args.theory, encoding="utf-8") as f:
        theory_list = json.load(f)
    theory_by_root = {t["root"]: t for t in theory_list}
    theory_ordered = [theory_by_root[r] for r in ordered_roots if r in theory_by_root]

    total_checkpoints = args.total_roots // ROOTS_PER_CHECKPOINT
    bad_skeletons = load_bad_word_skeletons()
    os.makedirs(args.out_dir, exist_ok=True)

    print(f"{total_checkpoints} чекпоинтов всего (корни 1-{total_checkpoints * ROOTS_PER_CHECKPOINT} из {args.total_roots}).", file=sys.stderr)

    dead_models = set()
    done_this_run = 0
    for checkpoint in range(1, total_checkpoints + 1):
        text_path = os.path.join(args.out_dir, f"checkpoint_{checkpoint:02d}.json")
        audio_json_path = os.path.join(args.out_dir, f"checkpoint_{checkpoint:02d}_audio.json")
        audio_dir = os.path.join(args.out_dir, f"audio_gt{checkpoint:02d}")

        if os.path.exists(audio_json_path):
            continue  # полностью готов с прошлого запуска

        if args.limit_checkpoints is not None and done_this_run >= args.limit_checkpoints:
            print(f"Достигнут --limit-checkpoints={args.limit_checkpoints}, останавливаюсь.", file=sys.stderr)
            break

        target_sentences = START_SENTENCES + (checkpoint - 1) * GROWTH_PER_CHECKPOINT
        print(f"[чекпоинт {checkpoint}/{total_checkpoints}] цель {target_sentences} предложений", file=sys.stderr)

        if not os.path.exists(text_path):
            if len(dead_models) >= len(models):
                print("  все модели исчерпаны для текста — останавливаюсь на сегодня.", file=sys.stderr)
                break
            up_to = checkpoint * ROOTS_PER_CHECKPOINT
            known_words = collect_known_words(theory_ordered, up_to)
            result, used = generate_text_with_rotation(models, dead_models, known_words, checkpoint, target_sentences)
            if result is None:
                print(f"  !! не удалось сгенерировать текст для чекпоинта {checkpoint} — пропускаю до следующего запуска", file=sys.stderr)
                continue
            book_data, gtts_count = to_book_data(result, checkpoint, bad_skeletons)
            with open(text_path, "w", encoding="utf-8") as f:
                json.dump(book_data, f, ensure_ascii=False, indent=2)
            print(f"  текст готов: «{book_data['title']}» (модель {used}, {gtts_count} на gTTS)", file=sys.stderr)

        print(f"  озвучиваю через пайплайн читалки...", file=sys.stderr)
        proc = subprocess.run(
            [sys.executable, READER_AUDIO_SCRIPT, text_path, audio_json_path, audio_dir],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if proc.returncode != 0:
            print(f"  !! озвучка не удалась (код {proc.returncode}):", file=sys.stderr)
            print(proc.stderr[-2000:], file=sys.stderr)
            continue
        print(f"  озвучено, см. {audio_dir}", file=sys.stderr)
        done_this_run += 1

    print(f"\nВ этом запуске полностью обработано: {done_this_run}.", file=sys.stderr)
    total_ready = sum(1 for c in range(1, total_checkpoints + 1)
                       if os.path.exists(os.path.join(args.out_dir, f"checkpoint_{c:02d}_audio.json")))
    print(f"Всего готово (текст+аудио): {total_ready}/{total_checkpoints}.", file=sys.stderr)


if __name__ == "__main__":
    main()
