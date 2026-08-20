"""Build the static browser food catalog from official USDA and CIQUAL downloads."""

from __future__ import annotations

import argparse
import json
import io
import html
import re
import sys
import unicodedata
import zipfile
from collections import defaultdict
from pathlib import Path
from xml.etree import ElementTree as ET


ROOT = Path(__file__).resolve().parent
RAW = ROOT / "raw"
OUTPUT = ROOT.parent / "food-database.js"
TRANSLATIONS_FILE = ROOT / "translations-en-ru.json"
CURATED_FILE = ROOT / "curated-regional-foods.json"
TRANSLATIONS = json.loads(TRANSLATIONS_FILE.read_text(encoding="utf-8")) if TRANSLATIONS_FILE.exists() else {}
TRANSLATIONS_CASEFOLD = {key.casefold(): value for key, value in TRANSLATIONS.items()}
BLS_FILE = RAW / "bls-4.0-2025-de" / "BLS_4_0_2025_DE" / "BLS_4_0_Daten_2025_DE.xlsx"
HEALTH_DIET_FILE = RAW / "health-diet-offline-table.swf"
SCHOOL65_FILE = RAW / "school65-calorie-table.html"
SCHOOL65_URL = "https://xn--65-6kc3bfr2e.xn--80acgfbsl1azdqr.xn--p1ai/?section_id=36"
NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}

HEALTH_DIET_BLOCKED_NAMES = {
    "Грибы сушеные",  # в исходной офлайн-таблице ошибочно повторяет КБЖУ солёных грибов
    "Горошек зеленый быстрозамороженный",  # фактически указаны значения сухого зерна
    "Кукуруза, сахарная консервированная",  # фактически указаны значения сухого зерна
    "Кукуруза, свежая в початках молочной спелости",  # фактически указаны значения сухого зерна
    "Сметана, 36% жирности",  # жиры в исходной строке указаны как 30%
    "Сметана, 40% жирности",  # жиры в исходной строке указаны как 30%
    "Сыворотка подсырная",  # строка ошибочно повторяет состав кисломолочного напитка
}

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

