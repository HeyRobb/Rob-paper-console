from collections import defaultdict
import csv
import os
import random

import pytest
from unittest.mock import MagicMock, patch
from PIL import Image, ImageChops, ImageFont
from app.modules.crossword import (
    ANSWER_BLOCK_MARGIN_DOTS,
    MAX_NUM_WORDS,
    MIN_NUM_WORDS,
    CrosswordGenerator,
    _clamp_num_words,
    _printer_text_font,
    execute_crossword,
    render_text_block,
)

def test_crossword_generator_initialization():
    """Verify the generator initializes with correct dimensions."""
    gen = CrosswordGenerator(8, 60)
    assert gen.width == 8
    assert gen.height == 60
    assert len(gen.grid) == 60
    assert len(gen.grid[0]) == 8

def test_crossword_can_place_logic():
    """Test the overlap and adjacency logic for word placement."""
    gen = CrosswordGenerator(10, 10)
    
    # Place initial word
    gen.place("CAT", "Feline", 0, 0, "across", 1)
    
    # Valid intersection (shares 'A' at grid[0][1], which is x=1, y=0)
    assert gen.can_place("ACT", 1, 0, "down") is True
    
    # Edge Case: Invalid overlap (conflicting letter)
    assert gen.can_place("DOG", 0, 0, "down") is False
    
    # Edge Case: Invalid adjacency (too close to existing word)
    assert gen.can_place("DOG", 0, 1, "across") is False

def test_crossword_bounds_checks():
    """Verify words cannot be placed outside grid dimensions or at negative coordinates."""
    gen = CrosswordGenerator(8, 8)
    # Fits exactly across
    assert gen.can_place("ABCDEFGH", 0, 0, "across") is True
    # Too long across
    assert gen.can_place("ABCDEFGHI", 0, 0, "across") is False
    # Negative start
    assert gen.can_place("CAT", -1, 0, "across") is False
    assert gen.can_place("CAT", 0, -1, "across") is False
    # Fits exactly down
    assert gen.can_place("ABCDEFGH", 0, 0, "down") is True
    # Too long down
    assert gen.can_place("ABCDEFGHI", 0, 0, "down") is False

def test_crossword_adjacency_blocking():
    """Ensure words aren't placed immediately adjacent to others if it would create invalid letter clusters."""
    gen = CrosswordGenerator(10, 10)
    # Place a word in the middle
    gen.place("CAT", "Feline", 2, 2, "across", 1)
    
    # Parallel word directly above should fail
    assert gen.can_place("DOG", 2, 1, "across") is False
    # Parallel word directly below should fail
    assert gen.can_place("DOG", 2, 3, "across") is False
    # Word starting exactly where another ends should fail
    assert gen.can_place("BAT", 5, 2, "across") is False
    # Word ending exactly where another starts should fail
    assert gen.can_place("BAT", 0, 2, "across") is False

def test_crossword_overlap_calculation():
    """Verify the overlap percentage math: (sum of lengths - unique cells) / sum of lengths."""
    gen = CrosswordGenerator(8, 8)
    # Two words, 0 overlap
    gen.place("CAT", "Hint", 0, 0, "across", 1)
    gen.place("DOG", "Hint", 0, 2, "across", 2)
    assert gen._calculate_overlap() == 0.0

    # Clear and try an actual intersection
    gen = CrosswordGenerator(8, 8)
    gen.place("CAT", "Hint", 0, 0, "across", 1)
    gen.place("ACT", "Hint", 1, 0, "down", 2)
    # C A T (3 cells)
    #   C
    #   T   (A is shared, so total unique cells = 5. Total word length = 6)
    # (6 - 5) / 6 = 1/6
    assert gen._calculate_overlap() == pytest.approx(1/6)

def test_crossword_scoring_logic():
    """Test the puzzle scoring algorithm components."""
    gen = CrosswordGenerator(8, 8)
    # score = (num_words * 1000) + (overlap * 500) - (height * 10)
    
    # 5 words, 20% overlap, 10 height
    # 5000 + 100 - 100 = 5000
    score1 = gen._calculate_puzzle_score(5, 0.2, 10)
    assert score1 == 5000

    # 5 words, 50% overlap, 15 height
    # 5000 + 250 - 150 = 5100 (Higher overlap beats lower height penalty)
    score2 = gen._calculate_puzzle_score(5, 0.5, 15)
    assert score2 > score1
    assert score2 == 5100

def test_crossword_generation_does_not_exceed_requested_word_count():
    """Generator should never place more words than the configured cap."""
    file_path = os.path.join(
        os.path.dirname(__file__),
        "..",
        "app",
        "data",
        "crossword_words.csv",
    )
    with open(file_path, mode="r", encoding="utf-8") as f:
        word_pool = [
            (row["word"].strip().upper(), row["hint"].strip())
            for row in csv.DictReader(f)
            if row["word"].strip().isalpha() and row["hint"].strip()
        ]

    random.seed(2)
    gen = CrosswordGenerator(8, 8)
    gen.generate(word_pool, 10)

    assert len(gen.words_placed) <= 10

