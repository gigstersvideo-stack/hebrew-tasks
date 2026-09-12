"""
generate_root_theory.py — прототип генерации "теории" для курса "Корни"
(см. ROOTS_CURRICULUM.md, раздел "Предлагаемая структура теории"). Пока
пробный прогон на нескольких корнях для оценки качества/структуры перед
тем, как ставить на поток все 227.

Структура ответа на корень (см. RESPONSE_SCHEMA): корень + интуиция
(явно как подсказка, не правило), 2-4 РЕАЛЬНЫХ частотных производных
слова (только те биньяны, что реально употребимы для этого корня — не
гипотетическая полная таблица), честная пометка расхождений (когда
производное значит не то, что подсказывает интуиция — см. Arad 2005 в
ROOTS_CURRICULUM.md), опциональная мнемоника, переходная фраза к практике.

Запуск:
    export GEMINI_API_KEY=твой_ключ
    python3 generate_root_theory.py theory_test_roots.json --out theory_test_output.json
"""

import argparse
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


RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "root": {"type": "string"},
        "semantic_field": {
            "type": "string",
            "description": (
                "Абзац (3-5 предложений) о ядре значения корня и его семантическом "
                "поле — с какими идеями/образами/областями он связан, явно "
                "хеджировано как интуиция-подсказка ('вокруг идеи...', 'что-то в "
                "районе...'), НЕ строгое правило — у ивритских корней значение "
                "часто расходится по биньянам."
            ),
        },
        "origin_note": {
            "type": "string",
            "description": (
                "Коротко (1-2 предложения) о происхождении корня, ТОЛЬКО если это "
                "реально помогает понять/запомнить значение. Если неясно/спорно "
                "или не добавляет пользы — пустая строка, не выдумывай красивую "
                "этимологию."
            ),
        },
        "girzah_note": {
            "type": "object",
            "description": (
                "Тип корня по традиционной классификации и, если он "
                "'слабый', конкретное сравнение форм — что именно отличается "
                "от обычного שלמים-корня."
            ),
            "properties": {
                "type_label": {
                    "type": "string",
                    "description": "Название типа: 'שלמים (обычный)' если без особенностей, иначе тип слабого корня, напр. 'ע\"ו (полый корень)'.",
                },
                "compare": {
                    "type": "array",
                    "description": (
                        "Если корень שלמים (обычный, без особенностей) — ПУСТОЙ массив, не изобретай "
                        "сравнение на пустом месте. Если слабый — 1-2 пары {биньян+время, "
                        "форма, что именно нестандартно}, показывающие КОНКРЕТНО разницу с "
                        "обычным спряжением (напр. Пааль-прошедшее קָם, где средний радикал "
                        "выпал)."
                    ),
                    "items": {
                        "type": "object",
                        "properties": {
                            "binyan": {"type": "string", "description": "напр. 'פָּעַל · прошедшее'"},
                            "form": {"type": "string", "description": "сама форма с огласовками"},
                            "note": {"type": "string", "description": "коротко, в чём нестандартность этой формы"},
                        },
                        "required": ["binyan", "form", "note"],
                    },
                },
                "text": {
                    "type": "string",
                    "description": (
                        "1-3 предложения прозой: КОНКРЕТНО, в чём спряжение/словообразование "
                        "отличается от обычного שלמים-корня (если слабый), что важно знать, "
                        "чтобы не переносить обычный паттерн по ошибке. Если корень שלמים — "
                        "коротко так и напиши, не изобретай сложность на пустом месте."
                    ),
                },
            },
            "required": ["type_label", "compare", "text"],
        },
        "fun_fact": {
            "type": "string",
            "description": (
                "Одна живая, ИСТОРИЧЕСКАЯ/традиционная деталь про этот корень — "
                "библейская отсылка, роль в традиции, старая идиома с историей. "
                "Пусто, если реально нечего сказать — не натягивай."
            ),
        },
        "modern_usage_note": {
            "type": "object",
            "description": (
                "Как этот корень живёт в СОВРЕМЕННОМ разговорном иврите/сленге/"
                "интернете сегодня — отдельно от fun_fact (тот про историю, это "
                "про сейчас)."
            ),
            "properties": {
                "text": {
                    "type": "string",
                    "description": "Пусто, если ничего особенного в современном употреблении нет — не натягивай.",
                },
                "example_he": {
                    "type": "string",
                    "description": "Естественное предложение-пример с огласовками, иллюстрирующее text. Пусто, если text пуст.",
                },
                "example_ru": {"type": "string", "description": "Перевод example_he. Пусто, если text пуст."},
            },
            "required": ["text", "example_he", "example_ru"],
        },
        "place_or_name_note": {
            "type": "object",
            "description": (
                "Реальный, узнаваемый топоним, название улицы/района или личное "
                "имя, построенное на этом корне — заземляет лингвистику в то, "
                "что реально видно на вывесках в Израиле."
            ),
            "properties": {
                "text": {
                    "type": "string",
                    "description": "Пусто, если такого узнаваемого примера нет — не натягивай притянутый.",
                },
                "example_he": {
                    "type": "string",
                    "description": "Естественное предложение-пример с огласовками, упоминающее это название. Пусто, если text пуст.",
                },
                "example_ru": {"type": "string", "description": "Перевод example_he. Пусто, если text пуст."},
            },
            "required": ["text", "example_he", "example_ru"],
        },
        "common_collocation": {
            "type": "string",
            "description": (
                "Слово или короткая фраза, с которой производное этого корня "
                "чаще всего стоит рядом в реальной речи (не обязательно "
                "устойчивая идиома, просто частая пара) — с примером. Пусто, "
                "если ничего особенно частого нет."
            ),
        },
        "song_or_quote_note": {
            "type": "string",
            "description": (
                "Строчка из известной израильской песни или расхожая цитата, "
                "где встречается слово этого корня — только если РЕАЛЬНО "
                "известная и узнаваемая, не выдумывай и не подбирай натянуто. "
                "Пусто, если ничего подходящего не знаешь уверенно."
            ),
        },
        "comprehension_question": {
            "type": "object",
            "description": (
                "Один вопрос с вариантами, проверяющий понимание именно "
                "нюанса/расхождения из divergence_note или girzah_note — не "
                "тривиальный 'переведи слово', а что-то, что показывает, "
                "усвоил ли ученик суть, а не просто прочитал текст."
            ),
            "properties": {
                "question": {"type": "string"},
                "options": {"type": "array", "items": {"type": "string"}, "minItems": 3, "maxItems": 4},
                "correct_index": {"type": "integer", "description": "индекс правильного варианта в options, с нуля"},
            },
            "required": ["question", "options", "correct_index"],
        },
        "derived_words": {
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
        },
        "set_phrases": {
            "type": "array",
            "description": (
                "Устойчивые выражения/идиомы с этим корнем, ТОЛЬКО если реально "
                "частотные и известные — пустой массив, если ничего подходящего нет, "
                "не придумывай искусственные."
            ),
            "items": {
                "type": "object",
                "properties": {
                    "phrase_he": {"type": "string"},
                    "phrase_ru": {"type": "string"},
                    "note": {"type": "string", "description": "где уместно, коротко"},
                    "example_he": {
                        "type": "string",
                        "description": "Естественное предложение-пример с этим выражением, с огласовками.",
                    },
                    "example_ru": {"type": "string", "description": "Перевод example_he."},
                },
                "required": ["phrase_he", "phrase_ru", "note", "example_he", "example_ru"],
            },
        },
        "common_mistakes": {
            "type": "string",
            "description": (
                "1-3 предложения: с чем русскоязычные ученики обычно путают формы "
                "этого корня — ложные друзья, ошибки в биньяне/роде/предлоге, что "
                "нельзя переносить напрямую из русского. Пусто, если реально нечего "
                "сказать по существу."
            ),
        },
        "divergence_note": {
            "type": "string",
            "description": (
                "Если какое-то из производных слов значит НЕ то, что подсказывает "
                "semantic_field (частый случай, не исключение) — честно это "
                "объяснить здесь. Пустая строка, если реальных расхождений среди "
                "выбранных слов нет — не придумывать натянутое."
            ),
        },
        "transition": {
            "type": "string",
            "description": "Одна переходная фраза к практике, напр. 'Теперь увидим этот корень в реальных предложениях.'",
        },
    },
    "required": [
        "root", "semantic_field", "origin_note", "girzah_note", "fun_fact",
        "modern_usage_note", "place_or_name_note", "common_collocation",
        "song_or_quote_note", "comprehension_question",
        "derived_words", "set_phrases", "common_mistakes", "divergence_note", "transition",
    ],
}