COMPOUND_NAMES = (
    (r"\bbaby ?food\b|\bbaby meal\b", "Детское питание"),
    (r"\btofu yogurt\b", "Соевый йогурт"),
    (r"\bsoy ?milk\b.*\bchocolate\b|\bchocolate soy ?milk\b", "Шоколадное соевое молоко"),
    (r"\bsoy ?milk\b", "Соевое молоко"), (r"\balmond milk\b", "Миндальное молоко"),
    (r"\boat milk\b|\boat beverage\b", "Овсяный напиток"),
    (r"\bsoybean, curd cheese\b|\bsoybean curd cheese\b", "Тофу"),
    (r"\bmeatless.*\bfrankfurter\b|\bfrankfurter, meatless\b", "Растительная колбаса"),
    (r"\bmeatballs?, meatless\b", "Растительные фрикадельки"),
    (r"\bvegetarian meatloaf or patties\b", "Растительные котлеты"),
    (r"\bsandwich spread, meatless\b", "Растительная бутербродная паста"),
    (r"\bburritos?\b", "Буррито"), (r"\btacos?\b", "Тако"),
    (r"\blasagna\b", "Лазанья"), (r"\bravioli\b", "Равиоли"),
    (r"\benchiladas?\b", "Энчилада"), (r"\bquesadillas?\b", "Кесадилья"),
    (r"\bsundae\b", "Десерт-мороженое"), (r"\bsmoothie\b", "Смузи"),
    (r"\bmilkshake\b|\bmilk shake\b", "Молочный коктейль"),
    (r"^fast foods?, biscuit\b", "Сэндвич на булочке"),
    (r"^vegetables?.*\bfor soups?\b|^mixed vegetables?\b", "Овощная смесь"),
    (r"^stewed fruits?\b", "Тушёные фрукты"),
    (r"\boyster mushrooms?\b", "Вешенки"),
    (r"\bduck eggs?\b", "Утиные яйца"), (r"\bgoose eggs?\b", "Гусиные яйца"),
    (r"\bquail eggs?\b", "Перепелиные яйца"), (r"\bostrich eggs?\b", "Страусиные яйца"),
    (r"\bturkey eggs?\b", "Индюшиные яйца"),
    (r"\bchicken giblets?\b", "Куриные потроха"), (r"\bturkey giblets?\b", "Потроха индейки"),
    (r"\bgoose fat\b|^fat, goose\b", "Гусиный жир"), (r"\bduck fat\b|^fat, duck\b", "Утиный жир"),
    (r"\bpate\b|\bpâté\b", "Паштет"), (r"\bdressing\b", "Заправка"),
    (r"\bterrine\b", "Террин"), (r"\btarts?\b", "Тарт"),
    (r"\bgnocchi\b", "Ньокки"), (r"\bdumplings?\b", "Клёцки"),
    (r"\bpatties?\b", "Котлеты"), (r"\bdip\b", "Закуска-соус"),
    (r"\bmustard greens?\b", "Листовая горчица"), (r"\bsoybean curd\b", "Тофу"),
    (r"\bfish oil\b", "Рыбий жир"), (r"\bchili with beans\b", "Чили с фасолью"),
    (r"\begg rolls?\b", "Спринг-роллы"),
    (r"^flour, bread\b", "Хлебная мука"), (r"^flour, pastry\b", "Кондитерская мука"),
    (r"\bonion rings?\b", "Луковые кольца"), (r"^pickles?, cucumber\b", "Огурцы маринованные"),
    (r"\bpeanut butter\b|\bpeanut spread\b", "Арахисовая паста"),
    (r"\bapplesauce\b", "Яблочное пюре"), (r"\bpea puree\b", "Гороховое пюре"),
    (r"\bmashed potatoes?\b", "Картофельное пюре"), (r"\bvegetable puree\b", "Овощное пюре"),
    (r"\bradish seeds?, sprouted\b", "Ростки редиса"),
    (r"\bmacaroni and cheese\b", "Макароны с сыром"), (r"\bnachos?\b", "Начос"),
    (r"\bkielbasa\b", "Колбаса"), (r"\bbeef sticks?\b", "Мясные палочки из говядины"),
    (r"\bpotato pockets?\b", "Картофельные кармашки с начинкой"),
    (r"\bapple juice\b", "Яблочный сок"), (r"\borange juice\b", "Апельсиновый сок"),
    (r"\btangerine juice\b|\bmandarin juice\b", "Мандариновый сок"),
    (r"\bgrape juice\b", "Виноградный сок"), (r"\bpineapple juice\b", "Ананасовый сок"),
    (r"\btomato juice\b", "Томатный сок"), (r"\bcranberry juice\b", "Клюквенный сок"),
    (r"\bjuice\b", "Сок"),
    (r"\bbarley flour\b", "Ячменная мука"), (r"\bbuckwheat flour\b", "Гречневая мука"),
    (r"\boat flour\b", "Овсяная мука"), (r"\brye flour\b", "Ржаная мука"),
    (r"\bwheat flour\b", "Пшеничная мука"), (r"\brice flour\b", "Рисовая мука"),
    (r"\bcorn flour\b|\bcornmeal\b", "Кукурузная мука"),
    (r"\bsoy flour\b", "Соевая мука"), (r"\bchickpea flour\b|\bbesan\b", "Нутовая мука"),
    (r"\bsandwich\b", "Сэндвич"), (r"\bwrap\b", "Ролл с начинкой"),
    (r"\bhamburger\b|\bcheeseburger\b", "Гамбургер"), (r"\bpizza\b", "Пицца"),
    (r"\bsoup\b|\bchowder\b|\bbisque\b", "Суп"),
    (r"\bsalad\b", "Салат"), (r"\bsauce\b|\bgravy\b", "Соус"),
    (r"\bbroth\b|\bstock\b", "Бульон"), (r"\bstew\b", "Рагу"),
    (r"\bcookies?\b|\bbiscuits?\b", "Печенье"), (r"\bcrackers?\b", "Крекеры"),
    (r"\bbreadsticks?\b", "Хлебные палочки"), (r"\bpancakes?\b|\bcrepes?\b", "Блины"),
    (r"\bwaffles?\b", "Вафли"), (r"\bmuffins?\b", "Маффины"),
    (r"\bcroissants?\b", "Круассаны"), (r"\bcheesecake\b", "Чизкейк"),
    (r"\bcake\b", "Торт"), (r"\bpie\b", "Пирог"), (r"\bpastry\b", "Выпечка"),
    (r"\bdoughnuts?\b|\bdonuts?\b", "Пончики"), (r"\bcustard\b", "Заварной крем"),
    (r"\bdessert\b", "Десерт"), (r"\bpudding\b", "Пудинг"),
    (r"\bcand(?:y|ies)\b", "Конфеты"),
    (r"\bice cream\b", "Мороженое"), (r"\bpopcorn\b", "Попкорн"),
    (r"\bchips?\b|\bcrisps?\b", "Чипсы"), (r"\bpretzels?\b", "Крендельки"),
    (r"\bnuggets?\b", "Наггетсы"), (r"\bcroquettes?\b", "Крокеты"),
    (r"\bmeatballs?\b", "Мясные фрикадельки"),
    (r"\bsausage\b|\bfrankfurter\b|\bsalami\b", "Колбаса"),
)

