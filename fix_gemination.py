"""
fix_gemination.py — чинит найденную (по вопросу пользователя про מהווה)
проблему написания: слабая корневая буква ו/י, которая по грамматике
ДОЛЖНА быть удвоена (средняя корневая в биньянах פִּעֵל/הִתְפַּעֵל/פֻּעַל/
הֻפְעַל), в данных иногда написана ОДНОЙ буквой с дагешем вместо ДВУХ
букв — напр. "מְהַוָּה" вместо "מְהַוָּוה", "הִסְתַּיֵּם" вместо
"הִסְתַּייֵּם". В כתיב מלא без никуда дагеш пропадает и слово перестаёт
быть однозначным, поэтому современная практика пишет такую букву дважды.

НЕ трогает все ו/י с дагешем подряд (это словил бы и дагеш после
артикля ה/предлога — другое явление, там как раз ничего чинить не
надо) — только ту букву, которая является СРЕДНЕЙ буквой САМОГО этого
корня (root в записи, см. find_gemination_violations в
hebrew_spelling_rules.py — сканирует ЛЮБОЕ текстовое поле записи, не
только заголовки слов, как в первой версии этого скрипта).

Для root_theory: каждое затронутое ПОЛЕ (не отдельное слово — если в
одном длинном тексте несколько нарушений, чиним всё поле одним вызовом)
чинится точечно, get/set по пути. Для root_sentences: he/focus_word/
cloze_token чинятся ВСЕ ТРИ ВМЕСТЕ, если нарушение нашлось хотя бы в
одном — т.к. cloze_token должен остаться буквальной подстрокой he.

Запуск:
    export GEMINI_API_KEY=твой_ключ
    python3 fix_gemination.py
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

from generate_root_theory import QuotaExhausted, _is_quota_error
from hebrew_spelling_rules import (
    fix_ktiv_chaser, find_ktiv_chaser_violations,
    find_homoglyph_violations as find_homoglyph_corrupted,
    find_gemination_violations, root_middle_letter,
    has_gemination, max_letter_run, split_clusters,
    is_safe_gemination_fix,
    DAGESH, VAV, YOD,
)


def get_by_path(entry, path):
    """path относителен к entry (объекту одной записи корня), может
    начинаться прямо с ключа без точки — напр. 'derived_words[0].word',
    'girzah_note.compare[0].form', 'common_mistakes'."""
    obj = entry
    for key, idx in re.findall(r"\.?([^.\[\]]+)|\[(\d+)\]", path):
        obj = obj[key] if key else obj[int(idx)]
    return obj


def set_by_path(entry, path, value):
    tokens = re.findall(r"\.?([^.\[\]]+)|\[(\d+)\]", path)
    obj = entry
    for key, idx in tokens[:-1]:
        obj = obj[key] if key else obj[int(idx)]
    lk, li = tokens[-1]
    if lk:
        obj[lk] = value
    else:
        obj[int(li)] = value

DEFAULT_MODELS = [
    "gemini-3.6-flash", "gemini-3.5-flash", "gemini-3.7-flash", "gemini-3.8-flash",
    "gemini-flash-latest", "gemini-flash-lite-latest", "gemini-3-flash-preview",
    "gemini-3.1-flash-lite", "gemini-3.5-flash-lite",
]


THEORY_SCHEMA = {"type": "string"}
SENTENCE_SCHEMA = {
    "type": "object",
    "properties": {
        "he": {"type": "string"},
        "focus_word": {"type": "string"},
        "cloze_token": {"type": "string"},
    },
    "required": ["he", "focus_word", "cloze_token"],
}

WORD_PROMPT = """Ты — эксперт по современному ивритскому правописанию (כתיב מלא).

ПРАВИЛО: если слабая корневая буква ו или י является СРЕДНЕЙ корневой
буквой и удваивается по грамматике (биньяны פִּעֵל/הִתְפַּעֵל/פֻּעַל/
הֻפְעַל — "удвоение среднего корня"), в современном полном написании
её принято писать ДВУМЯ буквами (וו/יי), а не одной буквой с дагешем.
Пример: קִיֵּם -> קִייֵּם, הִסְתַּיֵּם -> הִסְתַּייֵּם, טִיֵּל -> טִייֵּל,
מְהַוָּה -> מְהַוָּוה.

