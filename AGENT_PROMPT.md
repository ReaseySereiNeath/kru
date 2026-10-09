# Project: Kru — your book teacher (AI book summary web app, free open-source models only)

## What we are building
Kru ("teacher" in Thai and Khmer) is a web app where users upload a book
(up to ~500 pages) and get an easy-to-understand summary. It serves two kinds
of readers:
- Readers who want the whole story of a novel without reading it all.
- Learners (e.g. coding or business books) who struggle to find the core concepts.

The summary must be concise, written in plain language, and include visuals
(Mermaid diagrams and simple charts) to help understanding.

IMPORTANT CONSTRAINTS
- I have no paid AI API. The app must use free, open-source AI models only.
- The default setup runs everything on my own computer (local-first).
- Everything in the stack must have a free option.

I am building this step by step and want to understand the code. Explain your
decisions briefly as you go, in simple language.

## Tech stack (use exactly this unless you have a strong reason; ask me first)
- Frontend: Next.js (App Router) + TypeScript + Tailwind CSS
  - Render summaries with react-markdown
  - Render ```mermaid blocks with mermaid.js
  - Render ```chart blocks (JSON spec) with Chart.js
- Backend: Python 3.11+ with FastAPI
- Background worker: a separate Python process (same codebase as the backend)
- Database, auth, file storage: Supabase (free tier)
- Text extraction: PyMuPDF for PDF, ebooklib + BeautifulSoup for EPUB,
  plain read for TXT
