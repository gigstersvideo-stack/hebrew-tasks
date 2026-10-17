"""Курс «Праздники Израиля» — сборка holidays_course.json для тренажёра.
Источники: holidays/<глава>_cards.json (карточки), holidays/linked_songs.json (защищённые песни),
CHAPTERS ниже (порядок, даты, что лежит в читалке). Даты начала (вечер накануне) — Hebcal, Израиль.

    python holidays/build_course.py
"""
import json, os, sys
sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# ready=True — глава готова (есть материалы в читалке и карточки). starts — вечер, когда праздник начинается.
CHAPTERS = [
 {"id": "shabbat", "emoji": "🍞", "title": "Шабат", "he": "שַׁבָּת", "weekly": True,
  "blurb": "Каждую неделю: свечи, кидуш, две халы — и почему отдыхают именно в седьмой день.",
  "book": {"id": "shabbat-alef", "title": "שְׁתֵּי חַלּוֹת", "level": "א"},
  "songs": [{"id": "shalom-aleichem", "title": "שָׁלוֹם עֲלֵיכֶם", "level": "ב"},
            {"id": "shabbat-hamalka", "title": "שַׁבָּת הַמַּלְכָּה", "level": "ג"},
            {"id": "kiddush-shabbat", "title": "קִידּוּשׁ לְלֵיל שַׁבָּת", "level": "ד"}],
  "cards": "shabbat_cards.json"},
 {"id": "rosh-hashana", "emoji": "🍎", "title": "Рош ха-Шана", "he": "רֹאשׁ הַשָּׁנָה",
  "blurb": "Еврейский Новый год: яблоко с мёдом, шофар и «шана това».", "starts": ["2027-10-01", "2028-09-20"]},
 {"id": "yom-kippur", "emoji": "🕊️", "title": "Йом Кипур", "he": "יוֹם כִּיפּוּר",
  "blurb": "День искупления: пост, тишина, пустые дороги и дети на велосипедах.", "starts": ["2027-10-10", "2028-09-29"]},
 {"id": "sukkot", "emoji": "🛖", "title": "Суккот и Симхат Тора", "he": "סוּכּוֹת",
  "blurb": "Шалаши во дворах и на балконах, лулав и этрог; в конце — танцы с Торой.", "starts": ["2027-10-15", "2028-10-04"]},
 {"id": "hanukkah", "emoji": "🕎", "title": "Ханука", "he": "חֲנוּכָּה",
  "blurb": "Восемь вечеров свечей: Маккавеи, кувшинчик масла, свивон и пончики.",
  "starts": ["2026-12-04", "2027-12-24", "2028-12-12"], "days": 8,
  "book": {"id": "hanukkah-alef", "title": "הַנֵּס שֶׁל חֲנוּכָּה", "level": "א"},
  "songs": [{"id": "lichvod-hachanukah", "title": "לִכְבוֹד הַחֲנוּכָּה", "level": "ב"},
            {"id": "hanukkah-brachot", "title": "בִּרְכוֹת הַדְלָקַת הַנֵּרוֹת", "level": "ב"},
            {"id": "maoz-tzur", "title": "מָעוֹז צוּר", "level": "ד"}],
  "cards": "hanukkah_cards.json"},
 {"id": "tu-bishvat", "emoji": "🌱", "title": "Ту би-Шват", "he": "ט״ו בִּשְׁבָט",
  "blurb": "Новый год деревьев: цветёт миндаль, сажают деревья, едят сухофрукты — и почему именно 15-е.", "starts": ["2027-01-22", "2028-02-11"],
  "book": {"id": "tubishvat-alef", "title": "חוֹנִי וְהֶחָרוּב", "level": "א"},
  "songs": [{"id": "hashkediya", "title": "הַשְּׁקֵדִיָּיה פּוֹרַחַת", "level": "ב"},
            {"id": "eretz-chita", "title": "אֶרֶץ חִטָּה וּשְׂעוֹרָה", "level": "ב"},
            {"id": "rosh-hashana-lailan", "title": "רֹאשׁ הַשָּׁנָה לָאִילָן", "level": "ד"}],
  "cards": "tubishvat_cards.json"},
 {"id": "purim", "emoji": "🎭", "title": "Пурим", "he": "פּוּרִים",
  "blurb": "Свиток Эстер, маскарад, подарки соседям и трещотки на имени Амана — и почему «Пурим» значит «жребии».", "starts": ["2027-03-22", "2028-03-11"],
  "book": {"id": "purim-alef", "title": "הַמֶּלֶךְ שֶׁל פּוּרִים", "level": "א"},
  "songs": [{"id": "layehudim", "title": "לַיְּהוּדִים הָיְיתָה אוֹרָה", "level": "ב"},
            {"id": "mishenichnas-adar", "title": "מִשֶּׁנִּכְנַס אֲדָר", "level": "ב"},
            {"id": "shoshanat-yaakov", "title": "שׁוֹשַׁנַּת יַעֲקוֹב", "level": "ג"},
            {"id": "megillat-esther", "title": "מִן הַמְּגִילָּה", "level": "ד"}],
  "cards": "purim_cards.json"},
 {"id": "pesach", "emoji": "🫓", "title": "Песах", "he": "פֶּסַח",
  "blurb": "Исход из Египта: седер, маца и Агада — самая «певучая» глава.", "starts": ["2027-04-21", "2028-04-10"], "days": 7},
 {"id": "yom-hashoah", "emoji": "🕯️", "title": "Йом ха-Шоа", "he": "יוֹם הַשּׁוֹאָה",
  "blurb": "День памяти Катастрофы: сирена, свечи, имена.", "starts": ["2027-05-03", "2028-04-23"]},
 {"id": "zikaron-atzmaut", "emoji": "✡️", "title": "Йом ха-Зикарон и Йом ха-Ацмаут", "he": "יוֹם הַזִּיכָּרוֹן וְיוֹם הָעַצְמָאוּת",
  "blurb": "День памяти павших — и сразу за ним День независимости.", "starts": ["2027-05-10", "2028-04-30"], "days": 2},
 {"id": "lag-baomer", "emoji": "🔥", "title": "Лаг ба-Омер", "he": "ל״ג בָּעוֹמֶר",
  "blurb": "Костры по всей стране, рабби Шимон бар Йохай и ученики рабби Акивы.", "starts": ["2027-05-24", "2028-05-13"]},
 {"id": "shavuot", "emoji": "🌾", "title": "Шавуот", "he": "שָׁבוּעוֹת",
  "blurb": "Праздник урожая и дарования Торы: белая одежда, молочное, книга Рут.", "starts": ["2027-06-10", "2028-05-30"]},
 {"id": "av", "emoji": "🍇", "title": "Тиша бе-Ав и Ту бе-Ав", "he": "תִּשְׁעָה בְּאָב · ט״ו בְּאָב",
  "blurb": "День траура по Храму — а через неделю день любви.", "starts": ["2027-08-11", "2028-07-31"], "days": 8},
]