Корень этого слова: {root} (средняя буква: {middle}).
Слово/фраза: {word}

ВАЖНО: удвоенная буква уже правильно написана в ДВУХ возможных порядках
— голая буква перед огласованной (напр. י затем יֵּ, как в
לְהִתְבַּייֵּת) ИЛИ огласованная перед голой (напр. יִּ затем י, как в
חַיִּים). Если видишь любой из этих двух паттернов рядом — это уже
правильно удвоено, слово менять не нужно. НИКОГДА не добавляй третью
копию буквы поверх уже удвоенной.

ЗАДАЧА: если в этом слове СРЕДНЯЯ БУКВА КОРНЯ написана ОДНОЙ буквой с
дагешем (и НЕ примыкает к голой копии той же буквы ни с одной стороны)
— верни исправленную форму (добавь голую копию буквы рядом, только это
изменение, ничего больше не трогай). Если дагеш на этой букве по ДРУГОЙ
причине (не удвоение среднего корня — например, после артикля/предлога,
или буква вообще не относится к корню), или слово уже написано с
удвоенной буквой (см. выше), или дагеш там из-за шурука (простое "у") —
верни слово БЕЗ ИЗМЕНЕНИЙ. Если слово выглядит испорченным по ДРУГОЙ, не
относящейся к этому правилу причине — тоже верни без изменений (не твоя
задача сейчас чинить это).

Верни ТОЛЬКО само слово/фразу (без пояснений)."""

SENTENCE_PROMPT = """Ты — эксперт по современному ивритскому правописанию (כתיב מלא).

ПРАВИЛО: если слабая корневая буква ו или י является СРЕДНЕЙ корневой
буквой и удваивается по грамматике (биньяны פִּעֵל/הִתְפַּעֵל/פֻּעַל/
הֻפְעַל), в современном полном написании её принято писать ДВУМЯ буквами
(וו/יי), а не одной буквой с дагешем. Пример: קִיֵּם -> קִייֵּם,
הִסְתַּיֵּם -> הִסְתַּייֵּם.

ВАЖНО: удвоенная буква уже правильно написана в ДВУХ возможных порядках
— голая буква перед огласованной, ИЛИ огласованная перед голой (напр.
חַיִּים — уже верно удвоено). Если видишь любой из этих паттернов —
слово уже верно, ничего не трогай. НИКОГДА не добавляй третью копию
буквы поверх уже удвоенной.

Корень: {root} (средняя буква: {middle}).

Предложение (he): {he}
Слово в фокусе (focus_word): {focus_word}
Слово для пропуска (cloze_token): {cloze_token}

ЗАДАЧА: если слово(-а) этого корня в предложении/focus_word/cloze_token
написаны с одной буквой вместо удвоенной — исправь ВЕЗДЕ, где оно
встречается (в самом предложении he, и в focus_word/cloze_token), чтобы
все три поля остались согласованы (cloze_token должен по-прежнему быть
буквальной подстрокой he). Больше НИЧЕГО не меняй — ни другие слова, ни
огласовки, ни пунктуацию. Если дагеш там по другой причине или слово уже
верно — верни все три поля БЕЗ ИЗМЕНЕНИЙ.