# Правила честности/проверяемости и формат — взяты из уже проверенного в
# бою AI_PEDAGOGY_PROMPT тренажёра (разбор слова ИИ-педагогом,
# template3.html) и адаптированы под КОРЕНЬ с несколькими производными
# словами, а не одно слово: та же глубина по объёму, что польз
# ователь уже видит на разборе слова, не тоньше.
PROMPT_TEMPLATE = """Ты работаешь как строгий, внимательный педагог иврита — тот же
стандарт качества и честности, что при разборе отдельного слова: не
быстрый переводчик, а преподаватель, объясняющий, как слово живёт в
языке. Готовишь теоретическую карточку по КОРНЮ {root} (не одному слову)
для курса изучающих иврит.

Правила честности (обязательно):
- СТРОГО полное написание (כתיב מלא) — ВСЕГДА, для КАЖДОГО ивритского
  слова во всём ответе (корень, производные, примеры, идиомы, топонимы,
  цитаты), без исключений. В частности: звук "о" — всегда וֹ (вав+холам),
  никогда одна буква с холамом без вав (חוֹק — верно, חֹק — ошибка); звук
  "у" — всегда וּ (вав+дагеш, шурук), никогда кубуц (קוּם — верно, קֻם —
  ошибка). Если сомневаешься между полным и неполным написанием — бери
  форму, как её пишут в современных словарях (Академия языка иврит,
  Морфикс) — там почти всегда именно כתיב מלא.
- Не выдумывай огласовку, если не уверен — если не уверен, оставь без
  огласовки и явно укажи это словом в usage_note.
- Не выдумывай этимологию/происхождение — если неясно или спорно, оставь
  origin_note пустым, не сочиняй красивую версию.
- Если корень/форма спорны или неоднозначны — так и скажи в тексте,
  не утверждай уверенно то, в чём нет уверенности.

Правила содержания:
- semantic_field: абзац (не одна строка) о ядре значения — с какими
  идеями/образами/областями связан корень, явно как интуиция-подсказка,
  не формула.
- girzah_note: объект {{type_label, compare, text}}. type_label — тип
  корня (שלמים обычный, или слабый тип — פ"נ/פ"י/פ"א/ע"ו/ע"י/ל"ה/כפולים).
  Если слабый — compare: 1-2 пары {{binyan, form, note}}, конкретно
  показывающие форму, которая отличается от обычного שלמים-паттерна (не
  абстрактно "меняется", а САМА форма с огласовками); text — 1-3
  предложения прозой о том же. Если корень שלמים (без особенностей) —
  compare пустой массив, text короткий, не изобретай сложность на пустом
  месте.
- fun_fact: одна ИСТОРИЧЕСКАЯ/традиционная деталь — библейская отсылка,
  роль в традиции, старая идиома с историей. Пусто, если нечего сказать.
- modern_usage_note: как корень живёт в СОВРЕМЕННОМ разговорном иврите/
  сленге/интернете сегодня (отдельно от fun_fact — тот про историю).
  Объект {{text, example_he, example_ru}} — если text не пуст, ОБЯЗАТЕЛЬНО
  дай живой пример-предложение (example_he/example_ru), не оставляй их
  пустыми при непустом text. Всё пусто, если ничего особенного нет.
- place_or_name_note: реальный узнаваемый топоним/улица/район/личное имя
  на этом корне. Объект {{text, example_he, example_ru}} — если text не
  пуст, дай предложение-пример, упоминающее это название. Всё пусто, если
  такого нет — не притягивай.
- common_collocation: с каким словом производное этого корня чаще всего
  стоит рядом в реальной речи, с примером внутри текста. Пусто, если нет
  явного.
- song_or_quote_note: строчка из ДЕЙСТВИТЕЛЬНО известной израильской
  песни или расхожей цитаты с этим корнем — только если уверен, что она
  реальная и узнаваемая, не выдумывай. Пусто, если не уверен.
- comprehension_question: один вопрос с вариантами, проверяющий именно
  нюанс/расхождение из divergence_note или girzah_note — не тривиальный
  "переведи слово", а что-то, показывающее реальное понимание сути.
- derived_words: 2-4 РЕАЛЬНО употребимых производных — только формы,
  которые для ЭТОГО корня реально существуют и частотны в современном
  иврите, не гипотетическая полная таблица по всем биньянам. На каждое:
  огласовки (полное написание, как в современных словарях — с вав/יод
  там, где они реально пишутся), биньян, часть речи, перевод, уровень
  употребимости (частотное/разговорное/книжное/официальное/редкое/
  сленговое и т.п.), короткий нюанс употребления если есть что сказать
  по существу, 1-2 живых примера-предложения с переводом.
- set_phrases: устойчивые выражения с этим корнем — ТОЛЬКО реально
  частотные и известные, пустой список если ничего подходящего нет. На
  каждое — ОБЯЗАТЕЛЬНО живой пример-предложение (example_he/example_ru),
  не просто перевод самой фразы.
- common_mistakes: с чем русскоязычные ученики реально путают формы
  этого корня — ложные друзья, типичные ошибки в биньяне/роде/предлоге.
- divergence_note: честно объясни, если какое-то производное значит не
  то, что подсказывает semantic_field (частый случай для ивритских
  корней) — пусто, если реальных расхождений среди выбранных слов нет.
- Не превращай карточку в энциклопедию — глубина как на разборе
  отдельного слова, но для нескольких слов сразу, так что каждый пункт
  по объёму компактнее, чем был бы для одного слова.

Известные частотные слова этого корня (для ориентира, не обязательно
использовать именно эти — если знаешь более показательные формы,
используй их):
{known_members}
"""


