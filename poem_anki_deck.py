#!/usr/bin/env python3
"""
poem_anki_deck.py

Turn a poem into an Anki deck (.apkg) for memorization, using layered
recall cards that build up from "what comes next" cues to full recitation.

CARD TYPES
----------
1. Quarter Cloze      front: previous line + current line with 1 of 4 quarters
                       hidden       back: full current line (4 cards/line, lines
                       with 4+ words only)
2. Half Cloze         front: previous line + current line with 1 of 2 halves
                       hidden       back: full current line (2 cards/line, lines
                       with 2+ words only)
3. Line Start         front: 2 lines before          back: first N words of the next line
4. Line Odd Words     front: 2 lines before + current line with even-numbered
                       words blanked  back: full current line
5. Line Even Words    front: 2 lines before + current line with odd-numbered
                       words blanked  back: full current line
6. Line Completion    front: 1 line before + first N words of current line
                       back: full current line
7. Full Line          front: 2 lines before          back: full current line
8. Stanza Completion  front: line before the stanza + first line of the stanza
                       back: full stanza
9. Full Stanza        front: 2 lines before the stanza
                       back: full stanza
10. Full Poem         front: "Recite the poem"       back: full poem text
                       (exactly one card)

USAGE
-----
    python3 poem_anki_deck.py poem.txt --title "Poem Title" --author "Author"

    # or pipe text in:
    cat poem.txt | python3 poem_anki_deck.py --title "Poem Title"

    # options:
    python3 poem_anki_deck.py poem.txt \
        --title "Aeneid I.1-11" \
        --author "Virgil" \
        --deck "Latin::Poetry::Aeneid" \
        --output aeneid.apkg \
        --words 2 \
        --context-lines 2

INPUT FORMAT
------------
Plain text. Lines are separated by single newlines. Stanzas are separated
by one or more BLANK lines (i.e. two or more consecutive newlines).

    Arma virumque cano, Troiae qui primus ab oris
    Italiam, fato profugus, Laviniaque venit
    litora, multum ille et terris iactatus et alto

    vi superum saevae memorem Iunonis ob iram;
    multa quoque et bello passus, dum conderet urbem,
    inferretque deos Latio...
"""

import argparse
import html
import random
import sys
import tomllib
from dataclasses import dataclass, field
from itertools import count
from typing import Callable, List, Optional, Tuple

import genanki

# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------

@dataclass
class FlatLine:
    global_idx: int
    stanza_idx: int
    line_in_stanza: int
    text: str


def parse_poem(raw_text: str):
    """Split raw text into stanzas (list of list of line strings)."""
    text = raw_text.replace("\r\n", "\n").replace("\r", "\n").strip("\n")
    # split on 2+ newlines (one or more blank lines) -> stanza boundary
    import re
    raw_stanzas = re.split(r"\n\s*\n+", text)
    stanzas = []
    for rs in raw_stanzas:
        lines = [ln.strip() for ln in rs.split("\n") if ln.strip() != ""]
        if lines:
            stanzas.append(lines)
    if not stanzas:
        raise ValueError("No content found in poem text.")
    return stanzas


def flatten(stanzas):
    flat = []
    gi = 0
    for s_idx, stanza in enumerate(stanzas):
        for l_idx, line in enumerate(stanza):
            flat.append(FlatLine(gi, s_idx, l_idx, line))
            gi += 1
    return flat


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

BEGINNING_MARKER = "(beginning of poem)"


def esc(text: str) -> str:
    return html.escape(text, quote=False)


def lines_to_html(lines):
    """Join a list of raw line strings into an Anki-friendly HTML block."""
    if not lines:
        return f"<i>{BEGINNING_MARKER}</i>"
    return "<br>".join(esc(l) for l in lines)


def context_before(flat, global_idx, n_lines):
    """Return up to n_lines of raw text immediately before flat[global_idx]."""
    start = max(0, global_idx - n_lines)
    return [fl.text for fl in flat[start:global_idx]]


def first_n_words(text, n):
    words = text.split()
    snippet = " ".join(words[:n])
    return snippet + (" …" if len(words) > n else "")


