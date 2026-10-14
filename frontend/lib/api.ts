// Every call to the Kru API (the FastAPI backend) goes through this file.
// Each request carries the user's Supabase login token, which the API checks.
import { getSupabase } from "./supabase";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export type JobStatus =
  | "queued"
  | "extracting"
  | "analyzing"
  | "summarizing"
  | "merging"
  | "finalizing"
  | "done"
  | "failed"
  | "too_long"
  | "scanned_not_supported";

export type Job = {
  status: JobStatus;
  progress_current: number;
  progress_total: number;
  progress_message: string;
  error: string | null;
  attempts: number;
  queue_position: number | null;
  seconds_per_chunk: number | null;
  eta_seconds: number | null;
  updated_at: string;
};

export type Book = {
  id: string;
  title: string;
  author: string;
  file_type: "pdf" | "epub" | "txt";
  word_count: number | null;
  page_estimate: number | null;
  book_type: "fiction" | "learning" | null;
  subject: string | null;
  reader_level: string | null;
  status: JobStatus;
  total_tokens: number;
  total_seconds: number;
  created_at: string;
};

export type BookWithJob = Book & { job: Job | null };

export type Summary = {
  book: Book;
  markdown: string;
  model: string;
  created_at: string;
};

// An error from the API, with a message that is safe to show to the user.
export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

const UNREACHABLE = "Can't reach Kru right now. Check that the API is running, then try again.";

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const { data } = await getSupabase().auth.getSession();
  const token = data.session?.access_token;
  if (!token) throw new ApiError(401, "Please log in.");

  let response: Response;
  try {
    response = await fetch(API_URL + path, {
      ...init,
      headers: { ...init.headers, Authorization: `Bearer ${token}` },
    });
  } catch {
    // fetch only throws when there is no answer at all (server down, no network).
    throw new ApiError(0, UNREACHABLE);
  }

  if (response.status === 401) {
    // The login is no longer valid (it expired, or the account is gone).
    // Logging out here makes <RequireAuth> send the user to the login page.
    await getSupabase().auth.signOut();
  }
  if (!response.ok) {
    // The API sends errors as {"detail": "a message for the user"}.
    const body = await response.json().catch(() => null);
    const detail = typeof body?.detail === "string" ? body.detail : "Something went wrong.";
    throw new ApiError(response.status, detail);
  }
  if (response.status === 204) return undefined as T; // "done, nothing to send back"
  return response.json();
}

export function uploadBook(file: File): Promise<BookWithJob> {
  const form = new FormData();
  form.append("file", file);
  return request("/books", { method: "POST", body: form });
}

export const listBooks = () => request<Book[]>("/books");
export const getBook = (id: string) => request<BookWithJob>(`/books/${id}`);
export const getSummary = (id: string) => request<Summary>(`/books/${id}/summary`);
export const retryBook = (id: string) =>
  request<BookWithJob>(`/books/${id}/retry`, { method: "POST" });
export const deleteBook = (id: string) => request<void>(`/books/${id}`, { method: "DELETE" });
