"use client";

// Shown if a page crashes while drawing. Without this file the visitor
// would see a blank page. "Try again" draws the page once more.
import Link from "next/link";

export default function ErrorPage({ reset }: { error: Error; reset: () => void }) {
  return (
    <div className="mx-auto max-w-2xl px-5 py-16">
      <h1 className="text-4xl font-extrabold tracking-tight">This page hit a problem</h1>
      <p className="mt-4 font-serif text-lg text-slate">
        Your books and summaries are safe. Try loading the page again.
      </p>
      <div className="mt-6 flex flex-wrap items-center gap-x-6 gap-y-3">
        <button
          onClick={reset}
          className="cursor-pointer rounded-lg bg-pen px-6 py-3 text-lg font-semibold text-white hover:bg-pen-dark"
        >
          Try again
        </button>
        <Link href="/library" className="font-semibold text-pen underline underline-offset-2">
          Go to my library
        </Link>
      </div>
    </div>
  );
}
