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
корня (root в записи), и только:
  - заголовок слова + example'ы в derived_words
  - phrase_he в set_phrases
  - he + focus_word + cloze_token в root_sentences (все три вместе,
    т.к. cloze_token должен остаться буквальной подстрокой he)

Запуск:
    export GEMINI_API_KEY=твой_ключ
    python3 fix_gemination.py
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

from generate_root_theory import QuotaExhausted, _is_quota_error, fix_ktiv_chaser, find_ktiv_chaser_violations
from fix_arabic_contamination import find_corrupted as find_homoglyph_corrupted

DEFAULT_MODELS = [
    "gemini-3.6-flash", "gemini-3.5-flash", "gemini-3.7-flash", "gemini-3.8-flash",
    "gemini-flash-latest", "gemini-flash-lite-latest", "gemini-3-flash-preview",
    "gemini-3.1-flash-lite", "gemini-3.5-flash-lite",
]

DAGESH = "ּ"
VAV = "ו"
YOD = "י"
_STRIP = ".,!?;:\"'()«»־-"


def split_clusters(word):
    clusters = []
    for ch in word:
        if clusters and unicodedata.category(ch) == "Mn":
            clusters[-1] += ch
        else:
            clusters.append(ch)
    return clusters


def has_gemination(word, middle):
    """True, если средняя буква корня (middle) стоит с дагешем+огласовкой
    и НЕ примыкает к голой копии той же буквы ни с одной стороны — то
    есть ещё не удвоена. Уже верно удвоенные слова кластеризуются двумя
    способами (оба реально встречаются): голая буква ПЕРЕД дагешированной
    (לְהִתְבַּייֵּת — י, затем יֵּ) или дагешированная ПЕРЕД голой
    (חַיִּים — יִּ, затем י) — проверяем обе стороны, иначе уже
    правильные слова ложно считаются "надо чинить" и от повторного
    прогона на них модель может добавить ТРЕТЬЮ букву поверх верной
    формы (реальный баг, пойманный на первом прогоне — см. FEEDBACK_LOG.md)."""
    clean = word.strip(_STRIP)
    clusters = split_clusters(clean)
    for i, cl in enumerate(clusters):
        if cl[0] == middle and DAGESH in cl[1:] and len(cl) > 2:
            prev_bare = i > 0 and clusters[i - 1] == middle
            next_bare = i < len(clusters) - 1 and clusters[i + 1] == middle
            if prev_bare or next_bare:
                continue
            return True
    return False


def max_letter_run(word, middle):
    """Максимальное число ПОДРЯД идущих кластеров с базовой буквой middle
    (независимо от огласовок) — 3+ значит слово переудвоено (баг, не
    цель); используется как жёсткая защита от переисправления в
    attempt_fix, а не только эвристика для поиска кандидатов."""
    clean = word.strip(_STRIP)
    run = maxrun = 0
    for cl in split_clusters(clean):
        if cl[0] == middle:
            run += 1
            maxrun = max(maxrun, run)
        else:
            run = 0
    return maxrun


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
    if abs(len(fixed) - len(word)) > max(3, len(word) * 0.3):
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
    if abs(len(fixed["he"]) - len(s["he"])) > max(5, len(s["he"]) * 0.3):
        return None
    if max_letter_run(fixed["he"], middle) >= 3:  # переисправление
        return None
    return fixed


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
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("Нужен GEMINI_API_KEY в окружении.", file=sys.stderr)
        sys.exit(1)
    client_factory = lambda: genai.Client(api_key=api_key)
    dead_models = set()

    theory = json.load(open("root_theory_all.json", encoding="utf-8"))
    sentences = json.load(open("root_sentences_all.json", encoding="utf-8"))

    by_root_middle = {}
    for e in theory:
        letters = e["root"].split("-")
        if len(letters) == 3 and letters[1] in (VAV, YOD):
            by_root_middle[e["root"]] = letters[1]

    # ---- theory: derived_words headwords + set_phrases ----
    theory_targets = []  # (root, middle, setter)
    for e in theory:
        middle = by_root_middle.get(e["root"])
        if not middle:
            continue
        for w in e["derived_words"]:
            if has_gemination(w["word"], middle):
                theory_targets.append((e["root"], middle, w, "word"))
        for p in e["set_phrases"]:
            if has_gemination(p["phrase_he"], middle):
                theory_targets.append((e["root"], middle, p, "phrase_he"))

    print(f"Теория: {len(theory_targets)} кандидатов", file=sys.stderr)
    args_list = [(root, middle, obj[key]) for root, middle, obj, key in theory_targets]
    results = with_rotation(fix_theory_word, args_list, client_factory, dead_models)
    fixed_count = 0
    for i, fixed in results.items():
        root, middle, obj, key = theory_targets[i]
        if fixed != obj[key]:
            print(f"  {root}: {obj[key]!r} -> {fixed!r}", file=sys.stderr)
            fixed_count += 1
        obj[key] = fixed
    print(f"Теория готово: {len(results)}/{len(theory_targets)} обработано, {fixed_count} реально изменено", file=sys.stderr)
    json.dump(theory, open("root_theory_all.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    # ---- sentences: focus_word/cloze_token-flagged sentences ----
    sent_targets = []
    for entry in sentences:
        middle = by_root_middle.get(entry["root"])
        if not middle:
            continue
        for s in entry["sentences"]:
            fw, ct = s.get("focus_word", ""), s.get("cloze_token", "")
            if (fw and has_gemination(fw, middle)) or (ct and has_gemination(ct, middle)):
                sent_targets.append((entry["root"], middle, s))

    print(f"\nПредложения: {len(sent_targets)} кандидатов", file=sys.stderr)
    args_list2 = [(root, middle, s) for root, middle, s in sent_targets]
    results2 = with_rotation(fix_sentence, args_list2, client_factory, dead_models)
    fixed_count2 = 0
    for i, fixed in results2.items():
        root, middle, s = sent_targets[i]
        if fixed["he"] != s["he"] or fixed["focus_word"] != s.get("focus_word") or fixed["cloze_token"] != s.get("cloze_token"):
            print(f"  {root}: {s['he']!r} -> {fixed['he']!r}", file=sys.stderr)
            fixed_count2 += 1
        s["he"] = fixed["he"]
        s["focus_word"] = fixed["focus_word"]
        s["cloze_token"] = fixed["cloze_token"]
    print(f"Предложения готово: {len(results2)}/{len(sent_targets)} обработано, {fixed_count2} реально изменено", file=sys.stderr)
    json.dump(sentences, open("root_sentences_all.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
