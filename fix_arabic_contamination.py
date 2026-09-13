"""
fix_arabic_contamination.py — чинит найденную порчу данных курса "Корни":
модель иногда подставляла букву/огласовку из ДРУГОГО алфавита вместо
похожей на вид ивритской (реже — вместо кириллической в русском слове)
прямо посреди слова. Начиналось как "арабский баг" (напр. "בְּטֶקَس" вместо
"בְּטֶקֶס"), но при более широком сканировании нашлись те же подстановки из
латиницы (ל→L, ת→T, ן→N — самые частые), а также единичные случаи
армянского/тайского/гуджарати/полноширинных CJK-форм — везде один и тот
же механизм: обнаружено при разборе отзыва пользователя про дубли в
derived_words корня ה-י-ה (не связано с той правкой напрямую — отдельный,
более старый баг генерации).

Правит ТОЛЬКО конкретные заражённые строки (батчами, с контекстом), не
трогая остальной текст записи — в отличие от regenerate_derived_words.py,
который перегенерирует целое поле.

Запуск:
    export GEMINI_API_KEY=твой_ключ
    python3 fix_arabic_contamination.py
"""

import json
import os
import re
import sys
import time

try:
    from google import genai
    from google.genai import types
except ImportError:
    print("Нужно: pip install google-genai --break-system-packages", file=sys.stderr)
    sys.exit(1)

from generate_root_theory import QuotaExhausted, _is_quota_error, fix_ktiv_chaser, find_ktiv_chaser_violations

DEFAULT_MODELS = [
    "gemini-3.6-flash", "gemini-3.5-flash", "gemini-3.7-flash", "gemini-3.8-flash",
    "gemini-flash-latest", "gemini-flash-lite-latest", "gemini-3-flash-preview",
    "gemini-3.1-flash-lite", "gemini-3.5-flash-lite",
]

BATCH_SIZE = 20

FIX_SCHEMA = {
    "type": "array",
    "items": {"type": "string"},
}

PROMPT_TEMPLATE = """В приведённых ниже фрагментах текста (русский + иврит вперемешку, из
учебного курса иврита) есть известный баг генерации: модель иногда по
ошибке подставляла букву или огласовку из ДРУГОГО алфавита (чаще всего
латиница, но встречаются и другие) вместо похожей на вид ИВРИТСКОЙ
буквы/огласовки — например "אֶT" вместо "אֶת", "כָּL" вместо "כָּל",
"בְּטֶקَس" вместо "בְּטֶקֶס" (тут арабская буква), "مнение" вместо "мнение"
(тут в русском слове).

ЗАДАЧА: верни те же фрагменты, но с исправленной ошибкой — замени
посторонний символ(ы) на то, что явно было задумано (ивритская буква с
правильной огласовкой в СТРОГО полном написании כתיב מלא, или
кириллическая буква, если ошибка в русском слове). НЕ меняй больше НИЧЕГО
в тексте — ни слова, ни смысл, ни остальные огласовки, ни пунктуацию.
Если сомневаешься в точной огласовке испорченного ивритского слова —
восстанови её по смыслу фразы и стандартному современному написанию
этого слова.

Верни JSON-массив строк той же длины и в том же порядке, что вход —
i-й элемент выхода это исправленный i-й фрагмент.

Фрагменты (каждый на отдельной строке, пронумерован):
{items}
"""

# Правило "чужой алфавит внутри слова" (is_corrupted_word/find_corrupted)
# теперь живёт в hebrew_spelling_rules.py вместе с двумя другими
# правилами честности проекта — импортируем отсюда вместо дублирования;
# find_homoglyph_violations уже возвращает (path, string), тот же формат,
# что был у find_corrupted здесь.
from hebrew_spelling_rules import is_corrupted_word, is_corrupted, find_homoglyph_violations as find_corrupted  # noqa: E402


def get_by_path(root, path):
    obj = root
    for part in re.findall(r"\.([^.\[\]]+)|\[(\d+)\]", path):
        key, idx = part
        obj = obj[key] if key else obj[int(idx)]
    return obj


def set_by_path(root, path, value):
    tokens = re.findall(r"\.([^.\[\]]+)|\[(\d+)\]", path)
    obj = root
    for key, idx in tokens[:-1]:
        obj = obj[key] if key else obj[int(idx)]
    lk, li = tokens[-1]
    if lk:
        obj[lk] = value
    else:
        obj[int(li)] = value


def still_has_bad_arabic(s):
    return any(is_corrupted_word(w) for w in re.split(r"\s+", s))


def sanity_check(original, fixed):
    """Грубая защита от переписывания вместо точечного фикса: длина не
    должна отличаться больше чем на треть, и большая часть непроблемных
    слов должна остаться на месте."""
    if not fixed or not isinstance(fixed, str):
        return False
    if abs(len(fixed) - len(original)) > max(10, len(original) * 0.35):
        return False
    orig_words = set(w for w in re.split(r"\s+", original) if not is_corrupted_word(w))
    fixed_words = set(re.split(r"\s+", fixed))
    if not orig_words:
        return True
    overlap = len(orig_words & fixed_words) / len(orig_words)
    return overlap >= 0.6