FEMININE_BASE_NAMES = {
    "Фасоль", "Морковь", "Сельдь", "Форель", "Рыба-меч", "Гречневая крупа", "Овсяная крупа",
    "Куриная голень", "Куриная печень", "Говяжья печень",
}
PLURAL_BASE_NAMES = {
    "Макаронные изделия", "Креветки", "Мидии", "Устрицы", "Морские гребешки", "Грибы",
    "Соевые бобы", "Семена подсолнечника",
}
MASCULINE_BASE_NAMES = {"Киви"}

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
    (r"\begg", "Яйцо"), (r"burbot", "Налим"), (r"bluefish", "Луфарь"),
    (r"catfish", "Сом"), (r"flounder|\bsole\b|flatfish", "Камбала"),
    (r"grouper", "Групер"), (r"halibut", "Палтус"), (r"mahimahi", "Корифена"),
    (r"monkfish", "Морской чёрт"), (r"ocean pout", "Бельдюга"),
    (r"rockfish", "Морской окунь"), (r"sablefish", "Чёрная треска"),
    (r"sea bass", "Морской окунь"), (r"snapper", "Луциан"),
    (r"swordfish", "Рыба-меч"), (r"tilefish", "Кафельник"),
    (r"whitefish", "Сиг"), (r"whiting", "Мерланг"), (r"wolffish", "Зубатка"),
    (r"yellowtail", "Желтохвост"), (r"turbot", "Тюрбо"), (r"sturgeon", "Осётр"),
    (r"mackerel", "Скумбрия"), (r"perch", "Окунь"), (r"\bpike\b", "Щука"),
    (r"pollock", "Минтай"), (r"tilapia", "Тилапия"), (r"trout", "Форель"),
    (r"salmon", "Лосось"), (r"tuna", "Тунец"), (r"\bcod\b", "Треска"),
    (r"herring", "Сельдь"), (r"shrimp|prawn", "Креветки"),
    (r"mussel", "Мидии"), (r"oyster", "Устрицы"), (r"scallop", "Морские гребешки"),
    (r"squid|calamari", "Кальмар"), (r"lobster", "Омар"), (r"\bcrab\b", "Краб"),
    (r"\bduck\b", "Утка"), (r"\bgoose\b", "Гусь"), (r"rabbit", "Кролик"),
    (r"\bostrich\b", "Страус"), (r"\bemu\b", "Эму"),
    (r"potato", "Картофель"), (r"tomato", "Помидор"), (r"cucumber", "Огурец"),
    (r"carrot", "Морковь"), (r"cabbage", "Капуста"), (r"broccoli", "Брокколи"),
    (r"cauliflower", "Цветная капуста"), (r"zucchini|courgette", "Кабачок"),
    (r"pumpkin", "Тыква"), (r"eggplant|aubergine", "Баклажан"),
    (r"beet", "Свёкла"), (r"radish", "Редис"), (r"turnip", "Репа"),
    (r"celery", "Сельдерей"), (r"lettuce", "Салат латук"), (r"mushroom", "Грибы"),
    (r"bell pepper|sweet pepper", "Сладкий перец"), (r"spinach", "Шпинат"),
    (r"onion|chive|spring onion", "Лук"), (r"garlic", "Чеснок"),
    (r"\bapple", "Яблоко"), (r"\bbanana", "Банан"), (r"\borange", "Апельсин"),
    (r"pear", "Груша"), (r"strawberr", "Клубника"), (r"blueberr", "Черника"),
    (r"raspberr", "Малина"), (r"grape", "Виноград"), (r"peach", "Персик"),
    (r"apricot", "Абрикос"), (r"nectarine", "Нектарин"), (r"plum", "Слива"),
    (r"sweet cherr|sour cherr|\bcherr", "Вишня"), (r"watermelon", "Арбуз"),
    (r"\bmelon\b|cantaloupe", "Дыня"), (r"pineapple", "Ананас"), (r"mango", "Манго"),
    (r"kiwi", "Киви"), (r"pomegranate", "Гранат"), (r"cranberr", "Клюква"),
    (r"currant", "Смородина"), (r"grapefruit", "Грейпфрут"),
    (r"tangerine|mandarin", "Мандарин"), (r"lemon", "Лимон"),
    (r"lentil", "Чечевица"), (r"chickpea", "Нут"), (r"\bbeans?\b", "Фасоль"),
    (r"soybeans?", "Соевые бобы"), (r"\bpeas?\b", "Горох"),
    (r"walnut", "Грецкий орех"), (r"almond", "Миндаль"), (r"cashew", "Кешью"), (r"peanut", "Арахис"),
    (r"sunflower seed", "Семена подсолнечника"), (r"olive oil", "Оливковое масло"),
    (r"sunflower oil", "Подсолнечное масло"), (r"\bbutter\b", "Сливочное масло"),
    (r"whole.?wheat bread|wholemeal bread", "Хлеб цельнозерновой"), (r"rye bread", "Хлеб ржаной"),
    (r"white bread", "Хлеб белый"), (r"\bbread\b", "Хлеб"),
    (r"sugar-coated almonds", "Миндаль в сахарной глазури"),
    (r"candies?, fudge, vanilla with nuts", "Сливочная помадка с ванилью и орехами"),
    (r"chocolate", "Шоколад"), (r"ice cream", "Мороженое"),
    (r"coffee", "Кофе"), (r"\btea\b", "Чай"), (r"^water\b|drinking water|mineral water", "Вода"),
    (r"\bjuice\b", "Сок"), (r"mayonnaise", "Майонез"), (r"ketchup", "Кетчуп"),
    (r"mustard", "Горчица"), (r"\bhoney\b", "Мёд"), (r"\bsugar\b", "Сахар"),
    (r"soup", "Суп"), (r"pizza", "Пицца"), (r"salad", "Салат"),
)

