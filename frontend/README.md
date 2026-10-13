# Kru website

The Next.js app for Kru. Setup and run instructions are in the main
[README](../README.md), section "The website".

- `app/` — one folder per page (`page.tsx`): landing, login, upload, library,
  `books/[id]` (progress) and `books/[id]/summary`.
- `components/` — shared pieces: login state, header, the summary renderer,
  diagrams and charts.
- `lib/` — the API client, the Supabase client, and small text helpers.
- `app/globals.css` — colours, fonts, the highlighter effect, summary text styles.
