"""
audit_corpus.py — единая точка входа для ретроактивной проверки ВСЕГО
существующего контента тренажёра, а не только того, что затронул
последний прогон генерации.

Зачем отдельно от hebrew_spelling_rules.py::_main(): при разборе бага с
792 оторванными приставками в читалке (см. E:\\hebrew-reader) выяснилось,
что merge_prefix_fragments() там ловил только КНИГИ, СГЕНЕРИРОВАННЫЕ
ПОСЛЕ появления фиксера — сам корпус, готовый ДО этого, ретроактивно не
проверялся никогда. Тот же риск есть и здесь: если добавить новое
правило в hebrew_spelling_rules.py, но не прогнать его руками против
уже готового корпуса, старые нарушения останутся незамеченными. Этот
скрипт — постоянный, переиспользуемый способ закрыть именно этот
пробел: гоняет ВСЕ правила против ВСЕГО, что есть, и его можно (нужно)
перезапускать вручную каждый раз, когда в hebrew_spelling_rules.py
добавляется новое правило.

Запуск: python audit_corpus.py
Ненулевой exit code, если найдено хоть одно неподтверждённое нарушение.

Запуск python audit_corpus.py --dict — дополнительно словарная проверка
полного написания (Hspell, см. hebrew_spelling_rules.py, правило 1в): слова,
которых нет в словаре, но есть вариант с одним лишним/недостающим י или ו.
Отдельный режим, пока в корпусе есть такие слова; отчёт пишется в
_dict/audit_report.json.
"""

import glob
import json
import os
import re
import sys

import hebrew_spelling_rules as rules

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_PATH = os.path.join(HERE, "spelling_verified_ok.json")
# Старый кэш конкретно для gemination (fix_gemination.py) — формат и
# потребитель не трогаю (см. план: риск сломать рабочий скрипт не
# оправдан), но здесь тоже учитываю, чтобы не дублировать уже
# подтверждённый моделью шум как "новые" находки.
LEGACY_GEMINATION_CACHE_PATH = os.path.join(HERE, "gemination_verified_ok.json")


def _load_legacy_gemination_cache():
    if not os.path.exists(LEGACY_GEMINATION_CACHE_PATH):
        return set()
    return set(
        tuple(x) for x in json.load(open(LEGACY_GEMINATION_CACHE_PATH, encoding="utf-8"))
    )


def _report(cache, rule, path, word, extra, total, confirmed_noise):
    if rules.is_verified(cache, rule, path, word):
        return total, confirmed_noise + 1
    print(f"  {rule} · {path} · {word!r}{extra}", file=sys.stderr)
    return total + 1, confirmed_noise


def audit_root_theory(cache, legacy_gemination, total, confirmed_noise):
    p = os.path.join(HERE, "root_theory_all.json")
    if not os.path.exists(p):
        return total, confirmed_noise
    print("\n=== root_theory_all.json ===", file=sys.stderr)
    theory = json.load(open(p, encoding="utf-8"))
    for entry in theory:
        root = entry.get("root")
        for item in rules.find_all_violations(entry, root=root):
            if item["rule"] == "gemination" and ("theory", root, item["path"]) in legacy_gemination:
                confirmed_noise += 1
                continue
            extra = f" ({item['reason']})" if item.get("reason") else ""
            key_path = f"[{root}] {item['path']}"
            total, confirmed_noise = _report(
                cache, item["rule"], key_path, item["word"], extra, total, confirmed_noise
            )
    return total, confirmed_noise


def audit_root_sentences(cache, legacy_gemination, total, confirmed_noise):
    p = os.path.join(HERE, "root_sentences_all.json")
    if not os.path.exists(p):
        return total, confirmed_noise
    print("\n=== root_sentences_all.json ===", file=sys.stderr)
    sentences = json.load(open(p, encoding="utf-8"))
    for entry in sentences:
        root = entry.get("root")
        for item in rules.find_all_violations(entry, root=root):
            if item["rule"] == "gemination":
                m = re.match(r"sentences\[(\d+)\]", item["path"])
                idx = int(m.group(1)) if m else None
                if idx is not None and ("sentence", root, idx) in legacy_gemination:
                    confirmed_noise += 1
                    continue
            extra = f" ({item['reason']})" if item.get("reason") else ""
            key_path = f"[{root}] {item['path']}"
            total, confirmed_noise = _report(
                cache, item["rule"], key_path, item["word"], extra, total, confirmed_noise
            )
        for path, token, he in rules.find_cloze_mismatch_violations(entry, path=f"[{root}]"):
            total, confirmed_noise = _report(
                cache, "cloze_mismatch", path, token, f" (не найден в {he!r})", total, confirmed_noise
            )
    return total, confirmed_noise