STATES = (
    (r"\braw\b|uncooked", ("сырой", "сырая", "сырое", "сырые")),
    (r"\bdry\b(?!\s+heat)|\bdried\b", ("сухой", "сухая", "сухое", "сухие")),
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
    (r"\bcooked\b|\bprepared\b|dry heat", ("приготовленный", "приготовленная", "приготовленное", "приготовленные")),
)

NAME_OVERRIDES = {
    "Candies, fudge, vanilla with nuts": "Сливочная помадка с ванилью и орехами",
    "Candies, sugar-coated almonds": "Миндаль в сахарной глазури",
    "Candies, nougat, with almonds": "Нуга с миндалём",
    "Candied almond or praline": "Миндаль в сахаре или пралине",
    "Oil, apricot kernel": "Абрикосовое масло",
    "Oil, mustard": "Горчичное масло",
    "Oil, oat": "Овсяное масло",
    "Oil, tomatoseed": "Масло из семян томата",
    "Oil, soybean lecithin": "Масло соевого лецитина",
    "Goose lard/fat": "Гусиный жир",
}

FORBIDDEN_NAME_FRAGMENTS = (
    "мухляк", "сухая жара", "позиция ", "нфс", "не указано далее", "доказательств",
    "бреад", "препаред", "фром", "кукед", "роастед", "фрайд", "дрй", "микс",
    "чоколате", "кандиес", "алмондс", "сагар", "сугар", "филлед", "икинг",
    "валнутс", "пеанут", "фоод", "хомемаде", "рекипе", "вит ", " анд ",
)

ALLOWED_TRANSLITERATED_WORDS = {
    "pizza", "pasta", "yogurt", "kefir", "tofu", "miso", "tempeh", "hummus",
    "falafel", "curry", "burger", "risotto", "lasagna", "taco", "burrito", "sushi",
    "mozzarella", "ricotta", "feta", "brie", "camembert", "gouda", "parmesan",
    "cheddar", "quinoa", "papad", "natto", "ramen", "kimchi", "granola",
}

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
    if original in NAME_OVERRIDES:
        return NAME_OVERRIDES[original]
    lower = original.lower()
    if re.search(r"mock chicken|chicken flavored", lower):
        return ""
    compound = next((ru for pattern, ru in COMPOUND_NAMES if re.search(pattern, lower)), "")
    if compound:
        return compound
    base_matches = []
    for priority, (pattern, russian) in enumerate(BASE_NAMES):
        match = re.search(pattern, lower)
        if match:
            base_matches.append((match.start(), priority, russian))
    first_match = min(base_matches) if base_matches else None
    base = first_match[2] if first_match and first_match[0] <= 14 else ""
    if base == "Курица" and re.search(
        r"meatless|pot pie|salad|sandwich|soup|stock|broth|gravy|spread|patty|sausage|gumbo|giblets?|\bfeet\b|babyfood|^potatoes?\b|\bpesto\b|\bsauce\b|sweet and sour|bologna",
        lower,
    ):
        base = ""
    if not base:
        return ""
    last_word = base.split()[-1].lower()
    form = 0 if base in MASCULINE_BASE_NAMES or base.startswith("Сыр ") else 3 if base in PLURAL_BASE_NAMES else 1 if base in FEMININE_BASE_NAMES else 3 if last_word.endswith(("ы", "и")) and not last_word.endswith("ия") else 2 if last_word.endswith(("о", "е")) else 1 if last_word.endswith(("а", "я")) else 0
    states = []
    cooking_state_added = False
    preservation_state_added = False
    has_cooked_state = bool(re.search(r"boiled|steamed|baked|roasted|fried|grilled|smoked|braised|stewed|\bcooked\b|\bprepared\b", lower))
    for pattern, forms in STATES:
        if pattern == r"\braw\b|uncooked" and has_cooked_state:
            continue
        if "cooked" in pattern and states:
            continue
        is_cooking_state = bool(re.search(r"raw|boiled|steamed|baked|roasted|fried|grilled|smoked|braised|stewed|cooked|prepared", pattern))
        is_preservation_state = bool(re.search(r"dry|dried|canned|frozen|fresh|dehydrated", pattern))
        if is_cooking_state and cooking_state_added:
            continue
        if is_preservation_state and preservation_state_added:
            continue
        label = forms[form]
        if re.search(pattern, lower) and label not in states:
            states.append(label)
            cooking_state_added = cooking_state_added or is_cooking_state
            preservation_state_added = preservation_state_added or is_preservation_state
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
    return re.sub(r"[^a-zа-яё0-9]+", " ", item["original"].lower()).strip()


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


