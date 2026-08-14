"""Incrementally translate generated catalog names with a local Argos model."""

from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path

import ctranslate2
import sentencepiece as spm


ROOT = Path(__file__).resolve().parent.parent
DATABASE = ROOT / "food-database.js"
CACHE = ROOT / "food-data-sources" / "translations-en-ru.json"
MODEL = ROOT / "food-data-sources" / ".argos-models" / "translate-en_ru-1_9"

CYRILLIC = re.compile(r"[А-Яа-яЁё]")
LATIN_WORD = re.compile(r"[A-Za-z]+")
TRANSLIT = {
    "a": "а", "b": "б", "c": "к", "d": "д", "e": "е", "f": "ф", "g": "г",
    "h": "х", "i": "и", "j": "дж", "k": "к", "l": "л", "m": "м", "n": "н",
    "o": "о", "p": "п", "q": "к", "r": "р", "s": "с", "t": "т", "u": "у",
    "v": "в", "w": "в", "x": "кс", "y": "й", "z": "з",
}


def load_rows() -> list[list]:
    text = DATABASE.read_text(encoding="utf-8")
    return json.loads(text[text.index("foods:") + len("foods:") : -3])


def load_cache() -> dict[str, str]:
    return json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}


def save_cache(cache: dict[str, str]) -> None:
    temporary = CACHE.with_suffix(".tmp")
    temporary.write_text(json.dumps(cache, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(CACHE)


def transliterate_word(match: re.Match) -> str:
    word = match.group(0)
    converted = "".join(TRANSLIT.get(char.lower(), char) for char in word)
    return converted.capitalize() if word[:1].isupper() else converted


def clean_translation(source: str, translated: str) -> str:
    result = re.sub(r"\s+", " ", translated).strip(" ,.;")
    result = LATIN_WORD.sub(transliterate_word, result)
    if not CYRILLIC.search(result):
        result = LATIN_WORD.sub(transliterate_word, source)
    if result:
        result = result[0].upper() + result[1:]
    return result or LATIN_WORD.sub(transliterate_word, source)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--batch-size", type=int, default=128)
    args = parser.parse_args()

    cache = load_cache()
    names = sorted({row[4] for row in load_rows() if not CYRILLIC.search(row[4]) and row[4] not in cache})
    if args.limit:
        names = names[: args.limit]
    if not names:
        print("No new English names to translate.")
        return

    processor = spm.SentencePieceProcessor(model_file=str(MODEL / "sentencepiece.model"))
    translator = ctranslate2.Translator(str(MODEL / "model"), device="cpu", inter_threads=4, intra_threads=3)
    started = time.monotonic()
    for offset in range(0, len(names), args.batch_size):
        batch = names[offset : offset + args.batch_size]
        prepared = [name.title() if name.isupper() else name for name in batch]
        tokens = [processor.encode(name, out_type=str) for name in prepared]
        results = translator.translate_batch(tokens, beam_size=1, max_batch_size=64, batch_type="examples", max_input_length=256)
        for source, result in zip(batch, results):
            cache[source] = clean_translation(source, processor.decode(result.hypotheses[0]))
        save_cache(cache)
        completed = min(offset + len(batch), len(names))
        elapsed = time.monotonic() - started
        print(f"Translated {completed}/{len(names)} ({completed / elapsed:.1f} names/s)", flush=True)

    print(f"Cache now contains {len(cache)} translations.")


if __name__ == "__main__":
    main()