def masked_line_html(text, keep_parity):
    """Render text with every other word blanked out.

    keep_parity: "odd" keeps the 1st, 3rd, ... words visible and blanks the
    rest; "even" keeps the 2nd, 4th, ... words visible and blanks the rest.
    """
    words = text.split()
    parts = []
    for i, word in enumerate(words):
        word_num = i + 1
        visible = (word_num % 2 == 1) if keep_parity == "odd" else (word_num % 2 == 0)
        parts.append(esc(word) if visible else '<span class="blank">___</span>')
    return " ".join(parts)


def split_into_chunks(words, n_chunks):
    """Split words into n_chunks contiguous, roughly-equal-sized chunks."""
    base, extra = divmod(len(words), n_chunks)
    chunks = []
    start = 0
    for i in range(n_chunks):
        size = base + (1 if i < extra else 0)
        chunks.append(words[start:start + size])
        start += size
    return chunks


def cloze_line_html(text, n_chunks, hide_idx):
    """Render text split into n_chunks contiguous chunks, with the hide_idx-th chunk blanked out."""
    chunks = split_into_chunks(text.split(), n_chunks)
    parts = []
    for i, chunk in enumerate(chunks):
        if i == hide_idx:
            parts.extend('<span class="blank">___</span>' for _ in chunk)
        else:
            parts.extend(esc(word) for word in chunk)
    return " ".join(parts)


# --------------------------------------------------------------------------
# Anki model / deck construction
# --------------------------------------------------------------------------

CARD_CSS = """
.card {
    font-family: Meiryo;
    font-size: 40px;
    text-align: center;
    color: black;
    background-color: white;
}
.tag {
    display: inline-block;
    font-size: 12px;
    letter-spacing: 0.05em;
    text-transform: uppercase;
    color: #888;
    border: 1px solid #ccc;
    border-radius: 4px;
    padding: 2px 8px;
    margin-bottom: 14px;
}
.context {
    color: #666;
    font-style: italic;
}
.blank {
    color: #bbb;
    letter-spacing: 2px;
}
hr#answer {
    margin-top: 18px;
    margin-bottom: 18px;
    border: none;
    border-top: 1px solid #ccc;
}
.answer {
    font-weight: bold;
}
"""

def make_model():
    model_id = random.Random("poem-deck-model-v1").randrange(1 << 30, 1 << 31)
    return genanki.Model(
        model_id,
        "Generated Poetry Note",
        fields=[
            {"name": "CardType"},
            {"name": "Front"},
            {"name": "Back"},
            {"name": "Sort"},
        ],
        templates=[
            {
                "name": "Card",
                "qfmt": '<div class="tag">[{{CardType}}]</div><div>{{Front}}</div>',
                "afmt": '<div class="tag">[{{CardType}}]</div><div>{{Front}}</div>'
                        '<hr id="answer"><div class="answer">{{Back}}</div>',
            }
        ],
        css=CARD_CSS,
        sort_field_index=3,
    )


def stable_guid(*parts):
    return genanki.guid_for(*[str(p) for p in parts])


# --------------------------------------------------------------------------
# Card generation
# --------------------------------------------------------------------------
#
# Each card type is a small, self-contained builder registered in one of the
# lists below. build_notes() just walks the poem and, at each granularity
# (line / stanza / poem), runs every builder registered for that granularity.
# To add a new card type: write a builder function and append a CardSpec for
# it to the relevant list -- no changes to build_notes() are needed.

@dataclass
class LineContext:
    fl: "FlatLine"
    ctx1: List[str]  # 1 line of context immediately before this line
    ctx2: List[str]  # n_ctx_lines of context immediately before this line
    n_words: int


@dataclass
class StanzaContext:
    s_idx: int
    stanza: List[str]
    line_before: List[str]  # 1 line of context before the stanza
    two_before: List[str]   # n_ctx_lines of context before the stanza


@dataclass
class PoemContext:
    stanzas: List[List[str]]
    title: str
    author: str


# A builder takes its context object and returns (front_html, back_html, guid_key),
# or None if this card doesn't apply to that context (e.g. a cloze card that
# needs more words than the line has).
CardBuilder = Callable[[object], Optional[Tuple[str, str, tuple]]]


