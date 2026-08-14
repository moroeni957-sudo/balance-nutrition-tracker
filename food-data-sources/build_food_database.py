"""Build the static browser food catalog from official USDA and CIQUAL downloads."""

from __future__ import annotations

import json
import io
import re
import unicodedata
import zipfile
from collections import defaultdict
from pathlib import Path
from xml.etree import ElementTree as ET


ROOT = Path(__file__).resolve().parent
RAW = ROOT / "raw"
OUTPUT = ROOT.parent / "food-database.js"
TRANSLATIONS_FILE = ROOT / "translations-en-ru.json"
TRANSLATIONS = json.loads(TRANSLATIONS_FILE.read_text(encoding="utf-8")) if TRANSLATIONS_FILE.exists() else {}
TRANSLATIONS_CASEFOLD = {key.casefold(): value for key, value in TRANSLATIONS.items()}
BLS_FILE = RAW / "bls-4.0-2025-de" / "BLS_4_0_2025_DE" / "BLS_4_0_Daten_2025_DE.xlsx"
NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}

GROUPS = {
    "Dairy and Egg Products": "Молочные продукты и яйца",
    "Spices and Herbs": "Специи и травы",
    "Baby Foods": "Детское питание",
    "Fats and Oils": "Масла и жиры",
    "Poultry Products": "Мясо и птица",
    "Soups, Sauces, and Gravies": "Супы и соусы",
    "Sausages and Luncheon Meats": "Мясо и птица",
    "Breakfast Cereals": "Крупы и макароны",
    "Fruits and Fruit Juices": "Фрукты и ягоды",
    "Pork Products": "Мясо и птица",
    "Vegetables and Vegetable Products": "Овощи",
    "Nut and Seed Products": "Орехи и семена",
    "Beef Products": "Мясо и птица",
    "Beverages": "Напитки",
    "Finfish and Shellfish Products": "Рыба и морепродукты",
    "Legumes and Legume Products": "Бобовые",
    "Lamb, Veal, and Game Products": "Мясо и птица",
    "Baked Products": "Хлеб и выпечка",
    "Sweets": "Сладости",
    "Cereal Grains and Pasta": "Крупы и макароны",
    "Fast Foods": "Готовые блюда",
    "Meals, Entrees, and Side Dishes": "Готовые блюда",
    "Snacks": "Закуски",
    "Restaurant Foods": "Готовые блюда",
    "American Indian/Alaska Native Foods": "Прочие продукты",
}

GROUP_KEYWORDS = (
    (("milk", "yogurt", "cheese", "cream", "butter", "egg", "dairy"), "Молочные продукты и яйца"),
    (("beef", "pork", "chicken", "turkey", "lamb", "veal", "meat", "sausage", "ham"), "Мясо и птица"),
    (("fish", "salmon", "tuna", "cod", "shrimp", "seafood", "shellfish"), "Рыба и морепродукты"),
    (("bread", "cake", "cookie", "pastry", "biscuit", "bakery"), "Хлеб и выпечка"),
    (("rice", "pasta", "cereal", "grain", "oat", "wheat", "noodle"), "Крупы и макароны"),
    (("vegetable", "potato", "tomato", "carrot", "salad"), "Овощи"),
    (("fruit", "apple", "banana", "berry", "juice"), "Фрукты и ягоды"),
    (("bean", "pea", "lentil", "legume"), "Бобовые"),
    (("nut", "seed"), "Орехи и семена"),
    (("oil", "fat", "margarine"), "Масла и жиры"),
    (("drink", "beverage", "coffee", "tea", "water"), "Напитки"),
    (("candy", "chocolate", "sweet", "dessert", "sugar"), "Сладости"),
    (("soup", "sauce", "gravy"), "Супы и соусы"),
    (("dish", "meal", "sandwich", "pizza", "burger"), "Готовые блюда"),
)

