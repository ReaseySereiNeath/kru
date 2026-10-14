"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import { SummarySkeleton } from "@/components/skeleton";
import { SummaryMarkdown } from "@/components/summary-markdown";
import { ApiError, type Summary, getSummary } from "@/lib/api";
import { extractHeadings, formatDate, type Heading } from "@/lib/format";

export function SummaryView() {
  const { id } = useParams<{ id: string }>();
  const [summary, setSummary] = useState<Summary | null>(null);
  const [error, setError] = useState<{ status: number; message: string } | null>(null);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    let cancelled = false;
    getSummary(id)
      .then((result) => !cancelled && setSummary(result))
      .catch((e) => {
        if (cancelled) return;
        setError(
          e instanceof ApiError
            ? { status: e.status, message: e.message }
            : { status: 0, message: "Couldn’t load the summary." },
        );
      });
    return () => {
      cancelled = true;
    };
  }, [id]);

  const handleCopy = async () => {
    if (!summary) return;
    await navigator.clipboard.writeText(summary.markdown);
    setCopied(true);
    setTimeout(() => setCopied(false), 2500);
  };

  if (error) {
    return (
      <div className="mx-auto max-w-2xl px-5 py-12">
        <h1 className="text-4xl font-extrabold tracking-tight">
          {error.status === 404 ? "No summary here yet" : "Couldn’t load the summary"}
        </h1>
        <p className="mt-4 font-serif text-lg text-slate">
          {error.status === 404
            ? "This book is still being summarized, or it doesn’t exist."
            : error.message}
        </p>
        <p className="mt-6 flex flex-wrap gap-x-6 gap-y-2 font-semibold text-pen">
          {error.status === 404 && (
            <Link href={`/books/${id}`} className="underline underline-offset-2">
              See its progress
            </Link>
          )}
          <Link href="/library" className="underline underline-offset-2">
            Back to my library
          </Link>
        </p>
      </div>
    );
  }

  if (!summary) return <SummarySkeleton />;

  const { book, markdown } = summary;
  const headings = extractHeadings(markdown);
  const facts = [
    book.book_type === "fiction" ? "Fiction" : "Learning",
    book.subject,
    book.page_estimate ? `about ${book.page_estimate} pages` : null,
    `summarized ${formatDate(summary.created_at)}`,
  ].filter(Boolean);

  return (
    <div className="mx-auto max-w-5xl px-5 py-10">
      <header className="border-b border-line pb-7">
        <h1 className="max-w-3xl text-4xl leading-tight font-extrabold tracking-tight break-words sm:text-5xl">
          {book.title}
        </h1>
        {book.author !== "Unknown" && (
          <p className="mt-2 font-serif text-xl text-slate">by {book.author}</p>
        )}
        <p className="mt-3 text-sm text-slate">{facts.join(", ")}</p>
        <button
          onClick={handleCopy}
          className="mt-5 cursor-pointer rounded-lg border border-pen px-4 py-2 font-semibold text-pen hover:bg-pen-soft"
        >
          <span aria-live="polite">{copied ? "Copied" : "Copy summary"}</span>
        </button>
      </header>

      <div className="mt-8 gap-12 lg:grid lg:grid-cols-[13rem_minmax(0,1fr)]">
        {headings.length > 1 && <Contents headings={headings} />}
        <article className="min-w-0">
          <SummaryMarkdown markdown={markdown} />
          <p className="mt-12 max-w-[68ch] border-t border-line pt-5 text-sm text-slate">
            Written by the AI model {summary.model}. It can get details wrong, so check the book
            for anything that matters.
          </p>
        </article>
      </div>
    </div>
  );
}

// The list of sections: a sidebar that stays in view on wide screens, and a
// fold-out list at the top on phones.
function Contents({ headings }: { headings: Heading[] }) {
  const links = (
    <ul className="space-y-2 text-[0.9375rem]">
      {headings.map((heading) => (
        <li key={heading.id}>
          <a href={`#${heading.id}`} className="block rounded text-slate hover:text-pen">
            {heading.text}
          </a>
        </li>
      ))}
    </ul>
  );
  return (
    <>
      <details className="mb-8 rounded-lg border border-line bg-sheet px-4 py-3 lg:hidden">
        <summary className="cursor-pointer font-semibold">In this summary</summary>
        <nav aria-label="Summary sections" className="mt-3">
          {links}
        </nav>
      </details>
      <nav aria-label="Summary sections" className="hidden lg:block">
        <div className="sticky top-24">
          <p className="mb-3 font-bold">In this summary</p>
          {links}
        </div>
      </nav>
    </>
  );
}