def contains_transliterated_english(name, original):
    russian_tokens = set(re.findall(r"[а-яё]+", name.casefold()))
    for word in re.findall(r"[a-z]+", original.casefold()):
        if len(word) < 4 or word in ALLOWED_TRANSLITERATED_WORDS:
            continue
        transliterated = russian_qualifier(word).casefold()
        if len(transliterated) >= 4 and transliterated in russian_tokens:
            return True
    return False


def valid_macros(item):
    values = [item[key] for key in ("kcal", "protein", "fat", "carbs")]
    if any(value is None or not isinstance(value, (int, float)) for value in values):
        return False
    kcal, protein, fat, carbs = values
    if not (0 <= kcal <= 950 and 0 <= protein <= 100 and 0 <= fat <= 100 and 0 <= carbs <= 100):
        return False
    if protein + fat + carbs > 105:
        return False
    if item.get("source") == "МЗР — официальная офлайн-таблица" and item.get("subgroup") == "Алкогольные напитки":
        return True
    calculated = protein * 4 + fat * 9 + carbs * 4
    if calculated == 0 and kcal > 20:
        return False
    return abs(kcal - calculated) <= max(170, kcal * 0.55)


def quality_item(item):
    name = clean(item.get("name"))
    if not name or len(name) > 110 or not re.search(r"[а-яё]", name, re.IGNORECASE):
        return False
    if re.search(r"[a-z]", name, re.IGNORECASE):
        return False
    lowered = f" {name.casefold()} "
    if any(fragment in lowered for fragment in FORBIDDEN_NAME_FRAGMENTS):
        return False
    if contains_transliterated_english(name, item.get("original", "")):
        return False
    return valid_macros(item)


def read_curated():
    if not CURATED_FILE.exists():
        return []
    entries = json.loads(CURATED_FILE.read_text(encoding="utf-8"))
    result = []
    for index, entry in enumerate(entries, 1):
        protein = round(float(entry["protein"]), 2)
        fat = round(float(entry["fat"]), 2)
        carbs = round(float(entry["carbs"]), 2)
        kcal = round(protein * 4 + fat * 9 + carbs * 4)
        aliases = clean(entry.get("aliases", ""))
        original = entry["name"] if not aliases else f'{entry["name"]}; {aliases}'
        result.append({
            "id": f"regional-{index:03d}", "level": 2,
            "group": entry["group"], "subgroup": entry["subgroup"],
            "name": entry["name"], "original": original,
            "kcal": kcal, "protein": protein, "fat": fat, "carbs": carbs,
            "source": "Среднее по типовой рецептуре",
        })
    return result


def health_diet_group(category):
    lowered = category.casefold().replace("ё", "е")
    rules = (
        (("алкоголь", "вода", "сок", "компот", "экстракт"), "Напитки"),
        (("мяс", "птиц", "субпродукт", "колбас", "сардель", "сосиск", "полуфабрикат"), "Мясо и птица"),
        (("рыб", "икра", "нерыбных объектов"), "Рыба и морепродукты"),
        (("молоч", "сыр", "морожен"), "Молочные продукты и яйца"),
        (("круп", "зерн", "макарон", "мука", "крахмал"), "Крупы и макароны"),
        (("хлеб",), "Хлеб и выпечка"),
        (("овощ", "трава", "гриб"), "Овощи"),
        (("фрукт", "ягод"), "Фрукты и ягоды"),
        (("орех", "семен"), "Орехи и семена"),
        (("зернобоб",), "Бобовые"),
        (("жир", "масло"), "Масла и жиры"),
        (("кондитер", "варенье"), "Сладости"),
        (("специ", "приправ", "соус", "сырье", "сырьё"), "Специи и травы"),
    )
    for markers, group in rules:
        if any(marker in lowered for marker in markers):
            return group
    return "Прочие продукты"