linked = json.load(open(os.path.join(HERE, "linked_songs.json"), encoding="utf-8"))
out = []
for ch in CHAPTERS:
    c = {k: v for k, v in ch.items() if k != "cards"}
    c["ready"] = "cards" in ch
    if "cards" in ch:
        cards = json.load(open(os.path.join(HERE, ch["cards"]), encoding="utf-8"))
        c["cards"] = [{"he": x["he"], "ru": x["ru"], "cloze_token": x["cloze_token"], "layer": x["layer"]} for x in cards]
        c["linked"] = [{k: v for k, v in s.items() if k != "chapter"} for s in linked if s["chapter"] == ch["id"]]
    out.append(c)
p = os.path.join(ROOT, "holidays_course.json")
json.dump({"chapters": out}, open(p, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
# Календарь для читалки (вкладка «Праздники и песни», сезонная плашка) — только даты и названия.
cal = [{k: c[k] for k in ("id", "emoji", "title", "he", "weekly", "starts", "days", "ready") if k in c} for c in out]
json.dump({"chapters": cal}, open(r"E:\hebrew-reader\holidays.json", "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
print(f"{len(out)} глав, готовы: {[c['id'] for c in out if c['ready']]}, карточек: {sum(len(c.get('cards', [])) for c in out)}, {os.path.getsize(p)//1024} КБ")