- AI model: open-source models only, no paid APIs.
  - Default: Ollama running locally, using its OpenAI-compatible endpoint
    (http://localhost:11434/v1). Use the `openai` Python package with a
    configurable base URL, so I can switch providers later by changing only
    environment variables.
  - Optional fallback: OpenRouter free models (IDs ending in ":free"). These
    have strict daily request limits. Handle HTTP 429 errors by waiting and
    retrying, and show the user a clear message if the daily limit is reached.

## Folder structure
/frontend      Next.js app
/backend
  /app         FastAPI routes, config, database access
  /pipeline    text extraction, chunking, LLM calls, prompt loading
  /worker      background job runner
  /tests
/prompts       prompt templates (already written by me — see list below).
               Load them from these files. Do not rewrite them without asking.
               Placeholders use {curly_braces}. Be careful: the prompts also
               contain literal JSON braces, so fill placeholders with simple
               string replacement of known names, NOT Python str.format().
README.md      setup and run instructions

Prompt files:
- prompts/0_book_check.txt     detect book type and chapters (returns JSON)
- prompts/1_system.txt         system prompt for every summary call
- prompts/2_chunk_summary.txt  notes for one chunk
- prompts/2b_merge_notes.txt   combine batches of notes when they are too long
- prompts/3_final_summary.txt  the final summary

## Configuration (all via environment variables; provide .env.example files)
LLM_BASE_URL=http://localhost:11434/v1
LLM_API_KEY=ollama              (Ollama ignores it, but the client needs a value)
LLM_MODEL_CHUNK=<model name from `ollama list`>
LLM_MODEL_FINAL=<model name; same model, or a larger one if my machine allows>
LLM_CONTEXT_TOKENS=16384
LLM_TEMPERATURE=0.3
CHUNK_TARGET_WORDS=4000         (I will test values between 2000 and 5000)
CHUNK_NOTES_MAX_WORDS=250
MERGE_NOTES_MAX_WORDS=400
WORKER_CONCURRENCY=1
MAX_WORDS=200000
MAX_FILE_MB=50
SUPABASE_URL, SUPABASE_ANON_KEY, SUPABASE_SERVICE_ROLE_KEY
Never hard-code secrets. Never commit .env files.

## How the summary pipeline works
1. Upload: accept PDF, EPUB, or TXT. Reject files over MAX_FILE_MB.
2. Extract text. Count words. Estimate pages as words / 400.
   - If words > MAX_WORDS, mark the book "too_long" and tell the user the limit
     (about 500 pages).
   - If a PDF has very little extractable text (under ~50 words per page on
     average), mark it "scanned_not_supported". No OCR in this version.
3. Book check: send the table of contents (or the first ~3,000 words if none is
   found) to prompts/0_book_check.txt. Request JSON output (Ollama supports
   "format": "json"). Strip ``` fences before parsing. If parsing fails, retry
   once asking for valid JSON only. Save book_type, subject, reader_level,
   chapters, skip_sections.
4. Chunking: split by chapter when chapters are detected; otherwise split by
   paragraphs. Target CHUNK_TARGET_WORDS per chunk. Combine short chapters;
   split long ones at paragraph boundaries. Remove sections in skip_sections.
5. Chunk summaries: for each chunk, in order, call prompts/2_chunk_summary.txt
   (with prompts/1_system.txt as the system prompt) using LLM_MODEL_CHUNK and
   {max_words} = CHUNK_NOTES_MAX_WORDS. Pass the previous chunk's "Carryover"
   section into the next call. Save each chunk's notes as soon as it finishes,
   and update job progress (e.g. "7 of 50").
6. Merge step (only when needed): estimate the token count of all chunk notes.
   If they do not fit in the final prompt's budget (see "Context safety"),
   group consecutive notes into batches that fit, and summarize each batch with
   prompts/2b_merge_notes.txt ({max_words} = MERGE_NOTES_MAX_WORDS).
   Repeat until everything fits.
7. Final summary: call prompts/3_final_summary.txt with the (merged) notes using
   LLM_MODEL_FINAL. Save the Markdown result.

## Context safety (very important for small local models)
- Before every LLM call, estimate the prompt's token count (approx. words x 1.4).
- Budget = LLM_CONTEXT_TOKENS minus 2,500 tokens reserved for the answer.
- If a chunk prompt exceeds the budget, split that chunk further instead of
  sending it. Never send a prompt that could be silently cut off.
- Pass num_ctx = LLM_CONTEXT_TOKENS to Ollama where possible, and document in
  the README how to set OLLAMA_CONTEXT_LENGTH as a backup.

## Resilience
- Retry each LLM call up to 3 times with exponential backoff (longer waits for
  HTTP 429 rate-limit errors).
- If a job crashes or the computer restarts, it must resume from the last
  completed chunk, not start over.
- Log time taken and token usage per call; store totals per book.

## Background jobs
- The API must never run the pipeline inside a web request. It only creates a job.
- The worker polls the jobs table and claims jobs safely using
  SELECT ... FOR UPDATE SKIP LOCKED.
- Respect WORKER_CONCURRENCY (default 1: one book at a time).
- Job statuses: queued, extracting, analyzing, summarizing, merging, finalizing,
  done, failed, too_long, scanned_not_supported.
- Store a human-friendly progress message and error message on the job.
- Show queue position to users whose job is waiting.

## Database tables (propose the exact SQL schema and Row Level Security policies)
- books: id, user_id, title, author, file_path, word_count, page_estimate,
  book_type, subject, reader_level, status, total_tokens, total_seconds,
  created_at
- jobs: id, book_id, status, progress_current, progress_total,
  progress_message, error, attempts, created_at, updated_at
- chunks: id, book_id, chunk_index, chapter_titles, word_count, notes,
  carryover, status, seconds_taken
- summaries: id, book_id, markdown, model, created_at
Users must only ever see their own books (enforce with Supabase RLS).
Delete the uploaded book file from storage after processing finishes
(we keep only the summary).

## API endpoints (FastAPI, authenticated with the Supabase user token)
- POST   /books                 upload a file, create book + job
- GET    /books                 list the user's books
- GET    /books/{id}            book details + job status
- GET    /books/{id}/summary    final summary Markdown
- POST   /books/{id}/retry      retry a failed job
- DELETE /books/{id}            delete book, chunks, summary
- GET    /health                checks database and LLM connection

## Frontend pages
Brand: the app is called "Kru", tagline "Your book teacher". Friendly, calm,
trustworthy look.
1. Landing page: what Kru does, sign up / log in.
2. Upload page: drag-and-drop, file type and size checks, clear limit message
   ("Books up to about 500 pages"). Warn that long books can take a while.
3. Progress page: polls status every 5 seconds, shows progress bar, message,
   queue position, and estimated time remaining (based on average seconds per
   chunk so far). Friendly error states with a retry button.
4. Summary page: renders the Markdown with Mermaid and Chart.js blocks,
   a table of contents for the summary sections, and a "copy" button.
   If a Mermaid diagram or chart fails to render, show "Diagram could not be
   displayed" instead of breaking the page.
5. My library: list of the user's books with status and date.
Design: clean, readable (good line length, clear headings), works on mobile.

## Build in phases. STOP after each phase, show me how to run and test it,
## and wait for my approval before starting the next phase.
Phase 1: Project setup, folder structure, .env.example files, and a README that
         explains how to install Ollama, download a model, set the context
         length, and check that it works. Include a small script that sends a
         test message to the model and prints the reply and the time taken.
Phase 2: Text extraction + word count + chunking, as a command-line script.
         Include tests with a small sample PDF and EPUB.
Phase 3: Full pipeline as a command-line script:
         `python -m pipeline.run path/to/book.pdf` prints the final summary,
         plus time per chunk and total time.
         (This lets me check summary quality and speed before any UI exists.)
Phase 4: Supabase schema, RLS policies, FastAPI endpoints, background worker.
Phase 5: Frontend: landing, upload, progress, summary, and library pages.
Phase 6: Polish: error handling, loading states, mobile layout, basic rate
         limits (e.g. 3 books per user per day), and clear instructions for
         running the whole app on my own computer with one command per part.
         Add a short README section on future deployment options, but do not
         deploy anything.

## Rules for you (the agent)
- Before writing code in Phase 1, list any questions or assumptions you have.
- Keep the code simple and readable. Prefer clear code over clever code.
- Add short comments explaining non-obvious parts.
- Write tests for the pipeline logic (extraction, chunking, JSON parsing,
  placeholder filling, context budget checks, merge step, resume after crash).
- Never add paid services or paid APIs.
- Do not add features I did not ask for. Suggest them at the end of a phase.
- If something in this plan seems wrong or risky, tell me before changing it.