@dataclass(frozen=True)
class CardSpec:
    name: str
    build: CardBuilder


def _make_cloze_builder(n_chunks: int, hide_idx: int, guid_prefix: str) -> CardBuilder:
    """front = previous line + current line with 1 of n_chunks contiguous
    chunks blanked, back = full line. Declines (returns None) if the line
    has fewer words than n_chunks, since it can't be split that finely.
    """
    def build(ctx: LineContext) -> Optional[Tuple[str, str, tuple]]:
        if len(ctx.fl.text.split()) < n_chunks:
            return None
        if ctx.ctx1:
            front = f'<div class="context">{lines_to_html(ctx.ctx1)}</div>'
        else:
            front = f'<div class="context"><i>{BEGINNING_MARKER}</i></div>'
        front += f"<div>{cloze_line_html(ctx.fl.text, n_chunks, hide_idx)}</div>"
        return (
            front,
            esc(ctx.fl.text),
            (guid_prefix, ctx.fl.global_idx, hide_idx),
        )
    return build


QUARTER_CLOZE_SPECS = [
    CardSpec("Quarter Cloze", _make_cloze_builder(4, i, "quarter-cloze"))
    for i in range(4)
]

HALF_CLOZE_SPECS = [
    CardSpec("Half Cloze", _make_cloze_builder(2, i, "half-cloze"))
    for i in range(2)
]


def _line_start(ctx: LineContext) -> Tuple[str, str, tuple]:
    """front = context before, back = first N words of this line."""
    return (
        lines_to_html(ctx.ctx2),
        esc(first_n_words(ctx.fl.text, ctx.n_words)),
        ("line-start", ctx.fl.global_idx),
    )


def _make_masked_line_builder(keep_parity: str, guid_prefix: str) -> CardBuilder:
    """front = context before + line with every other word blanked, back = full line."""
    def build(ctx: LineContext) -> Tuple[str, str, tuple]:
        if ctx.ctx2:
            front = f'<div class="context">{lines_to_html(ctx.ctx2)}</div>'
        else:
            front = f'<div class="context"><i>{BEGINNING_MARKER}</i></div>'
        front += f"<div>{masked_line_html(ctx.fl.text, keep_parity)}</div>"
        return (
            front,
            esc(ctx.fl.text),
            (guid_prefix, ctx.fl.global_idx),
        )
    return build


def _line_completion(ctx: LineContext) -> Tuple[str, str, tuple]:
    """front = 1 line before + first N words of this line, back = full line."""
    cue = first_n_words(ctx.fl.text, ctx.n_words)
    front_parts = []
    if ctx.ctx1:
        front_parts.append(f'<div class="context">{lines_to_html(ctx.ctx1)}</div>')
    else:
        front_parts.append(f'<div class="context">[Line Completion]<br>[{BEGINNING_MARKER}]</div>')
    front_parts.append(f"<div>{esc(cue)} …</div>")
    return (
        "".join(front_parts),
        esc(ctx.fl.text),
        ("line-completion", ctx.fl.global_idx),
    )


def _full_line(ctx: LineContext) -> Tuple[str, str, tuple]:
    """front = context before, back = full line."""
    return (
        lines_to_html(ctx.ctx2),
        esc(ctx.fl.text),
        ("full-line", ctx.fl.global_idx),
    )


# Within a stanza, line-level cards are emitted as a sequence of groups (see
# build_notes()): each group below is shuffled together across every line in
# the stanza before being added, except SEQUENTIAL_LINE_CARD_SPECS, which
# keeps its original line order.
START_ODD_EVEN_LINE_CARD_SPECS = [
    CardSpec("Line Start", _line_start),
    CardSpec("Line Odd Words", _make_masked_line_builder("odd", "line-odd-words")),
    CardSpec("Line Even Words", _make_masked_line_builder("even", "line-even-words")),
]

LINE_COMPLETION_CARD_SPECS = [
    CardSpec("Line Completion", _line_completion),
]

SEQUENTIAL_LINE_CARD_SPECS = [
    CardSpec("Full Line", _full_line),
]