def health_diet_level(category):
    lowered = category.casefold().replace("ё", "е")
    extended_markers = (
        "варенье", "консерв", "пресерв", "полуфабрикат", "колбас", "сардель", "сосиск",
        "кондитер", "морожен", "копчен", "солен", "вялен", "компот", "соус", "напитк",
    )
    return 2 if any(marker in lowered for marker in extended_markers) else 1


def health_diet_name(value):
    value = html.unescape(clean(value))
    replacements = {
        "Ликер Вишневый»": "Ликёр «Вишнёвый»",
        "сгущёном": "сгущённом",
        "сгущеном": "сгущённом",
        "Подберезовик": "Подберёзовик",
        "Подберезовики": "Подберёзовики",
        "топленое": "топлёное",
        "соленый": "солёный",
        "соленая": "солёная",
        "соленое": "солёное",
        "сушеный": "сушёный",
        "сушеная": "сушёная",
        "сушеное": "сушёное",
    }
    for source, target in replacements.items():
        value = value.replace(source, target)
    value = {
        "Абрикосы": "Абрикос",
        "Баклажаны": "Баклажан",
        "Яблоки": "Яблоко",
    }.get(value, value)
    return value


def read_health_diet_offline():
    """Read the official downloadable offline table without executing its Flash projector."""
    if not HEALTH_DIET_FILE.exists():
        return []
    text = HEALTH_DIET_FILE.read_bytes().decode("utf-8", errors="ignore")
    category_pattern = re.compile(
        r"<node label='(?P<category>[^']+)'>(?P<items>(?:<node label='[^']+' k='[^']*' b='[^']*' f='[^']*' u='[^']*'></node>)+)</node>"
    )
    item_pattern = re.compile(
        r"<node label='(?P<name>[^']+)' k='(?P<kcal>[^']*)' b='(?P<protein>[^']*)' f='(?P<fat>[^']*)' u='(?P<carbs>[^']*)'></node>"
    )
    result = []
    index = 0
    for category_match in category_pattern.finditer(text):
        category = health_diet_name(category_match.group("category"))
        group = health_diet_group(category)
        level = health_diet_level(category)
        for match in item_pattern.finditer(category_match.group("items")):
            index += 1
            raw_name = html.unescape(clean(match.group("name")))
            name = health_diet_name(raw_name)
            if raw_name in HEALTH_DIET_BLOCKED_NAMES or not match.group("kcal"):
                continue
            try:
                kcal = round(float(match.group("kcal")), 2)
                protein = round(float(match.group("protein") or 0), 2)
                fat = round(float(match.group("fat") or 0), 2)
                carbs = round(float(match.group("carbs") or 0), 2)
            except ValueError:
                continue
            if not (0 <= kcal <= 950 and 0 <= protein <= 100 and 0 <= fat <= 100 and 0 <= carbs <= 100):
                continue
            if protein + fat + carbs > 105:
                continue
            calculated = protein * 4 + fat * 9 + carbs * 4
            is_alcohol = "алкоголь" in category.casefold() or any(
                marker in name.casefold() for marker in ("водка", "коньяк", "вино", "ликёр", "наливка", "настойка", "пиво")
            )
            if not is_alcohol and abs(kcal - calculated) > max(70, kcal * 0.30):
                continue
            result.append({
                "id": f"health-diet-{index:04d}", "level": level,
                "group": group, "subgroup": category,
                "name": name, "original": name,
                "kcal": kcal, "protein": protein, "fat": fat, "carbs": carbs,
                "source": "МЗР — официальная офлайн-таблица",
            })
    return result


SCHOOL65_GROUPS = {
    "Напитки": "Напитки",
    "Грибы": "Овощи",
    "Икра": "Рыба и морепродукты",
    "Каши": "Крупы и макароны",
    "Колбаса и колбасные изделия": "Мясо и птица",
    "Масло, маргарин, жиры": "Масла и жиры",
    "Молочные продукты": "Молочные продукты и яйца",
    "Мясо, птица": "Мясо и птица",
    "Овощи": "Овощи",
    "Орехи, сухофрукты": "Орехи и семена",
    "Рыба и морепродукты": "Рыба и морепродукты",
    "Сладости": "Сладости",
    "Фрукты и ягоды": "Фрукты и ягоды",
    "Хлеб и хлебобулочные изделия, мука": "Хлеб и выпечка",
    "Яйца": "Молочные продукты и яйца",
}

SCHOOL65_BLOCKED_NAMES = {
    # Название не объясняет, что приведены значения сухого порошка, а не напитка.
    "Какао на молоке",
    # Углеводы и калорийность противоречат типичному сладкому сгущённому молоку.
    "Молоко сгущенное",
    # Явные опечатки в строках: БЖУ не соответствуют продукту или калорийности.
    "Крабовые палочки",
    "Печенье сдобное",
    "Плотва",
    # Русский синоним скумбрии уже есть в старом каталоге отдельной строкой.
    "Макрель",
}