BASE_NAMES = (
    (r"buckwheat", "Гречневая крупа"), (r"brown rice", "Рис бурый"),
    (r"wild rice", "Рис дикий"), (r"\brice\b", "Рис"), (r"oatmeal|oat flakes|\boats?\b", "Овсяная крупа"),
    (r"barley", "Ячмень"), (r"millet", "Пшено"), (r"quinoa", "Киноа"),
    (r"pasta|spaghetti|macaroni|noodles", "Макаронные изделия"),
    (r"whole milk", "Молоко цельное"), (r"skim milk|nonfat milk", "Молоко обезжиренное"),
    (r"\bmilk\b", "Молоко"), (r"kefir", "Кефир"), (r"yogurt", "Йогурт"),
    (r"cottage cheese", "Творог"), (r"mozzarella", "Сыр моцарелла"),
    (r"cheddar", "Сыр чеддер"), (r"parmesan", "Сыр пармезан"),
    (r"gouda", "Сыр гауда"), (r"brie", "Сыр бри"), (r"feta", "Сыр фета"),
    (r"camembert", "Сыр камамбер"), (r"ricotta", "Сыр рикотта"),
    (r"blue cheese", "Сыр с голубой плесенью"), (r"cream cheese", "Сыр сливочный"),
    (r"\bcheese\b", "Сыр"),
    (r"^chicken eggs?\b|^eggs?, chicken\b", "Куриное яйцо"), (r"chicken fat|fat, chicken", "Куриный жир"),
    (r"chicken tenders?", "Куриные стрипсы"), (r"chicken.*nuggets?|nuggets?.*chicken", "Куриные наггетсы"),
    (r"chicken.*(sausage|bratwurst|frankfurter)|(sausage|bratwurst|frankfurter).*chicken", "Куриная колбаса"),
    (r"chicken (stock|broth)", "Куриный бульон"), (r"chicken soup", "Куриный суп"),
    (r"chicken.*\bbreasts?\b", "Куриная грудка"), (r"chicken.*\bthighs?\b", "Куриное бедро"),
    (r"chicken.*\bwings?\b", "Куриное крыло"), (r"chicken.*\bdrumsticks?\b", "Куриная голень"),
    (r"chicken.*\b(legs?|quarters?)\b", "Куриный окорочок"),
    (r"chicken.*\blivers?\b|\blivers?\b.*chicken", "Куриная печень"),
    (r"chicken.*\bhearts?\b|\bhearts?\b.*chicken", "Куриное сердце"),
    (r"chicken.*\b(gizzards?|stomachs?)\b|\b(gizzards?|stomachs?)\b.*chicken", "Куриный желудок"),
    (r"(ground|minced) chicken|chicken.*(ground|minced)", "Куриный фарш"),
    (r"\bchicken\b", "Курица"), (r"turkey.*\bbreast\b", "Грудка индейки"),
    (r"\bturkey\b", "Индейка"), (r"beef.*\bliver", "Говяжья печень"),
    (r"beef.*\b(ground|minced)\b", "Говяжий фарш"), (r"\bbeef\b", "Говядина"),
    (r"\bpork\b", "Свинина"), (r"\blamb\b", "Баранина"), (r"\bveal\b", "Телятина"),
    (r"\begg", "Яйцо"), (r"salmon", "Лосось"), (r"tuna", "Тунец"),
    (r"cod", "Треска"), (r"herring", "Сельдь"), (r"shrimp", "Креветки"),
    (r"potato", "Картофель"), (r"tomato", "Помидор"), (r"cucumber", "Огурец"),
    (r"carrot", "Морковь"), (r"cabbage", "Капуста"), (r"broccoli", "Брокколи"),
    (r"spinach", "Шпинат"), (r"onion", "Лук"), (r"garlic", "Чеснок"),
    (r"apple", "Яблоко"), (r"banana", "Банан"), (r"orange", "Апельсин"),
    (r"pear", "Груша"), (r"strawberr", "Клубника"), (r"blueberr", "Черника"),
    (r"raspberr", "Малина"), (r"grape", "Виноград"), (r"peach", "Персик"),
    (r"lentil", "Чечевица"), (r"chickpea", "Нут"), (r"\bbeans?\b", "Фасоль"),
    (r"walnut", "Грецкий орех"), (r"almond", "Миндаль"), (r"peanut", "Арахис"),
    (r"sunflower seed", "Семена подсолнечника"), (r"olive oil", "Оливковое масло"),
    (r"sunflower oil", "Подсолнечное масло"), (r"\bbutter\b", "Сливочное масло"),
    (r"whole.?wheat bread|wholemeal bread", "Хлеб цельнозерновой"), (r"rye bread", "Хлеб ржаной"),
    (r"white bread", "Хлеб белый"), (r"\bbread\b", "Хлеб"),
    (r"chocolate", "Шоколад"), (r"ice cream", "Мороженое"),
    (r"coffee", "Кофе"), (r"\btea\b", "Чай"), (r"^water\b|drinking water|mineral water", "Вода"),
    (r"soup", "Суп"), (r"pizza", "Пицца"), (r"salad", "Салат"),
)