def audit_growing_texts(cache, total, confirmed_noise):
    files = sorted(glob.glob(os.path.join(HERE, "growing_texts", "checkpoint_*.json")))
    files = [f for f in files if not re.search(r"_audio\.json$", f)]
    if not files:
        return total, confirmed_noise
    print("\n=== growing_texts/checkpoint_*.json ===", file=sys.stderr)
    for f in files:
        name = os.path.basename(f)
        entry = json.load(open(f, encoding="utf-8"))
        for item in rules.find_all_violations(entry, root=None):
            extra = f" ({item['reason']})" if item.get("reason") else ""
            key_path = f"[{name}] {item['path']}"
            total, confirmed_noise = _report(
                cache, item["rule"], key_path, item["word"], extra, total, confirmed_noise
            )
    return total, confirmed_noise


def _iter_corpus_entries():
    """(метка, объект) для всех файлов курса: теория, предложения, растущие тексты."""
    p = os.path.join(HERE, "root_theory_all.json")
    if os.path.exists(p):
        for entry in json.load(open(p, encoding="utf-8")):
            yield f"theory[{entry.get('root')}]", entry
    p = os.path.join(HERE, "root_sentences_all.json")
    if os.path.exists(p):
        for entry in json.load(open(p, encoding="utf-8")):
            yield f"sentences[{entry.get('root')}]", entry
    for f in sorted(glob.glob(os.path.join(HERE, "growing_texts", "checkpoint_??.json"))):
        yield os.path.basename(f), json.load(open(f, encoding="utf-8"))


def audit_dictionary():
    words = rules.load_dictionary()
    if not words:
        print("Словарь Hspell недоступен (нет сети и нет _dict/he_IL.dic).", file=sys.stderr)
        return 2
    import collections
    occurrences = collections.Counter()
    locations = {}
    for label, entry in _iter_corpus_entries():
        for path, token, reason, cands in rules.find_dictionary_spelling_violations(entry, label, words=words):
            occurrences[token] += 1
            locations.setdefault(token, path)
    auto = review = 0
    auto_occ = review_occ = 0
    report = []
    for token, n in occurrences.most_common():
        fix = rules.suggest_dictionary_fix(token, words)
        kind = fix[0] if fix else "review"
        value = fix[1] if fix else []
        if kind == "auto":
            auto += 1
            auto_occ += n
        else:
            review += 1
            review_occ += n
        report.append({"word": token, "count": n, "kind": kind, "suggest": value, "where": locations[token]})
    os.makedirs(rules.DICT_DIR, exist_ok=True)
    out = os.path.join(rules.DICT_DIR, "audit_report.json")
    json.dump(report, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"Словарная проверка: {len(occurrences)} слов, {sum(occurrences.values())} вхождений", file=sys.stderr)
    print(f"  можно исправить механически: {auto} слов ({auto_occ} вхождений)", file=sys.stderr)
    print(f"  нужна ручная проверка:       {review} слов ({review_occ} вхождений)", file=sys.stderr)
    print(f"  отчёт: {out}", file=sys.stderr)
    return 1 if occurrences else 0


def main():
    if "--dict" in sys.argv:
        sys.exit(audit_dictionary())
    cache = rules.load_verified_cache(CACHE_PATH)
    legacy_gemination = _load_legacy_gemination_cache()
    total = 0
    confirmed_noise = 0
    total, confirmed_noise = audit_root_theory(cache, legacy_gemination, total, confirmed_noise)
    total, confirmed_noise = audit_root_sentences(cache, legacy_gemination, total, confirmed_noise)
    total, confirmed_noise = audit_growing_texts(cache, total, confirmed_noise)

    print(
        f"\nВсего нарушений: {total} "
        f"(плюс {confirmed_noise} подтверждённых ранее не-нарушений, исключены)",
        file=sys.stderr,
    )
    sys.exit(1 if total else 0)


if __name__ == "__main__":
    main()