def test_crossword_num_words_is_clamped_to_supported_range():
    assert _clamp_num_words(999) == MAX_NUM_WORDS
    assert _clamp_num_words(0) == MIN_NUM_WORDS
    assert _clamp_num_words("not a number") == 10

def test_no_duplicate_words():
    file_path = os.path.join(
        os.path.dirname(__file__),
        "..",
        "app",
        "data",
        "crossword_words.csv",
    )

    # Dictionary to store word occurrences: { WORD: [row_data, ...] }
    word_map = defaultdict(list)
    
    with open(file_path, mode='r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            # Clean and normalize the word
            word = row['word'].strip().upper()
            word_map[word].append(row)
            
    # Filter for words that appear more than once
    duplicates = {word: rows for word, rows in word_map.items() if len(rows) > 1}
    
    if duplicates:
        print(f"Found {len(duplicates)} duplicate words:\n")
        
        for word, occurrences in duplicates.items():
            print(f"Word: {word}")
            print(f"Count: {len(occurrences)}")
            for i, row in enumerate(occurrences, 1):
                hint = row.get('hint', 'No hint provided')
                diff = row.get('difficulty', 'unknown')
                print(f"  {i}. [{diff}] {hint}")
            print("-" * 30)
    
    assert not duplicates, "Duplicate words found in the crossword word list"


ANSWER_TEXT = "ANSWERS: 1A:CAT, 2D:DOG, 3A:PLANET, 4D:ORBIT, 5A:COMET"


def test_render_text_block_inverted_is_upright_rotated_180():
    font = ImageFont.load_default()
    upright = render_text_block(ANSWER_TEXT, 372, font)
    inverted = render_text_block(ANSWER_TEXT, 372, font, inverted=True)

    assert upright.mode == inverted.mode == "1"
    assert inverted.size == upright.size
    # Exactly the upright block turned upside-down...
    rotated = upright.transpose(Image.Transpose.ROTATE_180)
    assert ImageChops.difference(inverted, rotated).getbbox() is None
    # ...and genuinely different from the upright one.
    assert ImageChops.difference(inverted, upright).getbbox() is not None


def test_render_text_block_wraps_to_the_requested_width():
    font = ImageFont.load_default()
    long_text = "ANSWERS: " + ", ".join(f"{i}A:WORD{i}" for i in range(1, 19))
    narrow = render_text_block(long_text, 120, font)
    wide = render_text_block(long_text, 372, font)
    single_line = render_text_block("ANSWERS:", 372, font)

    assert narrow.width == 120 and wide.width == 372
    assert single_line.height < wide.height < narrow.height


def test_render_text_block_breaks_a_word_wider_than_the_line():
    font = ImageFont.load_default()
    img = render_text_block("X" * 200, 100, font, inverted=True)
    assert img.width == 100
    assert img.height > render_text_block("X", 100, font).height


def test_render_text_block_empty_text_returns_a_blank_image():
    img = render_text_block("", 100, ImageFont.load_default(), inverted=True)
    assert img.width == 100
    assert img.getbbox() is not None  # 1-bit white background is non-zero
    assert ImageChops.invert(img.convert("L")).getbbox() is None  # no ink


def test_printer_text_font_uses_real_fonts_and_ignores_placeholders():
    real = ImageFont.load_default()
    printer = MagicMock()
    printer._get_font.return_value = real
    assert _printer_text_font(printer, "light") is real
    printer._get_font.assert_called_once_with("light")

    # A mock printer that hands back a placeholder falls back to PIL's default.
    placeholder = MagicMock()
    fallback = _printer_text_font(placeholder, "light")
    assert isinstance(fallback, (ImageFont.FreeTypeFont, ImageFont.ImageFont))

    # A printer with no font support at all, or one that raises, also works.
    class NoFonts:
        pass

    class BrokenFonts:
        def _get_font(self, style):
            raise RuntimeError("boom")

    for p in (NoFonts(), BrokenFonts()):
        assert isinstance(
            _printer_text_font(p, "light"), (ImageFont.FreeTypeFont, ImageFont.ImageFont)
        )


def test_execute_crossword_prints_answers_as_an_inverted_image():
    random.seed(7)
    printer = MagicMock()
    printer.PRINTER_WIDTH_DOTS = 384

    with patch("app.modules.crossword.render_text_block", wraps=render_text_block) as spy:
        execute_crossword(printer, {"num_words": 6, "difficulty": "Easy"})

    # The answer key is rendered inverted, as one block starting with "ANSWERS: ".
    assert spy.call_count == 1
    args, kwargs = spy.call_args
    assert args[0].startswith("ANSWERS: ")
    assert args[1] == 384 - ANSWER_BLOCK_MARGIN_DOTS
    assert kwargs.get("inverted") is True

    # It goes out as the second image (after the grid), not as caption text.
    assert printer.print_image.call_count == 2
    answer_img = printer.print_image.call_args_list[-1].args[0]
    assert answer_img.mode == "1"
    assert answer_img.width == 384 - ANSWER_BLOCK_MARGIN_DOTS
    assert not any(
        str(c.args[0]).startswith("ANSWERS")
        for c in printer.print_caption.call_args_list
    )
    # The "hidden below" hint stays as normal, upright text.
    printer.print_caption.assert_any_call("Solution hidden below...")