def school65_name(value, category):
    value = html.unescape(re.sub(r"<[^>]+>", " ", value))
    value = clean(value).replace("&nbsp;", " ")
    replacements = {
        "Вишневый": "Вишнёвый", "сушеные": "сушёные", "Сушеные": "Сушёные",
        "сушеный": "сушёный", "сушенные": "сушёные",
        "вареный": "варёный", "Вареный": "Варёный", "вареная": "варёная",
        "вареные": "варёные", "копченая": "копчёная", "копченые": "копчёные",
        "топленое": "топлёное", "топленый": "топлёный", "сгущенное": "сгущённое",
        "сгущенным": "сгущённым", "слоеное": "слоёное", "Зеленый": "Зелёный",
        "зеленый": "зелёный", "Черный": "Чёрный", "черная": "чёрная",
        "темный": "тёмный",
        "Подберезовики": "Подберёзовики", "Красноперка": "Краснопёрка",
        "Осетр": "Осётр", "Семга": "Сёмга", "Мед": "Мёд",
    }
    for source, target in replacements.items():
        value = value.replace(source, target)
    exact = {
        "Белые свежие": "Белые грибы, свежие",
        "Белые сушёные": "Белые грибы, сушёные",
        "Ледяная": "Ледяная рыба",
        "Куры": "Курица",
        "Гуси": "Гусь",
        "Утки": "Утка",
        "Цыплята": "Цыплёнок",
        "Абрикосы": "Абрикос",
        "Баклажаны": "Баклажан",
        "Бананы": "Банан",
        "Кабачки": "Кабачок",
        "Яблоки": "Яблоко",
        "Памело": "Помело",
        "Кровянка": "Кровяная колбаса",
        "Салат": "Салат листовой",
        "Фасоль": "Фасоль стручковая",
        "Бобы": "Бобы свежие",
    }
    value = exact.get(value, value)
    value = re.sub(
        r"(?<=\s)(Почки|Печень|Сердце|Мозги|Вымя|Язык|Говяжьи|Свиные|Куриные|Любительские|Молочные)(?=\b)",
        lambda match: match.group(0).lower(),
        value,
    )
    if category == "Грибы" and value in {"Волнушки", "Лисички", "Маслята", "Опята", "Рыжики"}:
        value += ", грибы"
    return value


def read_school65_table():
    """Read the school table whose footer permits reuse with an active source link."""
    if not SCHOOL65_FILE.exists():
        return []
    text = SCHOOL65_FILE.read_text(encoding="utf-8", errors="ignore")
    section_pattern = re.compile(
        r"<h3[^>]*>(?P<category>.*?)</h3>\s*<table[^>]*>(?P<table>.*?)</table>",
        re.IGNORECASE | re.DOTALL,
    )
    row_pattern = re.compile(r"<tr[^>]*>(?P<row>.*?)</tr>", re.IGNORECASE | re.DOTALL)
    cell_pattern = re.compile(r"<t[dh][^>]*>(?P<cell>.*?)</t[dh]>", re.IGNORECASE | re.DOTALL)
    result = []
    index = 0
    for section in section_pattern.finditer(text):
        category = html.unescape(re.sub(r"<[^>]+>", " ", section.group("category")))
        category = clean(category)
        group = SCHOOL65_GROUPS.get(category)
        if not group:
            continue
        for row in row_pattern.finditer(section.group("table")):
            cells = [html.unescape(re.sub(r"<[^>]+>", " ", match.group("cell"))) for match in cell_pattern.finditer(row.group("row"))]
            if len(cells) != 5 or clean(cells[0]).casefold() == "продукт":
                continue
            index += 1
            raw_name = clean(cells[0])
            if raw_name in SCHOOL65_BLOCKED_NAMES:
                continue
            values = [number(cell) for cell in cells[1:]]
            if any(value is None for value in values):
                continue
            protein, fat, carbs, kcal = (round(value, 2) for value in values)
            if not (0 <= kcal <= 950 and 0 <= protein <= 100 and 0 <= fat <= 100 and 0 <= carbs <= 100):
                continue
            if protein + fat + carbs > 105:
                continue
            calculated = protein * 4 + fat * 9 + carbs * 4
            # Этот небольшой справочник содержит несколько явных опечаток. Для нового
            # источника используем более строгую проверку, чем для больших официальных баз.
            if calculated == 0 and kcal > 10:
                continue
            if abs(kcal - calculated) > max(35, kcal * 0.18):
                continue
            name = school65_name(raw_name, category)
            result.append({
                "id": f"school65-{index:03d}", "level": 2,
                "group": group, "subgroup": category,
                "name": name, "original": raw_name,
                "kcal": kcal, "protein": protein, "fat": fat, "carbs": carbs,
                "source": "Школа №65 — таблица калорийности",
                "sourceUrl": SCHOOL65_URL,
            })
    return result


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