STATES = (
    (r"\braw\b|uncooked", ("сырой", "сырая", "сырое", "сырые")),
    (r"\bdry\b|\bdried\b", ("сухой", "сухая", "сухое", "сухие")),
    (r"\bboiled\b|cooked in water|parboiled, cooked", ("варёный", "варёная", "варёное", "варёные")),
    (r"steamed", ("на пару",) * 4),
    (r"baked|roasted", ("запечённый", "запечённая", "запечённое", "запечённые")),
    (r"fried", ("жареный", "жареная", "жареное", "жареные")),
    (r"grilled", ("на гриле",) * 4),
    (r"smoked", ("копчёный", "копчёная", "копчёное", "копчёные")),
    (r"canned", ("консервированный", "консервированная", "консервированное", "консервированные")),
    (r"frozen", ("замороженный", "замороженная", "замороженное", "замороженные")),
    (r"fresh", ("свежий", "свежая", "свежее", "свежие")),
    (r"dehydrated", ("сушёный", "сушёная", "сушёное", "сушёные")),
    (r"braised|stewed", ("тушёный", "тушёная", "тушёное", "тушёные")),
    (r"\bcooked\b|prepared", ("приготовленный", "приготовленная", "приготовленное", "приготовленные")),
)

BLS_GROUPS = {
    "B": "Хлеб и выпечка", "C": "Крупы и макароны", "D": "Хлеб и выпечка", "E": "Крупы и макароны",
    "F": "Фрукты и ягоды", "G": "Овощи", "H": "Бобовые", "K": "Овощи",
    "M": "Молочные продукты и яйца", "N": "Напитки", "P": "Напитки", "Q": "Масла и жиры",
    "R": "Специи и травы", "S": "Сладости", "T": "Рыба и морепродукты",
    "U": "Мясо и птица", "V": "Мясо и птица", "W": "Мясо и птица", "X": "Готовые блюда", "Y": "Готовые блюда",
}


def number(value):
    if value is None or value == "" or value == "-":
        return None
    text = str(value).strip().replace(",", ".")
    less = text.startswith("<")
    text = re.sub(r"[^0-9.\-]", "", text)
    try:
        result = float(text)
        return result / 2 if less else result
    except ValueError:
        return None


def clean(text):
    return re.sub(r"\s+", " ", str(text or "")).strip(" ,-;")


def russian_group(source_group, description):
    source_group = clean(source_group)
    if source_group in GROUPS:
        return GROUPS[source_group]
    haystack = f"{source_group} {description}".lower()
    for words, translated in GROUP_KEYWORDS:
        if any(word in haystack for word in words):
            return translated
    return "Прочие продукты"


