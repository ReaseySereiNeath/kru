"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { RequireAuth } from "@/components/auth";
import { StatusBadge } from "@/components/status-badge";
import { ApiError, type Book, deleteBook, listBooks } from "@/lib/api";
import { formatDate, isInProgress } from "@/lib/format";

const REFRESH_MS = 10_000;

function Library() {
  const [books, setBooks] = useState<Book[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Changing this number makes the effect below fetch the list again.
  const [reload, setReload] = useState(0);
  const refresh = () => setReload((n) => n + 1);

  useEffect(() => {
    let cancelled = false;
    listBooks()
      .then((list) => {
        if (cancelled) return;
        setBooks(list);
        setError(null);
      })
      .catch((e) => {
        if (cancelled) return;
        setError(e instanceof ApiError ? e.message : "Couldn’t load your library.");
      });
    return () => {
      cancelled = true;
    };
  }, [reload]);

  // While a book is being worked on, refresh the list so its status stays current.
  const anyInProgress = books?.some((b) => isInProgress(b.status)) ?? false;
  useEffect(() => {
    if (!anyInProgress) return;
    const timer = setInterval(refresh, REFRESH_MS);
    return () => clearInterval(timer);
  }, [anyInProgress]);

  const handleDelete = async (book: Book) => {
    if (!window.confirm(`Delete “${book.title}” and its summary? This can’t be undone.`)) return;
    try {
      await deleteBook(book.id);
      setBooks((current) => current?.filter((b) => b.id !== book.id) ?? null);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Couldn’t delete the book.");
    }
  };

  return (
    <div className="mx-auto max-w-3xl px-5 py-12">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <h1 className="text-4xl font-extrabold tracking-tight">My library</h1>
        <Link
          href="/upload"
          className="rounded-lg bg-pen px-5 py-2.5 font-semibold text-white hover:bg-pen-dark"
        >
          Upload a book
        </Link>
      </div>

      {error && (
        <p role="alert" className="mt-6 rounded-lg bg-danger-soft px-4 py-3 text-danger">
          {error}{" "}
          <button onClick={refresh} className="cursor-pointer font-semibold underline">
            Try again
          </button>
        </p>
      )}

      {books === null && !error && <p className="mt-10 text-slate">Loading your books…</p>}

      {books?.length === 0 && (
        <div className="mt-10 rounded-xl border border-line bg-sheet px-6 py-12 text-center">
          <p className="text-xl font-bold">No books yet</p>
          <p className="mt-2 font-serif text-slate">
            Upload your first book and its summary will appear here.
          </p>
        </div>
      )}

      {books && books.length > 0 && (
        <ul className="mt-8 divide-y divide-line border-y border-line">
          {books.map((book) => (
            <li key={book.id} className="flex items-start justify-between gap-4 py-5">
              <div className="min-w-0">
                {/* A finished book opens its summary; any other opens its progress page. */}
                <Link
                  href={book.status === "done" ? `/books/${book.id}/summary` : `/books/${book.id}`}
                  className="rounded text-xl leading-snug font-bold break-words hover:text-pen"
                >
                  {book.title}
                </Link>
                <p className="mt-0.5 font-serif text-slate">{book.author}</p>
                <p className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-slate">
                  <StatusBadge status={book.status} />
                  <span>Added {formatDate(book.created_at)}</span>
                  {book.page_estimate && <span>about {book.page_estimate} pages</span>}
                </p>
              </div>
              <button
                onClick={() => handleDelete(book)}
                aria-label={`Delete ${book.title}`}
                className="shrink-0 cursor-pointer rounded px-2.5 py-1.5 text-sm text-slate hover:bg-danger-soft hover:text-danger"
              >
                Delete
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export default function LibraryPage() {
  return (
    <RequireAuth>
      <Library />
    </RequireAuth>
  );
}