Верни JSON-объект {{"he": ..., "focus_word": ..., "cloze_token": ...}}."""


def fix_theory_word(client, model, root, middle, word):
    prompt = WORD_PROMPT.format(root=root, middle=middle, word=word)
    try:
        response = client.models.generate_content(
            model=model, contents=prompt,
            config=types.GenerateContentConfig(response_mime_type="application/json", response_schema=THEORY_SCHEMA),
        )
        fixed = json.loads(response.text)
    except Exception as e:
        if _is_quota_error(e):
            raise QuotaExhausted(str(e)) from e
        return None
    if not isinstance(fixed, str) or not fixed.strip():
        return None
    fixed = fix_ktiv_chaser(fixed)
    if find_homoglyph_corrupted({"x": fixed}):
        return None
    if fixed == word:
        return fixed
    if not is_safe_gemination_fix(word, fixed, middle):
        return None
    if max_letter_run(fixed, middle) >= 3:  # переисправление — 3+ буквы подряд
        return None
    return fixed


def fix_sentence(client, model, root, middle, s):
    prompt = SENTENCE_PROMPT.format(
        root=root, middle=middle, he=s["he"],
        focus_word=s.get("focus_word", ""), cloze_token=s.get("cloze_token", ""),
    )
    try:
        response = client.models.generate_content(
            model=model, contents=prompt,
            config=types.GenerateContentConfig(response_mime_type="application/json", response_schema=SENTENCE_SCHEMA),
        )
        fixed = json.loads(response.text)
    except Exception as e:
        if _is_quota_error(e):
            raise QuotaExhausted(str(e)) from e
        return None
    if not all(k in fixed for k in ("he", "focus_word", "cloze_token")):
        return None
    fixed = fix_ktiv_chaser(fixed)
    if find_homoglyph_corrupted(fixed):
        return None
    if fixed["cloze_token"] and fixed["cloze_token"] not in fixed["he"]:
        return None
    if fixed["he"] != s["he"] and not is_safe_gemination_fix(s["he"], fixed["he"], middle):
        return None
    if fixed["focus_word"] != s.get("focus_word", "") and not is_safe_gemination_fix(s.get("focus_word", ""), fixed["focus_word"], middle):
        return None
    if fixed["cloze_token"] != s.get("cloze_token", "") and not is_safe_gemination_fix(s.get("cloze_token", ""), fixed["cloze_token"], middle):
        return None
    if max_letter_run(fixed["he"], middle) >= 3:  # переисправление
        return None
    return fixed


VERIFIED_OK_PATH = "gemination_verified_ok.json"


def load_verified_ok():
    """Кэш (root, path/idx)-ключей, для которых модель уже сказала "не
    надо чинить" — has_gemination сканирует ВСЁ текстовое поле целиком,
    а не только слово этого конкретного корня, поэтому регулярно ловит
    случайные слова ДРУГИХ корней с дагешем по совсем другой причине
    (напр. дагеш хазак после артикля ה: "הַיֶּלֶד" не имеет отношения к
    корню ה-י-ה, хотя формально совпадает по паттерну). Без этого кэша
    один и тот же ложный кандидат переспрашивается у модели в КАЖДОМ
    прогоне, впустую тратя суточную квоту."""
    try:
        return set(tuple(x) for x in json.load(open(VERIFIED_OK_PATH, encoding="utf-8")))
    except FileNotFoundError:
        return set()


def save_verified_ok(verified):
    json.dump(sorted(list(x) for x in verified), open(VERIFIED_OK_PATH, "w", encoding="utf-8"), ensure_ascii=False, indent=2)


def with_rotation(fn, args_list, client_factory, dead_models, max_rounds=4):
    """Общая обёртка вращения моделей с частичным успехом, как в
    fix_arabic_contamination.py — но fn применяется к ОДНОМУ элементу за
    раз (эти вызовы недостаточно похожи, чтобы безопасно батчевать: у
    каждого свой JSON-объект/схема, не просто строка)."""
    results = {}
    remaining = list(range(len(args_list)))
    for _ in range(max_rounds):
        if not remaining:
            break
        still = []
        for i in remaining:
            done = False
            for model in DEFAULT_MODELS:
                if model in dead_models:
                    continue
                client = client_factory()
                try:
                    r = fn(client, model, *args_list[i])
                except QuotaExhausted:
                    dead_models.add(model)
                    continue
                if r is not None:
                    results[i] = r
                    done = True
                    break
            if not done:
                still.append(i)
        remaining = still
    return results


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-theory", action="store_true", help="не трогать root_theory_all.json — вся квота этого прогона достаётся предложениям")
    ap.add_argument("--skip-sentences", action="store_true", help="не трогать root_sentences_all.json")
    args = ap.parse_args()

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("Нужен GEMINI_API_KEY в окружении.", file=sys.stderr)
        sys.exit(1)
    client_factory = lambda: genai.Client(api_key=api_key)
    dead_models = set()

    theory = json.load(open("root_theory_all.json", encoding="utf-8"))
    sentences = json.load(open("root_sentences_all.json", encoding="utf-8"))
    verified_ok = load_verified_ok()

    # ---- theory: ЛЮБОЕ текстовое поле записи (не только заголовки слов
    # и set_phrases, как раньше) — find_gemination_violations сканирует
    # всю запись рекурсивно. Дедуп по ПУТИ: если в одном длинном тексте
    # (напр. common_mistakes) несколько нарушений, чиним всё поле целиком
    # одним вызовом, а не слово за словом.
    if not args.skip_theory:
        theory_targets = []  # (root, middle, entry, path)
        skipped_cached = 0
        for e in theory:
            middle = root_middle_letter(e["root"])
            if not middle:
                continue
            seen_paths = set()
            for path, _word in find_gemination_violations(e, e["root"]):
                if path in seen_paths:
                    continue
                seen_paths.add(path)
                if ("theory", e["root"], path) in verified_ok:
                    skipped_cached += 1
                    continue
                theory_targets.append((e["root"], middle, e, path))

        print(f"Теория: {len(theory_targets)} кандидатов ({skipped_cached} пропущено — уже подтверждены как не-нарушения)", file=sys.stderr)
        args_list = [(root, middle, get_by_path(entry, path)) for root, middle, entry, path in theory_targets]
        results = with_rotation(fix_theory_word, args_list, client_factory, dead_models)
        fixed_count = 0
        for i, fixed in results.items():
            root, middle, entry, path = theory_targets[i]
            original = get_by_path(entry, path)
            if fixed != original:
                print(f"  {root}: {original!r} -> {fixed!r}", file=sys.stderr)
                fixed_count += 1
            else:
                verified_ok.add(("theory", root, path))
            set_by_path(entry, path, fixed)
        print(f"Теория готово: {len(results)}/{len(theory_targets)} обработано, {fixed_count} реально изменено", file=sys.stderr)
        json.dump(theory, open("root_theory_all.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        save_verified_ok(verified_ok)
    else:
        print("Теория пропущена (--skip-theory)", file=sys.stderr)

    if args.skip_sentences:
        print("Предложения пропущены (--skip-sentences)", file=sys.stderr)
        return

    # ---- sentences: he/focus_word/cloze_token — по ЛЮБОМУ из трёх,
    # дедуп по индексу предложения (чиним все три поля вместе)
    sent_targets = []
    skipped_cached2 = 0
    for entry in sentences:
        middle = root_middle_letter(entry["root"])
        if not middle:
            continue
        seen_idx = set()
        for path, _word in find_gemination_violations(entry, entry["root"]):
            m = re.match(r"sentences\[(\d+)\]", path)
            if not m:
                continue
            idx = int(m.group(1))
            if idx in seen_idx:
                continue
            seen_idx.add(idx)
            if ("sentence", entry["root"], idx) in verified_ok:
                skipped_cached2 += 1
                continue
            sent_targets.append((entry["root"], middle, entry["sentences"][idx], idx))

    print(f"\nПредложения: {len(sent_targets)} кандидатов ({skipped_cached2} пропущено — уже подтверждены как не-нарушения)", file=sys.stderr)
    args_list2 = [(root, middle, s) for root, middle, s, _idx in sent_targets]
    results2 = with_rotation(fix_sentence, args_list2, client_factory, dead_models)
    fixed_count2 = 0
    for i, fixed in results2.items():
        root, middle, s, idx = sent_targets[i]
        if fixed["he"] != s["he"] or fixed["focus_word"] != s.get("focus_word") or fixed["cloze_token"] != s.get("cloze_token"):
            print(f"  {root}: {s['he']!r} -> {fixed['he']!r}", file=sys.stderr)
            fixed_count2 += 1
        else:
            verified_ok.add(("sentence", root, idx))
        s["he"] = fixed["he"]
        s["focus_word"] = fixed["focus_word"]
        s["cloze_token"] = fixed["cloze_token"]
    print(f"Предложения готово: {len(results2)}/{len(sent_targets)} обработано, {fixed_count2} реально изменено", file=sys.stderr)
    json.dump(sentences, open("root_sentences_all.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    save_verified_ok(verified_ok)


if __name__ == "__main__":
    main()
