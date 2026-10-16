# Kru — your book teacher

Kru ("teacher" in Khmer) turns a book (PDF, EPUB, or TXT, up to about
500 pages) into a short, plain-language summary with diagrams. It uses free,
open-source AI models running on your own computer through
[Ollama](https://ollama.com), or the free tier of an online AI service if you
choose. No paid APIs.

## Run Kru (every day)

Once the [first-time setup](#first-time-setup-macos) is done, Kru needs four
parts running. Each is one command, in its own terminal, from the project
folder:

```bash
./kru db        # 1. the database (Supabase, in Docker). Stays running in the background.
./kru api       # 2. the API                      http://localhost:8000
./kru worker    # 3. the worker that summarizes books
./kru web       # 4. the website                  http://localhost:3000
```

Then open **http://localhost:3000**, create an account, and upload a book.

Not sure what is running? Ask:

```bash
./kru check     # shows what is set up, what is running, and how to start the rest
./kru test      # runs all tests and code checks
./kru stop-db   # stops the database when you are done (your data is kept)
```

Ollama (the AI model server) must be running too. If you installed it as a
Homebrew service it starts by itself; `./kru check` tells you if it is not.

To stop a part, press Ctrl+C in its terminal. Stopping the worker in the
middle of a book is safe: it continues from the last finished part when you
start it again.

## First-time setup (macOS)

Do these once, in order. The sections below explain each step.

1. **Ollama and a model**: [Setup: the AI model](#setup-the-ai-model-and-the-backend), steps 1 to 3.
2. **The backend**: steps 4 and 5 there (Python packages, `backend/.env`, and a check that the model answers).
3. **Docker and Supabase**: [Start Supabase](#1-start-supabase-once-per-computer-restart), including the three values for `backend/.env`.
4. **The website**: [Start the website](#3-start-the-website-third-terminal) (`npm install` and `frontend/.env.local`).
5. Run `./kru check`. When nothing is marked `no`, start the four parts above.

## Project layout

```
kru/
├── prompts/        Prompt templates (edit these to improve summaries)
├── backend/
│   ├── app/        FastAPI routes, config, database access
│   ├── pipeline/   Text extraction, chunking, LLM calls, prompt loading
│   ├── worker/     Background job runner
│   └── tests/
├── supabase/       Database schema (migrations) and local Supabase settings
└── frontend/       The website (Next.js)
```

All settings live in `backend/.env` (copy it from `backend/.env.example`) and
are read in one place: `backend/app/config.py`.

---

## Setup: the AI model and the backend

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

## The app: database, API, and worker

So far the commands above run on one book and print the result. The app
works differently, because a summary takes too long for a web page to wait:

```
Browser ──upload──▶ API ──saves file + "queued" job──▶ Supabase (database + files)
Browser ◀─progress─ API ◀──────reads job────────────── Supabase
                                                          ▲
                         Worker ──takes job, summarizes, saves progress──┘
```

- **Supabase** stores users, books, jobs, chunk notes, summaries, and the
  uploaded files. It runs on your computer in Docker.
- **The API** (FastAPI) only does quick things: check the login, save an
  upload, answer "how far is my book?".
- **The worker** is a separate program that takes one job at a time from the
  queue and runs the same pipeline as `python -m pipeline.run`.

### 1. Start Supabase (once per computer restart)

You need Docker running (Docker Desktop or OrbStack) and the Supabase CLI:

```bash
brew install supabase/tap/supabase
cd kru                    # the project folder (it contains supabase/)
supabase start            # first time: downloads images, takes a few minutes
```

Then copy three values into `backend/.env`. This command shows them:

```bash
supabase status -o env | grep -E "^(API_URL|ANON_KEY|SERVICE_ROLE_KEY)="
```

| `supabase status` name | goes in `backend/.env` as |
|---|---|
| `API_URL` | `SUPABASE_URL` |
| `ANON_KEY` | `SUPABASE_ANON_KEY` |
| `SERVICE_ROLE_KEY` | `SUPABASE_SERVICE_ROLE_KEY` |

(It also lists newer-style `PUBLISHABLE_KEY` / `SECRET_KEY`; Kru is tested
with the three above.) The service role key can read and change everything,
so it stays in the backend only. On a local Supabase these keys are the same
well-known demo values for everyone; they only become real secrets if you
later use a hosted Supabase project.

Useful: **Studio** at http://127.0.0.1:54323 lets you look at the tables
in your browser. `supabase stop` stops everything (your data is kept).
`supabase db reset` wipes the local database and re-creates it from
`supabase/migrations/`.

### 2. Start the API and the worker (two terminals)

```bash
# Terminal 1: the API
cd backend && source .venv/bin/activate
uvicorn app.main:app --reload --port 8000

# Terminal 2: the worker
cd backend && source .venv/bin/activate
python -m worker.run
```

Check: http://localhost:8000/health should show
`{"database":"ok","llm":"ok"}`. The interactive API page is at
http://localhost:8000/docs.

If port 8000 is already used by another program, pick another
(`--port 8001`) and remember it for the frontend's `NEXT_PUBLIC_API_URL`.

### 3. Start the website (third terminal)

You need Node.js 20 or newer.

```bash
cd frontend
npm install                    # first time only
cp .env.example .env.local     # first time only, then fill in the anon key:
supabase status -o env | grep "^ANON_KEY="     # paste the value into .env.local
npm run dev
```

Open http://localhost:3000. Create an account, upload a book, and watch it
work. (On a local Supabase no confirmation email is sent; the account works
right away.)

`.env.local` holds only public values: the Supabase address, the anon key,
and the API address. The service role key must never go in the frontend.

If port 3000 is taken, use `npm run dev -- -p 3010` and set
`FRONTEND_ORIGIN=http://localhost:3010` in `backend/.env` (then restart
the API), or the browser will block the website's calls to the API.

The pages:

| Page | Address | What it does |
|---|---|---|
| Landing | `/` | What Kru does |
| Log in | `/login` | Log in or create an account |
| Upload | `/upload` | Drag and drop a book; checks type and size first |
| Progress | `/books/<id>` | Asks the API every 5 seconds: place in line, progress bar, time left, retry on failure |
| Summary | `/books/<id>/summary` | The summary with diagrams and charts, a list of sections, and a copy button |
| Library | `/library` | Your books with status and date; delete a book |

If the AI writes a diagram or chart that can't be drawn, the page shows
"Diagram could not be displayed" in its place and the rest of the summary
stays readable.

Check the code with `npm run lint` and `npm run build`.

### API endpoints

All except `/health` need the header `Authorization: Bearer <Supabase login token>`.

| Method | Path | What it does |
|---|---|---|
| POST | `/books` | Upload a file; creates the book and a queued job |
| GET | `/books` | The user's books, newest first |
| GET | `/books/{id}` | One book with job status, progress, queue position, time left |
| GET | `/books/{id}/summary` | The final summary (Markdown) |
| POST | `/books/{id}/retry` | Put a failed job back in the queue |
| DELETE | `/books/{id}` | Delete the book, its file, chunks, and summary |
| GET | `/health` | Checks the database and the model server |

### How it stays safe and reliable

- **Users only see their own books.** The database enforces it (Row Level
  Security), and the API checks ownership too. From the browser, users can
  only *read* their own rows; every change goes through the API.
- **Two workers can't take the same job.** The worker claims a job with
  `SELECT ... FOR UPDATE SKIP LOCKED` (the `claim_next_job` function in
  `supabase/migrations/`).
- **A stopped worker loses nothing.** Each chunk's notes are saved as soon
  as they are done. When the worker starts again, it puts unfinished jobs
  back in the queue and continues from the last finished chunk. Run only one
  worker process; use `WORKER_CONCURRENCY` for more books at once.
- **Uploaded files are deleted** when a book is done (or rejected as too long
  or scanned). Only the summary is kept. After a failure the file is kept so
  "retry" works.

### Tests

`pytest` runs everything. The tests in `tests/test_supabase_live.py` use
the real local Supabase to check the security rules and the job queue; they
are skipped automatically when Supabase isn't running, and never run against
a non-local `SUPABASE_URL`.

### Paid services (optional)

Free stays the default. If you want better or faster summaries and are happy
to pay per book, you can switch on a paid service. Kru refuses every paid
service until you set `LLM_ALLOW_PAID=true`, so nothing costs money by
accident.

Two ways to pay:

- **Claude** (`LLM_PROVIDER=anthropic`), through Anthropic's own connection.
- **Any paid model on OpenRouter** (GPT, Claude, Mistral, and many more) with
  one OpenRouter key: keep `LLM_PROVIDER=openrouter` and use a model id
  without `:free`.

**Switch to Claude:**

1. Create an account at https://console.anthropic.com, add credit (prepaid),
   and create an API key. Consider setting a monthly spending limit in the
   console.
2. In `backend/.env`:

   ```
   LLM_PROVIDER=anthropic
   LLM_ALLOW_PAID=true
   LLM_API_KEY=<your key>
   LLM_MODEL_CHUNK=claude-haiku-5-5      # notes for each part: the cheap model is enough
   LLM_MODEL_FINAL=claude-sonnet-5-5     # the final summary: a stronger model
   LLM_EFFORT=                           # optional: low, medium, high (lower = cheaper)
   ```

3. Run `python -m pipeline.check_llm` (it costs a fraction of a cent), then
   restart the API and the worker. The worker's first log line says
   `PAID AI service` so you always know.
4. In `frontend/.env.local`, set `NEXT_PUBLIC_AI_SERVICE=Claude`.

**What a book costs.** Prices per million tokens (sent / written), from
Anthropic's price list as of October 2026. Check
https://www.anthropic.com/pricing for current prices.

| Model | Price | A 300-page book, roughly |
|---|---|---|
| `claude-haiku-5-5` | $0.10 / $0.50 | a few cents |
| `claude-sonnet-5-5` | $2 / $10 | about $0.50 to $1 |
| `claude-opus-5-5` | $4 / $20 | about $1 to $2 |

The ranges include Claude's "thinking", which is billed like the answer;
`LLM_EFFORT=low` reduces it. Mixing models works well: Haiku for the ~30
notes calls and Sonnet for the one final summary costs little more than
Haiku alone. Kru asks Claude to reuse the system prompt between calls
(prompt caching), which makes those repeated parts about ten times cheaper.

Things to know about Claude:

- Claude's safety checks occasionally decline a passage (violent scenes in
  novels, for example). With Sonnet and Opus, Anthropic then retries on
  another Claude model automatically. Haiku has no such retry: the book
  stops with "Claude declined to work on part of this book". Try a
  different model.
- Your daily upload limit (`BOOKS_PER_DAY`) is also your spending limit. Keep
  it if other people can upload.
- The book's text is sent to Anthropic. Read Anthropic's terms for how API
  data is handled.

---

## Limits

| Limit | Default | Change it in `backend/.env` |
|---|---|---|
| Book length | 200,000 words (about 500 pages) | `MAX_WORDS` |
| File size | 50 MB | `MAX_FILE_MB` (and `NEXT_PUBLIC_MAX_FILE_MB` in `frontend/.env.local`) |
| Uploads per user | 3 per 24 hours | `BOOKS_PER_DAY` (0 = no limit) |
| Paid AI services | refused | `LLM_ALLOW_PAID=true` allows them |
| Books summarized at once | 1 | `WORKER_CONCURRENCY` |

The daily limit counts uploads, not books in the library, so deleting a book
does not give the upload back. An upload that is refused straight away (wrong
file type, too big) does not count. Restart the API after changing a value.

While testing on your own computer you will hit the daily limit quickly: set
`BOOKS_PER_DAY=0`.

## If something goes wrong

Start with `./kru check`. It finds most of these.

| What you see | Likely cause | Fix |
|---|---|---|
| Website: "Can't reach Kru right now" | The API is not running | `./kru api` |
| A book stays at "You're next in line", then "Kru may be paused" | The worker is not running | `./kru worker` |
| Progress stops moving for a long time | The worker was stopped, or the Mac went to sleep | Start the worker again; it continues where it stopped. For long books, run `caffeinate -i ./kru worker` to keep the Mac awake |
| "Kru had to stop": cannot reach the AI model | Ollama is not running | `brew services start ollama`, then press "Try again" on the page |
| "Kru had to stop": the model was not found | Wrong model name | Fix `LLM_MODEL_CHUNK` / `LLM_MODEL_FINAL` in `backend/.env` (`ollama list` shows the names), restart the worker |
| "Ollama loaded ... with a ...-token context" | Ollama's context is smaller than `LLM_CONTEXT_TOKENS` | See [Context length](#3-context-length-important) |
| Login or sign up fails with a network error | Supabase is not running | `./kru db` (Docker must be running first) |
| `/health` shows `"database": "error..."` | Supabase is not running, or wrong values in `backend/.env` | `./kru db`, then compare with `supabase status -o env` |
| "address already in use" when starting the API or website | Another program uses port 8000 or 3000 | Stop that program, or see the port notes in the sections above |
| The website's calls fail, and the browser console mentions CORS | The website's address is not the one the API expects | Set `FRONTEND_ORIGIN` in `backend/.env` to the website's address, restart the API |
| "You can upload 3 books per day" | The daily limit | Wait, or set `BOOKS_PER_DAY=0` for local testing |
| A diagram shows "Diagram could not be displayed" | The AI wrote a diagram with a mistake in it | Nothing is broken. A bigger model makes fewer of these mistakes |

The worker's terminal shows one line per model call and the full details of
any error. That is the first place to look when a book fails.

## Putting Kru online later (not done yet)

Kru runs on your own computer today, and nothing here has been deployed.
These are the options when you want other people to use it. All have a free
starting point.

**The hard part is the AI model.** The website, API, and database are easy
to host for free. A 14-billion-parameter model is not: it needs a strong
graphics card or an Apple Silicon Mac, and free cloud servers have neither.
So the question for any deployment is "where does the model run?"

| Option | Where things run | Cost | Good for |
|---|---|---|---|
| A. Your Mac as the server | Everything on your Mac, reachable through a tunnel (Cloudflare Tunnel or Tailscale Funnel) | Free | Friends and beta testers. Works only while your Mac is on |
| B. Hosted site, model at home | Website on Vercel, database on Supabase's free cloud plan, API on a free small server (Render, Fly.io); the **worker and Ollama stay on your Mac** and pull jobs from the cloud database | Free | A real web address with the quality of your local model. Books wait while your Mac is off |
| C. Fully hosted, free model API | Same as B, but the worker also runs on a small server and uses a free online AI service (see [Using an online AI service](#using-an-online-ai-service)) | Free, with strict daily limits | Small experiments. A long book may not fit in one day's allowance |
| D. Fully hosted, your own GPU server | A rented GPU machine runs Ollama and the worker | Paid, by the hour | Many users. This is the first option that costs money |

Option B fits Kru's design best: the worker only needs to reach the database,
so it can run anywhere, including at home.

Things to do before any of these:

- **Check PyMuPDF's license.** Kru reads PDFs with PyMuPDF, which is free
  under the AGPL license. If you offer Kru to others as a web service, the
  AGPL asks you to publish Kru's source code (or buy a commercial license).
  The alternative is to switch to `pypdf`, which has a permissive license
  but extracts text less well.
- **Use a hosted Supabase project** and run the files in
  `supabase/migrations/` on it (`supabase link`, then `supabase db push`).
  Its keys are real secrets, unlike the local ones: keep the service role
  key only on the server, never in the website or in git.
- **Turn on email confirmation** in Supabase's auth settings, and set its
  site address to your real web address. Locally it is off, so accounts work
  right away.
- **Set the addresses**: `FRONTEND_ORIGIN` for the API,
  `NEXT_PUBLIC_API_URL` and the Supabase values for the website. Serve
  everything over HTTPS.
- **Keep the limits.** `BOOKS_PER_DAY` and `MAX_WORDS` are what stop one
  person from keeping the worker busy all day. One local model summarizes
  about two 300-page books per hour.
- **Think about what users upload.** Books are usually copyrighted. Kru
  deletes each file after summarizing and shows summaries only to the person
  who uploaded, which helps, but a public service should have terms of use.
- **Run the API without `--reload`** (that flag is for development), and
  keep one worker process.

---

## Using an online AI service

By default the AI runs on your own computer with Ollama. Instead, Kru can use
the free tier of an online AI service, or (only if you switch it on) a paid
service such as Claude. This is useful when your computer is
too slow for a good model, or when you want better summaries than a local
model gives. You choose one service for the whole app in `backend/.env`.

| `LLM_PROVIDER` | Service | Get a free key | Free models |
|---|---|---|---|
| `ollama` (default) | Your own computer | none needed | any model you `ollama pull` |
| `openrouter` | [OpenRouter](https://openrouter.ai) | openrouter.ai → Keys | only ids ending in `:free` (Kru refuses the others) |
| `groq` | [Groq](https://console.groq.com) | console.groq.com → API Keys | the models on a free account |
| `gemini` | Google Gemini | [aistudio.google.com/apikey](https://aistudio.google.com/apikey) | the models on the free tier |
| `anthropic` | Claude, **paid** | [console.anthropic.com](https://console.anthropic.com) | none: see [Paid services](#paid-services-optional) |
| `custom` | Any other OpenAI-compatible server (e.g. LM Studio) | depends | set `LLM_BASE_URL` too |

### Switch to an online service

1. Create a free account and an API key on the service's website.
2. In `backend/.env`, set three things (example for Gemini):

   ```
   LLM_PROVIDER=gemini
   LLM_API_KEY=<your key>
   LLM_MODEL_CHUNK=<model name>
   LLM_MODEL_FINAL=<model name>
   ```

   Not sure of the model name? Put any name in and run the check below: when
   the model isn't found, it lists the ones you can use.
3. Check that it works:

   ```bash
   cd backend && source .venv/bin/activate
   python -m pipeline.check_llm
   ```

   It should end with `All checks passed.` If it says the key was not
   accepted, copy the key again (without spaces).
4. Restart the API and the worker (`./kru api`, `./kru worker`).
5. Tell the website which service it is, so visitors know where their book
   goes: in `frontend/.env.local` set `NEXT_PUBLIC_AI_SERVICE=Google Gemini`
   (the service's name), then restart the website.

To go back to your own computer: `LLM_PROVIDER=ollama`,
`LLM_API_KEY=ollama`, and your Ollama model names; empty
`NEXT_PUBLIC_AI_SERVICE`.

### Things to know before you switch

- **Your book's text leaves your computer.** Kru sends every part of the
  book to the service. Free tiers may keep what you send, and some use it to
  improve their models (Google says so for Gemini's free tier, for example).
  Read the service's terms before using books that are private or that
  belong to someone else. With Ollama, nothing leaves your computer.
- **Free tiers have limits per minute and per day.** Kru waits and retries
  when it hits a per-minute limit. When a daily limit is reached, the book
  stops with "The free daily limit of ... has been reached" and keeps its
  progress: press "Try again" the next day and it continues where it stopped.
  A 300-page book needs about 30 model calls.
- **Some free tiers limit how big one request can be.** If you see "one
  request is too large for its free tier", lower `CHUNK_TARGET_WORDS` (for
  example to 2500) and `LLM_CONTEXT_TOKENS` (for example to 8192).
- **Kru never uses paid models unless you allow it.** Without
  `LLM_ALLOW_PAID=true` it stops with a message instead of calling Claude or
  a paid OpenRouter model. Groq and Gemini are free as long as your account
  has no billing set up: don't add a payment method to the account you use
  for Kru.
- **Free model lists change often.** If a model disappears, the check above
  lists the current ones.
- Online models are usually much faster than a local one, so a book can
  finish in a few minutes, rate limits permitting.