class QuotaExhausted(Exception):
    """Суточная квота модели исчерпана (429 RESOURCE_EXHAUSTED) — в
    отличие от прочих ошибок, повторять на ТОЙ ЖЕ модели бессмысленно
    (квота не появится за секунды retry), и это не имеет отношения к
    качеству контента для конкретного корня. run_roots_batch.py ловит
    именно это исключение, чтобы решить, какую модель считать
    исчерпанной НАВСЕГДА для этого прогона — а какую просто
    "не подошла для этого одного корня", не трогая остальные."""
    pass


def _is_quota_error(exc):
    return "RESOURCE_EXHAUSTED" in str(exc) or " 429" in str(exc) or str(exc).startswith("429")


CHOLAM = "ֹ"   # ֹ  — холам (гласная "о")
KUBUTZ = "ֻ"   # ֻ  — кубуц (гласная "у", неполное написание)
VAV = "ו"      # ו
# короткие служебные слова, где холам без вав — стандартное написание,
# не нарушение כתיב מלא (двух-трёхбуквенные слова этого типа в него не
# разворачиваются ни в каком стиле письма)
KTIV_CHASER_ALLOWLIST = {"לֹא", "כֹּה", "זֹאת", "פֹּה"}
_STRIP_PUNCT = ".,!?;:\"'()«»־-־"