def _stanza_completion(ctx: StanzaContext) -> Tuple[str, str, tuple]:
    """front = line before stanza + first line of stanza, back = full stanza."""
    front_parts = []
    if ctx.line_before:
        front_parts.append(f'<div class="context">{lines_to_html(ctx.line_before)}</div>')
    else:
        front_parts.append(f'<div class="context"><i>{BEGINNING_MARKER}</i></div>')
    front_parts.append(f"<br><div>{esc(ctx.stanza[0])}</div>...")
    return (
        "".join(front_parts),
        lines_to_html(ctx.stanza),
        ("stanza-completion", ctx.s_idx),
    )


def _full_stanza(ctx: StanzaContext) -> Tuple[str, str, tuple]:
    """front = context before stanza, back = full stanza."""
    return (
        lines_to_html(ctx.two_before) + "<br>...",
        lines_to_html(ctx.stanza),
        ("full-stanza", ctx.s_idx),
    )


STANZA_CARD_SPECS = [
    CardSpec("Stanza Completion", _stanza_completion),
    CardSpec("Full Stanza", _full_stanza),
]


def _full_poem(ctx: PoemContext) -> Tuple[str, str, tuple]:
    """front = 'recite the poem' prompt, back = full poem text."""
    heading = ctx.title if ctx.title else "this poem"
    by_line = f" by {ctx.author}" if ctx.author else ""
    full_text_html = "<br><br>".join(lines_to_html(stanza) for stanza in ctx.stanzas)
    return (
        f"Recite <b>{esc(heading)}</b>{esc(by_line)} in full.",
        full_text_html,
        ("full-poem",),
    )


POEM_CARD_SPECS = [
    CardSpec("Full Poem", _full_poem),
]


def build_notes(stanzas, model, deck_name, title, author, n_words, n_ctx_lines):
    flat = flatten(stanzas)
    notes = []
    sort_numbers = count(1)

    def add_note(name: str, front_html: str, back_html: str, guid_key: tuple) -> None:
        sort_number = next(sort_numbers)
        tags = ["poem-deck", name.lower().replace(" ", "-")]
        notes.append(genanki.Note(
            model=model,
            fields=[name, front_html, back_html, str(sort_number)],
            tags=tags,
            guid=stable_guid(deck_name, title, guid_key),
            due=sort_number,
        ))

    def emit(spec: CardSpec, ctx) -> None:
        """Build and add a note, skipping it if the builder declines (returns None)."""
        result = spec.build(ctx)
        if result is not None:
            add_note(spec.name, *result)

    def shuffle_and_emit(specs: List[CardSpec], ctxs: list, seed_label: str) -> None:
        """Build every spec x ctx note (dropping declined ones), shuffle
        deterministically, then add in that shuffled order."""
        built = [(spec, spec.build(ctx)) for ctx in ctxs for spec in specs]
        built = [(spec, result) for spec, result in built if result is not None]
        random.Random(f"shuffle:{deck_name}:{title}:{s_idx}:{seed_label}").shuffle(built)
        for spec, result in built:
            add_note(spec.name, *result)

    # --- Walk stanza by stanza. Within each stanza, line-level cards are
    #     emitted as a sequence of groups (see the spec lists above): the
    #     cloze groups, then Start/Odd/Even, then Completion -- each shuffled
    #     across every line in the stanza -- followed by Full Line in
    #     original line order, then the stanza-level cards. Every note's
    #     "due" (and Sort field) is assigned in this same final order, so
    #     it's also the order new cards are shown in Anki. Finally, run the
    #     poem-level builders once, after every stanza. ---
    for s_idx, stanza in enumerate(stanzas):
        stanza_lines = [fl for fl in flat if fl.stanza_idx == s_idx]
        line_ctxs = [
            LineContext(
                fl=fl,
                ctx1=context_before(flat, fl.global_idx, 1),
                ctx2=context_before(flat, fl.global_idx, n_ctx_lines),
                n_words=n_words,
            )
            for fl in stanza_lines
        ]

        shuffle_and_emit(QUARTER_CLOZE_SPECS, line_ctxs, "quarter-cloze")
        shuffle_and_emit(HALF_CLOZE_SPECS, line_ctxs, "half-cloze")
        shuffle_and_emit(START_ODD_EVEN_LINE_CARD_SPECS, line_ctxs, "start-odd-even")
        shuffle_and_emit(LINE_COMPLETION_CARD_SPECS, line_ctxs, "line-completion")

        for ctx in line_ctxs:
            for spec in SEQUENTIAL_LINE_CARD_SPECS:
                emit(spec, ctx)

        stanza_start_global = stanza_lines[0].global_idx
        stanza_ctx = StanzaContext(
            s_idx=s_idx,
            stanza=stanza,
            line_before=context_before(flat, stanza_start_global, 1),
            two_before=context_before(flat, stanza_start_global, n_ctx_lines),
        )
        for spec in STANZA_CARD_SPECS:
            emit(spec, stanza_ctx)

    poem_ctx = PoemContext(stanzas=stanzas, title=title, author=author)
    for spec in POEM_CARD_SPECS:
        emit(spec, poem_ctx)

    return notes


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

