# anki-poetry-helper

Turn a poem into an Anki deck (`.apkg`) for memorization, using layered
recall cards that build up from "what comes next" cues to full recitation.

> **Note:** This project was built mostly with AI assistance (Claude Code).
> Review the code before relying on it for anything beyond personal use.

## Setup

Requires [uv](https://docs.astral.sh/uv/). Dependencies are installed
automatically when you run the script with `uv run`.

## Usage

There are two ways to run the script: passing options on the command line,
or pointing it at a config file.

### Command-line usage

```sh
uv run python poem_anki_deck.py ws-sonnet18.txt --title "Sonnet 18" --author "William Shakespeare"
```

The poem text file is a positional argument (or pipe text in via stdin).
Available options:

| Flag               | Description                                              | Default                    |
| ------------------ | ---------------------------------------------------------- | --------------------------- |
| `--title`           | Poem title, used in deck/card content                    | `Untitled Poem`            |
| `--author`          | Poem author                                               | *(none)*                   |
| `--deck`            | Anki deck name, e.g. `Latin::Poetry::Aeneid`              | same as `--title`          |
| `--output`          | Output `.apkg` path                                       | a slug of the title        |
| `--words`           | Number of leading words used as a memorization cue        | `2`                         |
| `--context-lines`   | Number of lines of preceding context shown on cards        | `2`                         |

Run `uv run python poem_anki_deck.py --help` for the full list.

### Config file usage

Instead of command-line flags, you can put your settings in a TOML config
file and point the script at it with `--config`:

```sh
uv run python poem_anki_deck.py --config my_poem.toml
```

Copy [config.template.toml](config.template.toml) to get started — it
documents every field and its default. Only `input` (the path to the poem
text file) is required; everything else falls back to the same defaults as
the command-line flags above. When `--config` is given, all other
command-line options are ignored.

## Input format

Plain text. Lines are separated by single newlines. Stanzas are separated
by one or more blank lines:

```
Arma virumque cano, Troiae qui primus ab oris
Italiam, fato profugus, Laviniaque venit
litora, multum ille et terris iactatus et alto

vi superum saevae memorem Iunonis ob iram;
multa quoque et bello passus, dum conderet urbem,
inferretque deos Latio...
```

## Card types

Each poem produces several layers of recall cards, from short cues up to
full recitation:

1. **Line Start** — front: 2 lines before; back: first N words of the next line
2. **Line Completion** — front: 1 line before + first N words of the current line; back: full current line
3. **Full Line** — front: 2 lines before; back: full current line
4. **Stanza Completion** — front: line before the stanza + first line of the stanza; back: full stanza
5. **Full Stanza** — front: 2 lines before the stanza; back: full stanza
6. **Full Poem** — front: "Recite the poem"; back: full poem text (exactly one card)
