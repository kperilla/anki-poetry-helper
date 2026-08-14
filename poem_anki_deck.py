#!/usr/bin/env python3
"""
poem_anki_deck.py

Turn a poem into an Anki deck (.apkg) for memorization, using layered
recall cards that build up from "what comes next" cues to full recitation.

CARD TYPES
----------
1. Line Start        front: 2 lines before          back: first N words of the next line
2. Line Completion    front: 1 line before + first N words of current line
                       back: full current line
3. Full Line          front: 2 lines before          back: full current line
4. Stanza Completion  front: line before the stanza + first line of the stanza
                       back: full stanza
5. Full Stanza        front: 2 lines before the stanza
                       back: full stanza
6. Full Poem          front: "Recite the poem"       back: full poem text
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
from dataclasses import dataclass, field

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
    )


def stable_guid(*parts):
    return genanki.guid_for(*[str(p) for p in parts])


# --------------------------------------------------------------------------
# Card generation
# --------------------------------------------------------------------------

def build_notes(stanzas, model, deck_name, title, author, n_words, n_ctx_lines):
    flat = flatten(stanzas)
    notes = []

    def add_note(card_type, front_html, back_html, guid_key, extra_tags=None):
        tags = ["poem-deck", card_type.lower().replace(" ", "-")]
        if extra_tags:
            tags.extend(extra_tags)
        note = genanki.Note(
            model=model,
            fields=[card_type, front_html, back_html],
            tags=tags,
            guid=stable_guid(deck_name, title, guid_key),
        )
        notes.append(note)

    # --- Per-line cards: Line Start, Line Completion, Full Line ---
    for fl in flat:
        ctx2 = context_before(flat, fl.global_idx, n_ctx_lines)
        ctx1 = context_before(flat, fl.global_idx, 1)

        # 1. Line Start: front = 2 lines before, back = first N words of THIS line
        card_type = "Line Start"
        add_note(
            card_type,
            lines_to_html(ctx2),
            esc(first_n_words(fl.text, n_words)),
            ("line-start", fl.global_idx),
        )

        # 2. Line Completion: front = 1 line before + first N words of this line
        cue = first_n_words(fl.text, n_words)
        front_parts = []
        card_type = "Line Completion"
        if ctx1:
            front_parts.append(f'<div class="context">{lines_to_html(ctx1)}</div>')
        else:
            front_parts.append(f'<div class="context">[{card_type}]<br>[{BEGINNING_MARKER}]</div>')
        front_parts.append(f"<div>{esc(cue)} …</div>")
        add_note(
            card_type,
            "".join(front_parts),
            esc(fl.text),
            ("line-completion", fl.global_idx),
        )

        # 3. Full Line: front = 2 lines before, back = full line
        card_type = "Full Line"
        add_note(
            card_type,
            lines_to_html(ctx2),
            esc(fl.text),
            ("full-line", fl.global_idx),
        )

    # --- Per-stanza cards: Stanza Completion, Full Stanza ---
    for s_idx, stanza in enumerate(stanzas):
        card_type = "Stanza Completion"
        stanza_start_global = next(
            fl.global_idx for fl in flat if fl.stanza_idx == s_idx and fl.line_in_stanza == 0
        )
        line_before = context_before(flat, stanza_start_global, 1)
        two_before = context_before(flat, stanza_start_global, n_ctx_lines)
        stanza_html = lines_to_html(stanza)

        # 4. Stanza Completion: front = line before stanza + first line of stanza
        front_parts = []
        if line_before:
            front_parts.append(f'<div class="context">{lines_to_html(line_before)}</div>')
        else:
            front_parts.append(f'<div class="context"><i>{BEGINNING_MARKER}</i></div>')
        front_parts.append(f"<br><div>{esc(stanza[0])}</div>...")
        add_note(
            card_type,
            "".join(front_parts),
            stanza_html,
            ("stanza-completion", s_idx),
        )

        # 5. Full Stanza: front = 2 lines before stanza, back = full stanza
        card_type = "Full Stanza"
        add_note(
            card_type,
            lines_to_html(two_before) + '<br>...',
            stanza_html,
            ("full-stanza", s_idx),
        )

    # --- Full Poem card (exactly one) ---
    heading = title if title else "this poem"
    by_line = f" by {author}" if author else ""
    card_type = "Full Poem"
    full_text_html = "<br><br>".join(lines_to_html(stanza) for stanza in stanzas)
    add_note(
        card_type,
        f"Recite <b>{esc(heading)}</b>{esc(by_line)} in full.",
        full_text_html,
        ("full-poem",),
    )

    return notes


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Generate an Anki deck from a poem.")
    parser.add_argument("input", nargs="?", help="Path to a text file with the poem. Omit to read from stdin.")
    parser.add_argument("--title", default="Untitled Poem", help="Poem title (used in deck/card content).")
    parser.add_argument("--author", default="", help="Poem author (optional).")
    parser.add_argument("--deck", default=None, help="Anki deck name, e.g. 'Latin::Poetry::Aeneid'. Defaults to the title.")
    parser.add_argument("--output", default=None, help="Output .apkg path. Defaults to a slug of the title.")
    parser.add_argument("--words", type=int, default=2, help="Number of leading words used as a cue (default: 2).")
    parser.add_argument("--context-lines", type=int, default=2, help="Number of lines of preceding context (default: 2).")
    args = parser.parse_args()

    if args.input:
        with open(args.input, "r", encoding="utf-8") as f:
            raw_text = f.read()
    else:
        if sys.stdin.isatty():
            parser.error("Provide an input file path or pipe poem text via stdin.")
        raw_text = sys.stdin.read()

    stanzas = parse_poem(raw_text)

    deck_name = args.deck or args.title
    deck_id = random.Random(deck_name).randrange(1 << 30, 1 << 31)
    deck = genanki.Deck(deck_id, deck_name)

    model = make_model()
    notes = build_notes(
        stanzas, model, deck_name, args.title, args.author,
        n_words=args.words, n_ctx_lines=args.context_lines,
    )
    for note in notes:
        deck.add_note(note)

    output_path = args.output
    if not output_path:
        slug = "".join(c if c.isalnum() else "_" for c in args.title).strip("_").lower() or "poem"
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