def attempt_fix_once(client, model, items):
    """Один вызов модели на весь items. Возвращает список той же длины:
    исправленная строка на позициях, прошедших обе проверки, None на
    позициях, которые остались плохими (и достойны повторной попытки на
    другой модели/раунде) — НЕ всё-или-ничего для батча в целом, чтобы
    один упрямый элемент не топил остальные 19 хороших исправлений."""
    numbered = "\n".join(f"{i}: {s}" for i, s in enumerate(items))
    prompt = PROMPT_TEMPLATE.format(items=numbered)
    try:
        response = client.models.generate_content(
            model=model,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=FIX_SCHEMA,
            ),
        )
        fixed_list = json.loads(response.text)
    except Exception as e:
        if _is_quota_error(e):
            raise QuotaExhausted(str(e)) from e
        print(f"    вызов не удался ({e})", file=sys.stderr)
        return None

    if len(fixed_list) != len(items):
        print(f"    длина не совпала ({len(fixed_list)} vs {len(items)}) — весь ответ отбрасываю", file=sys.stderr)
        return None

    fixed_list = [fix_ktiv_chaser(s) for s in fixed_list]
    out = []
    n_bad = 0
    for orig, fx in zip(items, fixed_list):
        if still_has_bad_arabic(fx) or not sanity_check(orig, fx):
            out.append(None)
            n_bad += 1
        else:
            out.append(fx)
    if n_bad and os.environ.get("FIX_DEBUG"):
        for orig, fx in zip(items, fixed_list):
            if still_has_bad_arabic(fx) or not sanity_check(orig, fx):
                reason = "still_arabic" if still_has_bad_arabic(fx) else "sanity_check"
                print(f"      [{reason}] orig={orig!r} fixed={fx!r}", file=sys.stderr)
    if n_bad:
        print(f"    {n_bad}/{len(items)} не прошли проверку (остальные приняты)", file=sys.stderr)
    return out


def fix_with_rotation(items, client_factory, dead_models, max_rounds=4):
    """Чинит items (список строк), вращая модели и принимая частичные
    успехи — то, что не получилось в одном раунде на одной модели, идёт
    в следующий раунд/модель меньшим подмножеством, а не топит весь батч."""
    remaining = list(range(len(items)))
    results = {}
    for round_num in range(1, max_rounds + 1):
        if not remaining:
            break
        for model in DEFAULT_MODELS:
            if model in dead_models or not remaining:
                continue
            client = client_factory()
            sub_items = [items[i] for i in remaining]
            try:
                fixed_per_item = attempt_fix_once(client, model, sub_items)
            except QuotaExhausted:
                print(f"    модель {model}: квота исчерпана", file=sys.stderr)
                dead_models.add(model)
                continue
            if fixed_per_item is None:
                continue
            still_bad = []
            for local_i, orig_i in enumerate(remaining):
                fx = fixed_per_item[local_i]
                if fx is not None:
                    results[orig_i] = fx
                else:
                    still_bad.append(orig_i)
            remaining = still_bad
    return results


def process_file(fname, client_factory, dead_models):
    path_full = f"E:/ivrit/{fname}"
    data = json.load(open(path_full, encoding="utf-8"))
    corrupted = find_corrupted(data)
    print(f"\n=== {fname}: {len(corrupted)} заражённых строк ===", file=sys.stderr)
    if not corrupted:
        return 0, 0

    paths = [p for p, _ in corrupted]
    items = [s for _, s in corrupted]

    all_results = {}
    for batch_start in range(0, len(items), BATCH_SIZE):
        batch_items = items[batch_start:batch_start + BATCH_SIZE]
        print(f"  батч {batch_start // BATCH_SIZE + 1} ({len(batch_items)} шт.)...", file=sys.stderr)
        batch_results = fix_with_rotation(batch_items, client_factory, dead_models)
        for local_idx, fixed_str in batch_results.items():
            all_results[batch_start + local_idx] = fixed_str

    for idx, fixed_str in all_results.items():
        set_by_path(data, paths[idx], fixed_str)
    results = all_results

    with open(path_full, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"  сохранено, исправлено {len(results)}/{len(corrupted)}", file=sys.stderr)
    return len(results), len(corrupted)


def main():
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("Нужен GEMINI_API_KEY в окружении.", file=sys.stderr)
        sys.exit(1)

    dead_models = set()
    client_factory = lambda: genai.Client(api_key=api_key)

    total_fixed, total_found = 0, 0
    for fname in ["root_theory_all.json", "root_sentences_all.json"]:
        fixed, found = process_file(fname, client_factory, dead_models)
        total_fixed += fixed
        total_found += found

    print(f"\nВсего исправлено: {total_fixed}/{total_found}", file=sys.stderr)


if __name__ == "__main__":
    main()