def subgroup(group, description, source_subgroup=""):
    text = description.lower()
    rules = {
        "Молочные продукты и яйца": (("cheese", "Сыры"), ("yogurt", "Йогурты"), ("milk", "Молоко"), ("egg", "Яйца"), ("cream", "Сливки и сметана")),
        "Мясо и птица": (("chicken", "Курица"), ("turkey", "Индейка"), ("beef", "Говядина"), ("pork", "Свинина"), ("lamb", "Баранина"), ("sausage", "Колбасы")),
        "Рыба и морепродукты": (("fish", "Рыба"), ("salmon", "Рыба"), ("tuna", "Рыба"), ("shell", "Морепродукты"), ("shrimp", "Морепродукты")),
        "Крупы и макароны": (("rice", "Рис"), ("pasta", "Макароны"), ("noodle", "Макароны"), ("cereal", "Крупы"), ("grain", "Крупы")),
        "Хлеб и выпечка": (("bread", "Хлеб"), ("cake", "Торты и пирожные"), ("cookie", "Печенье"), ("pastry", "Выпечка")),
        "Овощи": (("potato", "Картофель"), ("salad", "Салаты"), ("vegetable", "Овощные продукты")),
        "Фрукты и ягоды": (("juice", "Соки"), ("berry", "Ягоды"), ("fruit", "Фрукты")),
        "Бобовые": (("bean", "Фасоль"), ("lentil", "Чечевица"), ("chickpea", "Нут"), ("pea", "Горох"), ("soy", "Соевые продукты")),
        "Орехи и семена": (("nut", "Орехи"), ("seed", "Семена"), ("butter", "Ореховые пасты")),
        "Масла и жиры": (("oil", "Растительные масла"), ("butter", "Сливочное масло"), ("margarine", "Маргарин и спреды")),
        "Напитки": (("coffee", "Кофе"), ("tea", "Чай"), ("alcohol", "Алкогольные напитки"), ("water", "Вода")),
        "Готовые блюда": (("sandwich", "Сэндвичи"), ("pizza", "Пицца"), ("salad", "Салаты"), ("meal", "Основные блюда")),
        "Сладости": (("chocolate", "Шоколад"), ("candy", "Конфеты"), ("ice cream", "Мороженое"), ("dessert", "Десерты")),
        "Супы и соусы": (("soup", "Супы"), ("sauce", "Соусы"), ("gravy", "Подливы"), ("broth", "Бульоны")),
        "Закуски": (("chip", "Чипсы"), ("cracker", "Крекеры"), ("popcorn", "Попкорн")),
        "Специи и травы": (("spice", "Специи"), ("herb", "Травы"), ("seasoning", "Приправы")),
        "Детское питание": (("cereal", "Каши"), ("fruit", "Фруктовое питание"), ("vegetable", "Овощное питание"), ("meat", "Мясное питание")),
    }
    for needle, translated in rules.get(group, ()):
        if needle in text:
            return translated
    defaults = {
        "Бобовые": "Другие бобовые", "Готовые блюда": "Другие блюда", "Детское питание": "Другие продукты для детей",
        "Закуски": "Другие закуски", "Крупы и макароны": "Другие крупы", "Масла и жиры": "Другие масла и жиры",
        "Молочные продукты и яйца": "Другие молочные продукты", "Мясо и птица": "Другие мясные продукты",
        "Напитки": "Другие напитки", "Овощи": "Другие овощи", "Орехи и семена": "Другие орехи и семена",
        "Рыба и морепродукты": "Другие рыбные продукты", "Сладости": "Другие сладости", "Специи и травы": "Другие специи",
        "Супы и соусы": "Другие супы и соусы", "Фрукты и ягоды": "Другие фрукты", "Хлеб и выпечка": "Другая выпечка",
    }
    return defaults.get(group, "Другие продукты")


def translated_name(original):
    original = clean(original)
    translated = TRANSLATIONS.get(original) or TRANSLATIONS_CASEFOLD.get(original.casefold())
    if translated:
        return translated
    lower = original.lower()
    if re.search(r"mock chicken|chicken flavored", lower):
        return original.title() if original.isupper() else original
    base = next((ru for pattern, ru in BASE_NAMES if re.search(pattern, lower)), "")
    if not base:
        return original.title() if original.isupper() else original
    if base == "Курица" and re.search(
        r"meatless|pot pie|salad|sandwich|soup|stock|broth|gravy|spread|patty|sausage|gumbo|giblets?|\bfeet\b|babyfood|^potatoes?\b|\bpesto\b|\bsauce\b|sweet and sour|bologna",
        lower,
    ):
        return original.title() if original.isupper() else original
    last_word = base.split()[-1].lower()
    form = 3 if last_word.endswith(("ы", "и", "ия")) else 2 if last_word.endswith(("о", "е")) else 1 if last_word.endswith(("а", "я", "ка", "ца")) else 0
    states = []
    has_cooked_state = bool(re.search(r"boiled|steamed|baked|roasted|fried|grilled|smoked|braised|stewed|\bcooked\b|prepared", lower))
    for pattern, forms in STATES:
        if pattern == r"\braw\b|uncooked" and has_cooked_state:
            continue
        if pattern == r"\bcooked\b|prepared" and states:
            continue
        label = forms[form]
        if re.search(pattern, lower) and label not in states:
            states.append(label)
    return f"{base}, {', '.join(states)}" if states else base


def nutrient_values(food):
    found = {}
    for item in food.get("foodNutrients", []):
        nutrient = item.get("nutrient") or {}
        nutrient_id = nutrient.get("id")
        if nutrient_id in (1003, 1004, 1005, 1008) and item.get("amount") is not None:
            found[nutrient_id] = number(item.get("amount"))
    if not all(found.get(key) is not None for key in (1003, 1004, 1005, 1008)):
        return None
    return [round(found[1008], 2), round(found[1003], 2), round(found[1004], 2), round(found[1005], 2)]