def _bare_consonants(word):
    return "".join(c for c in word if unicodedata.category(c) != "Mn")


def _matches_allowlist(word):
    """Проверяет и голое слово, и слово с приставками (ו/ה/ב/כ/ל/מ/ש +
    дагеш удвоения) — וְזֹאת/הַזֹּאת и т.п. не должны считаться ошибкой
    так же, как и голое זֹאת: сравниваем по буквам без огласовок, по
    суффиксу, а не только точным совпадением всего слова."""
    if word in KTIV_CHASER_ALLOWLIST:
        return True
    bare = _bare_consonants(word)
    return any(bare.endswith(_bare_consonants(allowed)) for allowed in KTIV_CHASER_ALLOWLIST)


def _split_clusters(word):
    """Разбивает слово на кластеры (базовая буква + все её комбинирующие
    знаки, категория Unicode Mn). Нужно, потому что некоторые буквы несут
    СРАЗУ два огласовочных знака (напр. каф с дагешем И холамом в הַכֹּל
    — "всё") — проверка/фикс по одному символу назад ошибочно принимает
    дагеш за "не вав перед холамом" и вставляет вав между дагешем и
    холамом вместо того, чтобы понять, что холам вообще сидит не на той
    букве. Кластеры решают это: смотрим на БАЗОВУЮ букву кластера, не на
    непосредственно предыдущий символ."""
    clusters = []
    for ch in word:
        if clusters and unicodedata.category(ch) == "Mn":
            clusters[-1] += ch
        else:
            clusters.append(ch)
    return clusters


