"""Fail-fast quality audit for the generated browser food catalog."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
DATABASE = ROOT / "food-database.js"
COLUMNS = ("id", "level", "group", "subgroup", "name", "original", "kcal", "protein", "fat", "carbs", "source", "sourceUrl")
SCHOOL65_SOURCE = "Школа №65 — таблица калорийности"
SCHOOL65_URL = "https://xn--65-6kc3bfr2e.xn--80acgfbsl1azdqr.xn--p1ai/?section_id=36"
BLOCKED_FRAGMENTS = (
    "бурбон", "мухляк", "сухая жара", "кандиес", "алмондс", "бреад", "препаред",
    "фром", "кукед", "роастед", "чоколате", "сугар", "сагар", "филлед", "икинг",
    "позиция ", "нфс", "не указано далее",
)
REQUIRED_NAMES = {
    "Налим, сырой", "Налим, приготовленный", "Миндаль в сахарной глазури",
    "Сливочная помадка с ванилью и орехами", "Куриная грудка, сырая",
    "Куриное бедро, запечённое", "Куриное крыло, жареное",
    "Борщ с говядиной", "Пельмени с говядиной и свининой, отварные",
    "Шаурма с курицей", "Шаурма с говядиной", "Шаурма с бараниной",
    "Шашлык из куриной грудки", "Шашлык из свиной шеи", "Шашлык из баранины",
    "Хинкали с говядиной и свининой", "Хачапури по-аджарски", "Лобио из красной фасоли",
}
SEMANTIC_RULES = (
    (r"oyster mushrooms?", "Вешенки"),
    (r"(?:potatoes?|potato), scalloped", "Картоф"),
    (r"turkey eggs?", "Индюшин"),
    (r"duck eggs?", "Утин"),
    (r"fish oil", "Рыбий жир"),
    (r"macaroni and cheese", "Макароны с сыром"),
    (r"peanut butter|peanut spread", "Арахисовая паста"),
    (r"onion rings?", "Луковые кольца"),
    (r"pickles?, cucumber", "Огурцы маринованные"),
    (r"mustard greens?", "Листовая горчица"),
    (r"soybean curd", "Тофу"),
    (r"scrapple", ""),
)


def load_items(database=DATABASE) -> list[dict]:
    text = Path(database).read_text(encoding="utf-8")
    if "foods:" not in text or not text.endswith("};\n"):
        raise AssertionError("Неверный формат food-database.js")
    rows = json.loads(text[text.index("foods:") + len("foods:") : -3])
    return [dict(zip(COLUMNS, row, strict=True)) for row in rows]


def display_key(name: str) -> str:
    return re.sub(r"[^а-яё0-9]+", " ", name.casefold()).replace("ё", "е").strip()


def main(database=DATABASE) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    items = load_items(database)
    errors = []
    if len(items) < 1000:
        errors.append(f"Каталог слишком мал: {len(items)}")

    keys = Counter(display_key(item["name"]) for item in items)
    duplicate_count = sum(count - 1 for count in keys.values() if count > 1)
    if duplicate_count:
        errors.append(f"Повторяющихся русских названий: {duplicate_count}")

    for item in items:
        name = item["name"]
        lowered = name.casefold()
        if not re.search(r"[а-яё]", name, re.IGNORECASE) or re.search(r"[a-z]", name, re.IGNORECASE):
            errors.append(f"Название не полностью русское: {name}")
        if any(fragment in lowered for fragment in BLOCKED_FRAGMENTS):
            errors.append(f"Мусорный перевод: {name}")
        kcal, protein, fat, carbs = (item[key] for key in ("kcal", "protein", "fat", "carbs"))
        if not (0 <= kcal <= 950 and 0 <= protein <= 100 and 0 <= fat <= 100 and 0 <= carbs <= 100):
            errors.append(f"КБЖУ вне диапазона: {name}")
        if protein + fat + carbs > 105:
            errors.append(f"Сумма макронутриентов выше 105 г: {name}")
        calculated = protein * 4 + fat * 9 + carbs * 4
        is_mzr_alcohol = item["source"] == "МЗР — официальная офлайн-таблица" and item["subgroup"] == "Алкогольные напитки"
        if not is_mzr_alcohol and abs(kcal - calculated) > max(170, kcal * 0.55):
            errors.append(f"Энергия не согласуется с БЖУ: {name}")
        if item["source"] == "USDA Branded":
            errors.append(f"Брендовая позиция не должна попадать в каталог: {name}")
        if item["source"] == SCHOOL65_SOURCE:
            if item["sourceUrl"] != SCHOOL65_URL:
                errors.append(f"Нет обязательной ссылки на источник: {name}")
            if abs(kcal - calculated) > max(35, kcal * 0.18):
                errors.append(f"Строка школы №65 не прошла строгую проверку энергии: {name}")

        original = item["original"]
        for pattern, expected in SEMANTIC_RULES:
            if re.search(pattern, original, re.IGNORECASE):
                if not expected:
                    errors.append(f"Запрещённое ложное совпадение: {name} <- {original}")
                elif expected.casefold() not in name.casefold():
                    errors.append(f"Смысловое несовпадение: {name} <- {original}; ожидалось {expected}")

    names = {item["name"] for item in items}
    missing = sorted(REQUIRED_NAMES - names)
    if missing:
        errors.append("Нет обязательных позиций: " + ", ".join(missing))

    group_counts = Counter(item["group"] for item in items)
    if group_counts["Русская кухня"] < 40 or group_counts["Кавказская кухня"] < 40:
        errors.append(f"Недостаточно региональных блюд: {dict(group_counts)}")

    # Контроль явного пересечения: новая таблица не должна заменять старую строку.
    chicken = next((item for item in items if item["name"] == "Курица"), None)
    if not chicken or chicken["source"] == SCHOOL65_SOURCE:
        errors.append("Новый источник заменил существующую позицию «Курица»")

    if errors:
        print("AUDIT FAILED")
        for error in errors[:100]:
            print("-", error)
        raise SystemExit(1)

    source_counts = Counter(item["source"] for item in items)
    print(json.dumps({
        "status": "OK", "total": len(items), "level1": sum(item["level"] == 1 for item in items),
        "level2": sum(item["level"] == 2 for item in items), "duplicates": duplicate_count,
        "groups": group_counts, "sources": source_counts,
    }, ensure_ascii=False, default=dict))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DATABASE, help="Generated food-database.js to audit")
    arguments = parser.parse_args()
    main(arguments.database)