def read_usda(filename, key, source, level):
    with zipfile.ZipFile(RAW / filename) as archive:
        foods = json.load(archive.open(archive.namelist()[0]))[key]
    result = []
    for food in foods:
        if not isinstance(food, dict):
            continue
        macros = nutrient_values(food)
        if not macros:
            continue
        description = clean(food.get("description"))
        category = food.get("foodCategory") or food.get("wweiaFoodCategory") or {}
        category_name = category.get("description") or category.get("wweiaFoodCategoryDescription") or ""
        group = russian_group(category_name, description)
        result.append({
            "id": f"{source.lower()}-{food.get('fdcId')}", "level": level,
            "group": group, "subgroup": subgroup(group, description, category_name),
            "name": translated_name(description), "original": description,
            "kcal": macros[0], "protein": macros[1], "fat": macros[2], "carbs": macros[3],
            "source": source,
        })
    return result


def xlsx_rows(path):
    with zipfile.ZipFile(path) as archive:
        shared_root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
        shared = ["".join(node.text or "" for node in item.findall(".//m:t", NS)) for item in shared_root.findall("m:si", NS)]
        root = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
    for row in root.findall(".//m:sheetData/m:row", NS):
        values = {}
        for cell in row.findall("m:c", NS):
            ref = cell.get("r", "")
            column = re.match(r"[A-Z]+", ref).group()
            node = cell.find("m:v", NS)
            value = "" if node is None else node.text
            if cell.get("t") == "s" and value:
                value = shared[int(value)]
            values[column] = value
        yield values


def read_ciqual():
    rows = iter(xlsx_rows(RAW / "ciqual-2025-eng.xlsx"))
    next(rows, None)
    result = []
    for row in rows:
        macros = [number(row.get("K")), number(row.get("O")), number(row.get("R")), number(row.get("Q"))]
        if any(value is None for value in macros):
            continue
        description = clean(row.get("H"))
        source_group = clean(row.get("D"))
        source_subgroup = clean(row.get("E"))
        group = russian_group(source_group, description)
        result.append({
            "id": f"ciqual-{row.get('G')}", "level": 2,
            "group": group, "subgroup": subgroup(group, description, source_subgroup),
            "name": translated_name(description), "original": description,
            "kcal": round(macros[0], 2), "protein": round(macros[1], 2),
            "fat": round(macros[2], 2), "carbs": round(macros[3], 2), "source": "CIQUAL",
        })
    return result


def read_bls():
    rows = iter(xlsx_rows(BLS_FILE))
    next(rows, None)
    result = []
    for row in rows:
        macros = [number(row.get("G")), number(row.get("M")), number(row.get("P")), number(row.get("S"))]
        if any(value is None for value in macros):
            continue
        code = clean(row.get("A"))
        description = clean(row.get("C")) or clean(row.get("B"))
        group = BLS_GROUPS.get(code[:1], russian_group("", description))
        result.append({
            "id": f"bls-{code}", "level": 2,
            "group": group, "subgroup": subgroup(group, description),
            "name": translated_name(description), "original": description,
            "kcal": round(macros[0], 2), "protein": round(macros[1], 2),
            "fat": round(macros[2], 2), "carbs": round(macros[3], 2), "source": "BLS 4.0",
        })
    return result


def stream_json_array(zip_path, array_key):
    decoder = json.JSONDecoder()
    with zipfile.ZipFile(zip_path) as archive, io.TextIOWrapper(archive.open(archive.namelist()[0]), encoding="utf-8") as source:
        buffer = ""
        marker = f'"{array_key}"'
        while marker not in buffer:
            chunk = source.read(65536)
            if not chunk:
                return
            buffer += chunk
        array_start = buffer.find("[", buffer.find(marker))
        buffer = buffer[array_start + 1:]
        finished = False
        while not finished:
            buffer = buffer.lstrip(" \r\n\t,")
            if buffer.startswith("]"):
                return
            try:
                item, consumed = decoder.raw_decode(buffer)
                buffer = buffer[consumed:]
                yield item
            except json.JSONDecodeError:
                chunk = source.read(262144)
                if not chunk:
                    finished = True
                else:
                    buffer += chunk