def find_ktiv_chaser_violations(obj, path=""):
    """Рекурсивно ищет в распарсенном JSON-ответе огласованные ивритские
    слова с явными признаками неполного написания (כתיב חסר):
    - холам не на вав (напр. חֹק вместо חוֹק — тот самый баг, который уже
      один раз проскочил вручную в мокапе теории для ק-ו-ם);
    - кубуц вместо шурук (напр. קֻם вместо קוּם).
    Проверяет по словам (не по всей строке разом), чтобы не считать
    ошибкой короткие служебные слова из KTIV_CHASER_ALLOWLIST (לֹא и т.п.),
    для которых холам без вав — норма в любом стиле письма. Не ловит все
    возможные случаи כתיב חסר (это лингвистически не всегда однозначно
    формализуемо), но ловит два самых частых и однозначных — этого
    достаточно, чтобы не пропускать баг молча, как уже случилось."""
    violations = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            violations += find_ktiv_chaser_violations(v, f"{path}.{k}" if path else k)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            violations += find_ktiv_chaser_violations(v, f"{path}[{i}]")
    elif isinstance(obj, str):
        for word in obj.split():
            clean = word.strip(_STRIP_PUNCT)
            if _matches_allowlist(clean):
                continue
            clusters = _split_clusters(clean)
            for i, cl in enumerate(clusters):
                if CHOLAM in cl and cl[0] != VAV:
                    violations.append((path, clean, "холам без вав — похоже на כתיב חסר, нужно וֹ"))
                elif KUBUTZ in cl:
                    violations.append((path, clean, "кубуц вместо шурук — в современном написании обычно וּ"))
    return violations


DAGESH = "ּ"  # ּ — дагеш (для шурук: вав + дагеш)


def _fix_ktiv_chaser_word(word):
    """Механически чинит ОДНО слово: холам не на вав -> вставить вав
    перед холамом; кубуц -> заменить на вав+дагеш (шурук). Оба
    преобразования однозначны по построению (не требуют понимания
    смысла слова), поэтому чиним программно вместо того, чтобы просить
    модель повторить — на практике для некоторых паттернов (напр.
    биньян פֻּעַל с кубуцем: מְיֻחָד) модель воспроизводит традиционное
    написание раз за разом НЕЗАВИСИМО ОТ МОДЕЛИ (см. run_roots_batch.py,
    прогон 2026-09-12 на ב-נ-ה — несколько разных моделей подряд не
    справились ретраями), так что повтор просто тратит квоту впустую.

    Работает по кластерам (буква + все её огласовки), не по одиночным
    символам — см. _split_clusters: буква может нести дагеш И холам
    ОДНОВРЕМЕННО (напр. каф в הַכֹּל — "всё"), и посимвольная проверка
    "предыдущий символ — вав?" в этом случае ошибочно видит дагеш вместо
    вав и вставляет новый вав ВНУТРИ кластера — портит слово (реальный
    баг, пойманный вживую на первом же растущем тексте, см.
    ROOTS_CURRICULUM.md)."""
    out = []
    for cl in _split_clusters(word):
        base, marks = cl[0], cl[1:]
        if CHOLAM in marks and base != VAV:
            out.append(base + marks.replace(CHOLAM, ""))
            out.append(VAV + CHOLAM)
        elif KUBUTZ in marks:
            out.append(base + marks.replace(KUBUTZ, ""))
            out.append(VAV + DAGESH)
        else:
            out.append(cl)
    return "".join(out)


