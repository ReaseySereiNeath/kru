# Kru — your book teacher

Kru ("teacher" in Khmer) turns a book (PDF, EPUB, or TXT, up to about
500 pages) into a short, plain-language summary with diagrams. It uses free,
open-source AI models running on your own computer through
[Ollama](https://ollama.com). No paid APIs.

## Build status

| Phase | What | Status |
|---|---|---|
| 1 | Project setup, config, LLM check script | ✅ done |
| 2 | Text extraction + chunking (command line) | ✅ done |
| 3 | Full summary pipeline (command line) | ✅ done |
| 4 | Supabase schema, API, background worker | — |
| 5 | Frontend pages | — |
| 6 | Polish, rate limits, run-everything guide | — |

## Project layout

```
kru/
├── prompts/        Prompt templates (edit these to improve summaries)
├── backend/
│   ├── app/        FastAPI routes, config, database access
│   ├── pipeline/   Text extraction, chunking, LLM calls, prompt loading
│   ├── worker/     Background job runner
│   └── tests/
└── frontend/       Next.js app (Phase 5)
```

All settings live in `backend/.env` (copy it from `backend/.env.example`) and
are read in one place: `backend/app/config.py`.

---

## Setup (macOS)

You need: Homebrew, **Python 3.11+**, and about 10 GB of free disk space for
the model.

> On this Mac, plain `python3` is version 3.8. Always use `python3.11`.

### 1. Install Ollama and start it

```bash
brew install ollama
brew services start ollama        # runs in the background, starts at login
curl http://localhost:11434/api/version   # should print a version number
```

(You can also install the desktop app from https://ollama.com/download.)

### 2. Download a model

```bash
ollama pull qwen2.5:14b           # ~9 GB; good quality on 32 GB of memory
ollama list                       # shows the models you have
```

Picking a size, as a rough guide:

| Your memory | Model | Notes |
|---|---|---|
| 16 GB | `qwen2.5:7b` | faster, simpler summaries |
| 32 GB+ | `qwen2.5:14b` | recommended default |

Put the model name in `LLM_MODEL_CHUNK` and `LLM_MODEL_FINAL` in `backend/.env`.

### 3. Context length (important)

The **context** is how much text the model can read at once, in tokens
(1 token ≈ 0.75 English words). Kru sends big pieces of a book, so the
context must be at least `LLM_CONTEXT_TOKENS` (default 16,384).

Two things to know:

- **If a prompt is longer than the context, Ollama silently cuts off the
  start of it.** No error. The summary just quietly gets worse. Kru checks
  sizes before every call to avoid this, but only if Ollama's context is
  really as big as `LLM_CONTEXT_TOKENS`.
- **Kru can't set the context per request.** Kru talks to Ollama through its
  OpenAI-compatible endpoint (`/v1`), and that endpoint ignores the `num_ctx`
  setting. (Tested on Ollama 0.40.1.) So the context is set on the Ollama
  server instead.

Ollama 0.40 already loads `qwen2.5:14b` with a 32,768-token context on a
32 GB Mac, which is more than enough. **Run the check in step 5.** If it
prints a `WARN` about context, set it yourself:

```bash
# Homebrew service (resets after a reboot; run again if the check warns):
launchctl setenv OLLAMA_CONTEXT_LENGTH 16384
brew services restart ollama

# Or, if you run Ollama by hand in a terminal:
OLLAMA_CONTEXT_LENGTH=16384 ollama serve
```

The Ollama desktop app also has a context length setting in its Settings.

### 4. Set up the backend

```bash
cd backend
python3.11 -m venv .venv          # a private Python just for this project
source .venv/bin/activate         # do this in every new terminal
pip install -r requirements.txt
cp .env.example .env              # then edit .env if needed
```

### 5. Check that the model works

```bash
cd backend
source .venv/bin/activate
python -m pipeline.check_llm
```

Expected output (times will vary):

```
OK:   models found
...   sending a test message (first call loads the model, please wait)
OK:   reply in 7.9 s
      Reply: People summarize books to capture the essence of the content...
      Tokens: 32 in, 33 out
OK:   Ollama context is 32768 tokens (needs >= 16384)

All checks passed.
```

The first call is slow because the model loads from disk into memory. Run it
again and it is much faster.

| Message | What to do |
|---|---|
| `cannot reach the LLM server` | `brew services start ollama` |
| `model(s) not found` | `ollama pull <name>`, or fix the name in `.env` |
| `WARN: Ollama loaded a ...-token context` | See step 3 |

### 6. Run the tests

```bash
cd backend
source .venv/bin/activate
pytest
```

---

## Try it: read and split a book (no AI)

This shows how Kru reads a book and how it splits it into chunks for the
model. Nothing is sent to the model, so it is fast.

```bash
cd backend
source .venv/bin/activate
python -m tests.samples.make_samples              # builds sample.pdf/.epub/.txt in tests/samples/
python -m pipeline.chunk_book tests/samples/sample.pdf --target-words 500
python -m pipeline.chunk_book path/to/your/book.epub              # uses CHUNK_TARGET_WORDS
python -m pipeline.chunk_book path/to/your/book.pdf --preview     # also shows the start of each chunk
```

Example output:

```
Title:    The Little Lighthouse
Author:   Ada Example
Type:     PDF, 13 pages
Words:    1,744 (about 4 pages at 400 words/page)
Status:   ok
Chapters: from the file's own chapters (PDF bookmarks / EPUB table of contents)
Chunks:   4 (target 500 words each)

   #   words  chapters
   1     241  Chapter 1: The Storm
   2     465  Chapter 2: The Long Night (part 1 of 3)
   3     460  Chapter 2: The Long Night (part 2 of 3)
   4     578  Chapter 2: The Long Night (part 3 of 3), Chapter 3: Morning
```

How the splitting works:

- **Chapters** come from the file itself: PDF bookmarks or the EPUB table of
  contents. A TXT file has none, so `chunk_book` splits it by paragraphs.
  (The full pipeline below also uses the chapter titles from the book
  check, and looks for them as headings in the text.)
- **Short chapters are combined** into one chunk. A chunk may go up to 20%
  over the target to keep a chapter whole.
- **Long chapters are cut into equal parts** at paragraph breaks.
- **Limits:** books over `MAX_WORDS` are reported as too long. A PDF with
  under 50 words per page is reported as scanned (no OCR yet).

Good books for testing: free public-domain EPUBs and TXTs from
[Project Gutenberg](https://www.gutenberg.org). Put your own test books in a
`books/` folder; it is in `.gitignore`.

---

## Summarize a book (command line)

```bash
cd backend
source .venv/bin/activate
python -m pipeline.run path/to/book.epub
python -m pipeline.run path/to/book.pdf --restart     # forget saved progress, start again
```

It prints each step as it goes, then the summary, the time per chunk, and
the total time and tokens. The summary is also saved as
`backend/runs/<book name>.summary.md`.

**It can take a while.** Measured with `qwen2.5:14b` on an M1 Max (32 GB):
about 45 seconds per chunk of ~4,500 words, plus about a minute for the final
summary. *Alice in Wonderland* (30,000 words, 6 chunks) took 6 minutes. A
300-page book (about 27 chunks) takes about 20-25 minutes; a 500-page book
about 35-40 minutes.

**Stopping is safe.** Progress is saved after every model call, in
`backend/runs/` (one file per book, named after the file's contents). If
you press Ctrl+C, or the computer restarts, run the same command again and it
continues from the last finished chunk.

What happens, step by step:

1. **Read** the file and check the limits (as in `chunk_book` above).
2. **Book check** (`prompts/0_book_check.txt`): the model says whether the
   book is fiction or a learning book, its subject and level, its chapters,
   and sections to skip (copyright, index...). This runs once per book.
3. **Chunk notes** (`prompts/2_chunk_summary.txt`): one call per chunk, in
   order. Each chunk's "Carryover" section is passed to the next chunk, so
   the model knows what happened before.
4. **Merge** (`prompts/2b_merge_notes.txt`), only for long books: if all the
   notes together are too long for the final prompt, groups of notes are
   combined into shorter notes until they fit.
5. **Final summary** (`prompts/3_final_summary.txt`): only the FICTION or
   the LEARNING half of this prompt is sent, depending on the book check.

To improve summaries, edit the files in `prompts/` and run again with
`--restart`. Re-run `pytest` afterwards: a test checks that every
`{placeholder}` the code fills is still in the prompt files.

**Context safety.** Before every call, Kru estimates the prompt's size
(words × 1.4 tokens) and never sends one bigger than `LLM_CONTEXT_TOKENS`
minus the room kept for the answer. Chunks too big for that are split
further. After the first call to each model, Kru also checks the context
Ollama really loaded, and stops if it's smaller than `LLM_CONTEXT_TOKENS`.

**Errors.** Each call is retried up to 3 times (after 2, 4, and 8 seconds;
after 20, 40, and 80 seconds for "too many requests"). If a free daily limit
is reached (OpenRouter), Kru stops with a message; progress is kept, so run
it again the next day.

---

## Optional: OpenRouter free models

Instead of Ollama you can use OpenRouter's free models (IDs ending in `:free`).
Change only `backend/.env`: see the commented OpenRouter lines in
`.env.example`.

Be aware: free models have strict daily request limits. A 500-page book needs
about 55 model calls, which may be more than one day's allowance. Check
OpenRouter's current limits before relying on it.