def read_branded(candidate_limit=35000):
    path = RAW / "usda-branded-2026-04-30-json.zip"
    result = []
    for food in stream_json_array(path, "BrandedFoods"):
        if not isinstance(food, dict):
            continue
        macros = nutrient_values(food)
        if not macros or any(value < 0 for value in macros) or macros[1] > 100 or macros[2] > 100 or macros[3] > 100:
            continue
        description = clean(food.get("description"))
        if not description:
            continue
        category_name = clean(food.get("brandedFoodCategory"))
        group = russian_group(category_name, description)
        result.append({
            "id": f"usda-branded-{food.get('fdcId')}", "level": 2,
            "group": group, "subgroup": subgroup(group, description, category_name),
            "name": translated_name(description), "original": description,
            "kcal": macros[0], "protein": macros[1], "fat": macros[2], "carbs": macros[3],
            "source": "USDA Branded",
        })
        if len(result) >= candidate_limit:
            break
    return result


def normalized_key(item):
    return re.sub(r"[^a-z0-9]+", " ", item["original"].lower()).strip()


def translated_display_key(item):
    return re.sub(r"[^a-zа-я0-9]+", " ", item["name"].lower().replace("ё", "е")).strip()


TRANSLIT_CHARS = {
    "a": "а", "b": "б", "c": "к", "d": "д", "e": "е", "f": "ф", "g": "г",
    "h": "х", "i": "и", "j": "дж", "k": "к", "l": "л", "m": "м", "n": "н",
    "o": "о", "p": "п", "q": "к", "r": "р", "s": "с", "t": "т", "u": "у",
    "v": "в", "w": "в", "x": "кс", "y": "й", "z": "з",
}
QUALIFIER_PHRASES = (
    ("reduced sodium", "с пониженным содержанием натрия"),
    ("low fat", "с низким содержанием жира"),
    ("peanut butter", "арахисовая паста"),
    ("peanut flour", "арахисовая мука"),
    ("peanut spread", "арахисовая паста"),
    ("lemon chicken", "курица с лимоном"),
    ("orange chicken", "курица с апельсином"),
    ("sesame chicken", "курица с кунжутом"),
    ("restaurant", "ресторан"),
    ("chinese", "китайский"),
    ("peanuts", "арахис"),
    ("cornmeal", "кукурузная мука"),
    ("smooth", "однородная"),
)
TRANSLIT_DIGRAPHS = (
    ("shch", "щ"), ("sch", "щ"), ("zh", "ж"), ("kh", "х"),
    ("ch", "ч"), ("sh", "ш"), ("th", "т"), ("ph", "ф"),
    ("ck", "к"), ("qu", "кв"), ("wh", "в"),
)


def russian_qualifier(value):
    value = unicodedata.normalize("NFKD", value)
    value = "".join(char for char in value if not unicodedata.combining(char))
    for source, target in QUALIFIER_PHRASES:
        value = re.sub(re.escape(source), target, value, flags=re.IGNORECASE)
    for source, target in TRANSLIT_DIGRAPHS:
        value = re.sub(source, target, value, flags=re.IGNORECASE)
    converted = []
    for char in value:
        lower = char.lower()
        if lower in TRANSLIT_CHARS:
            piece = TRANSLIT_CHARS[lower]
            converted.append(piece.capitalize() if char.isupper() else piece)
        elif char.isalpha() and not ("а" <= lower <= "я" or lower == "ё"):
            continue
        else:
            converted.append(char)
    return re.sub(r"\s+", " ", "".join(converted)).strip(" ,.;-—")


def balanced_take(items, limit, seen):
    buckets = defaultdict(list)
    for item in items:
        key = normalized_key(item)
        if key and key not in seen:
            buckets[item["group"]].append(item)
    for bucket in buckets.values():
        bucket.sort(key=lambda item: (len(item["original"]), item["original"]))
    picked = []
    groups = sorted(buckets)
    while len(picked) < limit and groups:
        next_groups = []
        for group in groups:
            if len(picked) >= limit:
                break
            while buckets[group]:
                item = buckets[group].pop(0)
                key = normalized_key(item)
                if key not in seen:
                    seen.add(key)
                    picked.append(item)
                    break
            if buckets[group]:
                next_groups.append(group)
        groups = next_groups
    return picked