def fix_ktiv_chaser(obj):
    """Рекурсивно применяет _fix_ktiv_chaser_word по всему JSON-объекту,
    сохраняя пробелы/пунктуацию на границах слов и не трогая слова из
    KTIV_CHASER_ALLOWLIST (см. _matches_allowlist)."""
    if isinstance(obj, dict):
        return {k: fix_ktiv_chaser(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [fix_ktiv_chaser(v) for v in obj]
    elif isinstance(obj, str):
        tokens = re.split(r"(\s+)", obj)
        for idx, tok in enumerate(tokens):
            if not tok or tok.isspace():
                continue
            core = tok.strip(_STRIP_PUNCT)
            if not core or _matches_allowlist(core):
                continue
            start = tok.index(core)
            tokens[idx] = tok[:start] + _fix_ktiv_chaser_word(core) + tok[start + len(core):]
        return "".join(tokens)
    return obj


def process_one(client, model, root_entry, retries=3):
    known = "\n".join(f"- {m['he']} ({m['translit']}) — {m['en']}" for m in root_entry["members"][:6])
    prompt = PROMPT_TEMPLATE.format(root=root_entry["root"], known_members=known)
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
            # Модель иногда эхом возвращает корень с другим символом
            # тире (напр. маqaf ־ вместо обычного -) — если доверять
            # этому эху как ключу при повторном запуске, run_roots_batch.py
            # не находит уже готовую запись по каноническому написанию из
            # root_frequency.json и генерирует ещё одну, с виду другую
            # запись для того же корня (реальный случай — см.
            # ROOTS_CURRICULUM.md, шаг 4, дубль ס-ו-ף/ס־ו־ף). Всегда
            # перезаписываем на каноническое написание.
            result["root"] = root_entry["root"]
        except Exception as e:
            if _is_quota_error(e):
                raise QuotaExhausted(str(e)) from e
            print(f"  попытка {attempt + 1}/{retries} не удалась ({e}), жду и повторяю...", file=sys.stderr)
            time.sleep(3)
            continue

        result = fix_ktiv_chaser(result)  # механический фикс — быстрее и надёжнее ретрая (см. docstring выше)
        violations = find_ktiv_chaser_violations(result)
        if not violations:
            return result

        # Сюда попадаем, только если автофикс не справился — то есть
        # нарушение шире двух охваченных паттернов (маловероятно, но
        # тогда ретрай — единственный оставшийся вариант).
        last_result, last_violations = result, violations
        print(f"  попытка {attempt + 1}/{retries}: автофикс не справился, остались нарушения ({len(violations)} шт.), повторяю генерацию...", file=sys.stderr)
        for path, word, reason in violations:
            print(f"    - [{path}] {word!r}: {reason}", file=sys.stderr)
        time.sleep(1)

    if last_result is not None:
        # Не возвращаем last_result даже как черновик — см. тот же
        # аргумент, что и раньше: при автопрогоне на 200 корнях лучше
        # попробовать другую модель, чем тихо принять то, что есть.
        print(f"  !! после {retries} попыток и автофикса для {root_entry['root']} остались нарушения, результат ОТБРОШЕН:", file=sys.stderr)
        for path, word, reason in last_violations:
            print(f"    - [{path}] {word!r}: {reason}", file=sys.stderr)
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("roots_path", help="JSON-файл — список записей из root_frequency.json")
    ap.add_argument("--out", default="theory_test_output.json")
    ap.add_argument("--model", default="gemini-3.6-flash")
    ap.add_argument("--api-key", default=None)
    args = ap.parse_args()

    api_key = args.api_key or os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("Нужен API-ключ: --api-key или GEMINI_API_KEY.", file=sys.stderr)
        sys.exit(1)

    client = genai.Client(api_key=api_key)

    with open(args.roots_path, encoding="utf-8") as f:
        roots = json.load(f)

    results = []
    for i, r in enumerate(roots):
        print(f"[{i + 1}/{len(roots)}] {r['root']}...", file=sys.stderr)
        res = process_one(client, args.model, r)
        if res is None:
            print(f"  !! не удалось для {r['root']}", file=sys.stderr)
            continue
        results.append(res)

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\nГотово: {len(results)}/{len(roots)} -> {args.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
