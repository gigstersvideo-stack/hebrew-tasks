"""Русская транскрипция огласованного иврита (для проверки озвучки на слух владельцем).

    from he_translit import translit; translit("לִפְגּוֹשׁ") -> "лифгош"

Приблизительно: без ударения; шва под первой буквой слова — «е», в середине — не читается;
немые йуд/вав полного письма пропускаются; патах гнува (ַח/ַע в конце) — «ах».
"""
import re
import unicodedata

DAGESH, SHIN, SIN, HOLAM, SHVA = "ּ", "ׁ", "ׂ", "ֹ", "ְ"
V = {"ַ": "а", "ָ": "а", "ֲ": "а", "ֶ": "е", "ֵ": "е", "ֱ": "е",
     "ִ": "и", "ֹ": "о", "ֺ": "о", "ֳ": "о", "ֻ": "у"}
C = {"א": "", "ג": "г", "ד": "д", "ה": "х", "ז": "з", "ח": "х", "ט": "т", "י": "й", "ל": "л", "מ": "м",
     "ם": "м", "נ": "н", "ן": "н", "ס": "с", "ע": "", "צ": "ц", "ץ": "ц", "ק": "к", "ר": "р", "ת": "т"}
HEB = re.compile(r"[א-ת]")


def _clusters(w):
    out = []
    for ch in unicodedata.normalize("NFD", w):
        if HEB.match(ch):
            out.append([ch, ""])
        elif "֑" <= ch <= "ׇ" and out:
            out[-1][1] += ch
    return out


def translit_word(w):
    cl = _clusters(w)
    res = []
    for i, (l, m) in enumerate(cl):
        v = "".join(V[x] for x in m if x in V)
        last = i == len(cl) - 1
        if l == "ו":
            if DAGESH in m and not v:
                res.append("у"); continue
            if HOLAM in m and (not v.replace("о", "")):
                after_shva = i > 0 and SHVA in cl[i - 1][1]   # מִצְוֹת — вав согласный
                res.append("во" if after_shva else "о"); continue
            if not m and i > 0:
                continue  # немой вав
            c = "в"
        elif l == "י" and not v and i > 0 and not (DAGESH in m):
            prev = cl[i - 1][1]
            if any(x in prev for x in ("ִ", "ֵ", "ֶ")):  # mater после и/е
                continue
            c = "й"
        elif l in "בכךפף":
            hard = DAGESH in m and l not in "ךף"
            c = {"ב": "б" if hard else "в", "כ": "к" if hard else "х", "ך": "х", "פ": "п" if hard else "ф", "ף": "ф"}[l]
        elif l == "ש":
            c = "с" if SIN in m else "ш"
        elif l == "ה" and last and not v and DAGESH not in m:
            c = ""  # немая hе в конце
        else:
            c = C.get(l, "")
        if last and l in "חע" and "ַ" in m:
            res.append("а" + c); continue  # патах гнува
        if not v and SHVA in m and i == 0:
            v = "е"
        res.append(c + v)
    return "".join(res)


def translit(text):
    return " ".join(translit_word(w) for w in re.split(r"\s+", text) if HEB.search(w))


def holam_male_to_haser(text):
    """Для озвучки: וֹ (холам малэ) → холам на предыдущей букве без вав — голоса читают «וֹ» как «во».
    Показ слова не меняется, правится только строка, которая уходит в синтез."""
    out = []
    for w in text.split(" "):
        cl = _clusters(w)
        if not cl or not any(l == "ו" and HOLAM in m for l, m in cl):
            out.append(w); continue
        res, i = [], 0
        tail = re.sub(r"^.*[א-ת֑-ׇ]", "", unicodedata.normalize("NFD", w))
        head = re.match(r"^[^א-ת]*", unicodedata.normalize("NFD", w)).group(0)
        for k, (l, m) in enumerate(cl):
            if (l == "ו" and HOLAM in m and not any(x in V and x != HOLAM for x in m) and res
                    and not any(x in V for x in res[-1][1]) and res[-1][0] != "ו"
                    and SHVA not in res[-1][1]          # после шва вав — согласный (מִצְוֹת)
                    and k != len(cl) - 1):              # окончание -וֹ в конце слова не трогаем
                res[-1][1] += HOLAM
                continue
            res.append([l, m])
        out.append(head + "".join(l + m for l, m in res) + tail)
    return unicodedata.normalize("NFC", " ".join(out))