def required_take(items, patterns, seen):
    picked = []
    for pattern in patterns:
        candidates = [item for item in items if re.search(pattern, item["original"], re.IGNORECASE)]
        candidates.sort(key=lambda item: (len(item["original"]), item["original"]))
        for item in candidates:
            key = normalized_key(item)
            if key not in seen:
                seen.add(key)
                picked.append(item)
                break
    return picked


def main():
    foundation = read_usda("usda-foundation-2026-04-30-json.zip", "FoundationFoods", "USDA Foundation", 1)
    legacy = read_usda("usda-sr-legacy-2018-04-json.zip", "SRLegacyFoods", "USDA SR", 1)
    legacy = [item for item in legacy if not re.search(r"\b[A-Z]{3,}\b", item["original"])]
    fndds = read_usda("usda-fndds-2021-2023-json.zip", "SurveyFoods", "USDA FNDDS", 2)
    ciqual = read_ciqual()
    bls = read_bls()

    seen = set()
    level_one = balanced_take(foundation, len(foundation), seen)
    bls_core = [{**item, "level": 1} for item in bls]
    level_one += required_take(bls_core, (
        r"^Chicken meat, without skin, raw$",
        r"^Chicken meat, without skin, fried without fat \(pan\)$",
        r"^Chicken meat, without skin, grilled$",
        r"^Chicken meat, without skin, boiled$",
        r"^Chicken meat, without skin, braised without fat$",
    ), seen)
    level_one += required_take(legacy, (
        r"Chicken, broiler or fryers, breast, skinless, boneless, meat only, raw$",
        r"Chicken, broilers or fryers, breast, meat only, cooked, roasted$",
        r"Chicken, broilers or fryers, thigh, meat only, cooked, roasted$",
        r"Chicken, broilers or fryers, meat only, cooked, roasted$",
        r"Rice, white, long-grain, regular, raw",
        r"Rice, white, long-grain, regular, cooked",
        r"Buckwheat groats, roasted, dry",
        r"Buckwheat groats, roasted, cooked",
    ), seen)
    level_one += balanced_take(legacy, 1600 - len(level_one), seen)
    extended_legacy = [{**item, "level": 2} for item in legacy]
    level_two = []
    for pool in (bls, ciqual, extended_legacy, fndds):
        level_two += balanced_take(pool, len(pool), seen)

    target_total = 30000
    branded = read_branded(60000)
    remaining = max(0, target_total - len(level_one) - len(level_two))
    level_two += balanced_take(branded, remaining, seen)
    selected = sorted(level_one + level_two, key=lambda item: (item["level"], item["group"], item["subgroup"], item["name"], item["original"]))
    foods = []
    translated_seen = set()
    for item in selected:
        key = translated_display_key(item)
        if not key:
            continue
        if key in translated_seen:
            parts = [clean(part) for part in item["original"].split(",") if clean(part)]
            candidates = []
            for width in range(1, min(len(parts), 4) + 1):
                qualifier = russian_qualifier(", ".join(parts[:width]))
                if qualifier:
                    candidates.append(f'{item["name"]} — {qualifier}')
            candidates.append(f'{item["name"]} — позиция {re.sub(r"[^0-9]", "", item["id"])[-9:]}')
            for candidate in candidates:
                candidate_item = {**item, "name": candidate}
                candidate_key = translated_display_key(candidate_item)
                if candidate_key not in translated_seen:
                    item = candidate_item
                    key = candidate_key
                    break
        translated_seen.add(key)
        foods.append(item)

    compact = [[item[key] for key in ("id", "level", "group", "subgroup", "name", "original", "kcal", "protein", "fat", "carbs", "source")] for item in foods]
    payload = json.dumps(compact, ensure_ascii=False, separators=(",", ":"))
    script = (
        "/* Generated from USDA FoodData Central and Anses-CIQUAL 2025. */\n"
        "window.FOOD_DATABASE={version:\"2026-08-14\",columns:[\"id\",\"level\",\"group\",\"subgroup\",\"name\",\"original\",\"kcal\",\"protein\",\"fat\",\"carbs\",\"source\"],foods:"
        + payload + "};\n"
    )
    OUTPUT.write_text(script, encoding="utf-8")
    source_counts = defaultdict(int)
    for item in foods:
        source_counts[item["source"]] += 1
    print(json.dumps({"level1": len(level_one), "level2": len(level_two), "total": len(foods), "bytes": OUTPUT.stat().st_size, "sources": source_counts}, ensure_ascii=False))


if __name__ == "__main__":
    main()