DEFAULT_TITLE = "Untitled Poem"


def load_config_file(path):
    """Read run settings from a TOML config file (see config.template.toml)."""
    with open(path, "rb") as f:
        data = tomllib.load(f)
    if not data.get("input"):
        raise SystemExit(f"Config file '{path}' must specify 'input' (path to the poem text file).")
    return {
        "input": data["input"],
        "title": data.get("title", DEFAULT_TITLE),
        "author": data.get("author", ""),
        "deck": data.get("deck"),
        "output": data.get("output"),
        "words": data.get("words", 2),
        "context_lines": data.get("context_lines", 2),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="Generate an Anki deck from a poem.")
    parser.add_argument("input", nargs="?", help="Path to a text file with the poem. Omit to read from stdin.")
    parser.add_argument("--config", default=None, help="Path to a TOML config file (see config.template.toml). When given, settings are read from the file and all options below are ignored.")
    parser.add_argument("--title", default=DEFAULT_TITLE, help="Poem title (used in deck/card content).")
    parser.add_argument("--author", default="", help="Poem author (optional).")
    parser.add_argument("--deck", default=None, help="Anki deck name, e.g. 'Latin::Poetry::Aeneid'. Defaults to the title.")
    parser.add_argument("--output", default=None, help="Output .apkg path. Defaults to a slug of the title.")
    parser.add_argument("--words", type=int, default=2, help="Number of leading words used as a cue (default: 2).")
    parser.add_argument("--context-lines", type=int, default=2, help="Number of lines of preceding context (default: 2).")
    args = parser.parse_args(argv)

    if args.config:
        cfg = load_config_file(args.config)
        with open(cfg["input"], "r", encoding="utf-8") as f:
            raw_text = f.read()
    else:
        cfg = {
            "title": args.title,
            "author": args.author,
            "deck": args.deck,
            "output": args.output,
            "words": args.words,
            "context_lines": args.context_lines,
        }
        if args.input:
            with open(args.input, "r", encoding="utf-8") as f:
                raw_text = f.read()
        else:
            if sys.stdin.isatty():
                parser.error("Provide an input file path or pipe poem text via stdin.")
            raw_text = sys.stdin.read()

    stanzas = parse_poem(raw_text)

    deck_name = cfg["deck"] or cfg["title"]
    deck_id = random.Random(deck_name).randrange(1 << 30, 1 << 31)
    deck = genanki.Deck(deck_id, deck_name)

    model = make_model()
    notes = build_notes(
        stanzas, model, deck_name, cfg["title"], cfg["author"],
        n_words=cfg["words"], n_ctx_lines=cfg["context_lines"],
    )
    for note in notes:
        deck.add_note(note)

    output_path = cfg["output"]
    if not output_path:
        slug = "".join(c if c.isalnum() else "_" for c in cfg["title"]).strip("_").lower() or "poem"
        output_path = f"{slug}.apkg"

    genanki.Package(deck).write_to_file(output_path)

    # Summary
    counts = {}
    for n in notes:
        counts[n.fields[0]] = counts.get(n.fields[0], 0) + 1
    print(f"Wrote {len(notes)} notes to {output_path}")
    print(f"  Stanzas: {len(stanzas)}, Lines: {sum(len(s) for s in stanzas)}")
    for k, v in counts.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