def main(public_build=False, output_path=OUTPUT):
    raw_foundation = read_usda("usda-foundation-2026-04-30-json.zip", "FoundationFoods", "USDA Foundation", 1)
    raw_legacy = read_usda("usda-sr-legacy-2018-04-json.zip", "SRLegacyFoods", "USDA SR", 1)
    raw_fndds = read_usda("usda-fndds-2021-2023-json.zip", "SurveyFoods", "USDA FNDDS", 2)
    raw_ciqual = read_ciqual()
    raw_bls = read_bls()
    # Even for a public build the local MZR table may participate in collision
    # filtering. Its rows are removed from the final payload below, so public
    # output cannot expose them or resurrect a lower-priority dubious value.
    raw_health_diet = read_health_diet_offline()
    raw_school65 = read_school65_table()

    foundation = [item for item in raw_foundation if quality_item(item)]
    legacy = [item for item in raw_legacy if quality_item(item)]
    legacy = [item for item in legacy if not re.search(r"\b[A-Z]{3,}\b", item["original"])]
    fndds = [item for item in raw_fndds if quality_item(item)]
    ciqual = [item for item in raw_ciqual if quality_item(item)]
    bls = [item for item in raw_bls if quality_item(item)]
    curated = [item for item in read_curated() if quality_item(item)]
    health_diet = [item for item in raw_health_diet if quality_item(item)]
    school65 = [item for item in raw_school65 if quality_item(item)]

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
    level_two = curated[:]
    for pool in (bls, ciqual, extended_legacy, fndds):
        level_two += balanced_take(pool, len(pool), seen)
    # Existing local records keep priority. The newly added school table is appended
    # last, so a colliding normalized Russian display name can never replace one.
    selected = health_diet + level_one + level_two + school65
    foods = []
    translated_seen = set()
    for item in selected:
        key = translated_display_key(item)
        if not key:
            continue
        if key in translated_seen:
            continue
        translated_seen.add(key)
        foods.append(item)
    if public_build:
        foods = [item for item in foods if item["source"] != "МЗР — официальная офлайн-таблица"]
    foods.sort(key=lambda item: (item["level"], item["group"], item["subgroup"], item["name"], item["original"]))

    columns = ("id", "level", "group", "subgroup", "name", "original", "kcal", "protein", "fat", "carbs", "source", "sourceUrl")
    compact = [[item.get(key, "") for key in columns] for item in foods]
    payload = json.dumps(compact, ensure_ascii=False, separators=(",", ":"))
    local_notice = " LOCAL-ONLY: includes the MZR offline table; do not publish this generated file."
    database_version = "2026-08-20-local-mzr-school65" if health_diet and school65 else "2026-08-20-local-mzr" if health_diet else "2026-08-20-public-school65" if school65 else "2026-08-20-public"
    script = (
        "/* Generated from USDA FoodData Central, Anses-CIQUAL 2025, BLS 4.0 and curated regional recipes."
        + (local_notice if health_diet and not public_build else "") + " */\n"
        "window.FOOD_DATABASE={version:" + json.dumps(database_version) + ",columns:" + json.dumps(columns, ensure_ascii=False, separators=(",", ":")) + ",foods:"
        + payload + "};\n"
    )
    output_path = Path(output_path).resolve()
    output_path.write_text(script, encoding="utf-8")
    source_counts = defaultdict(int)
    for item in foods:
        source_counts[item["source"]] += 1
    rejected = {
        "USDA Foundation": len(raw_foundation) - len(foundation),
        "USDA SR": len(raw_legacy) - len(legacy),
        "USDA FNDDS": len(raw_fndds) - len(fndds),
        "CIQUAL": len(raw_ciqual) - len(ciqual),
        "BLS 4.0": len(raw_bls) - len(bls),
        "МЗР — официальная офлайн-таблица": len(raw_health_diet) - len(health_diet),
        "Школа №65 — таблица калорийности": len(raw_school65) - len(school65),
    }
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps({"level1": sum(item["level"] == 1 for item in foods), "level2": sum(item["level"] == 2 for item in foods), "total": len(foods), "bytes": output_path.stat().st_size, "output": str(output_path), "public": public_build, "sources": source_counts, "rejected": rejected}, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--public", action="store_true", help="Exclude sources without confirmed redistribution rights")
    parser.add_argument("--output", type=Path, default=OUTPUT, help="Generated JavaScript file")
    arguments = parser.parse_args()
    main(public_build=arguments.public, output_path=arguments.output)
