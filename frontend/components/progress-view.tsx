"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { ApiError, type BookWithJob, getBook, retryBook } from "@/lib/api";
import { formatTimeLeft, isInProgress, progressPercent } from "@/lib/format";

const POLL_MS = 5000;

// The steps shown under the progress bar, with the statuses each one covers.
const STEPS = [
  { label: "Reading the book", statuses: ["extracting", "analyzing"] },
  { label: "Taking notes on each part", statuses: ["summarizing", "merging"] },
  { label: "Writing the summary", statuses: ["finalizing"] },
];

export function ProgressView() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const [book, setBook] = useState<BookWithJob | null>(null);
  const [notFound, setNotFound] = useState(false);
  const [offline, setOffline] = useState(false); // the API didn't answer
  const [retrying, setRetrying] = useState(false);
  const [retryError, setRetryError] = useState<string | null>(null);

  const status = book?.job?.status ?? book?.status;
  // Keep asking while the book is waiting or being worked on (or not loaded yet).
  const keepPolling = !notFound && (status === undefined || isInProgress(status));

  useEffect(() => {
    if (!keepPolling) return;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;

    const poll = async () => {
      try {
        const latest = await getBook(id);
        if (stopped) return;
        setBook(latest);
        setOffline(false);
      } catch (e) {
        if (stopped) return;
        if (e instanceof ApiError && e.status === 404) {
          setNotFound(true);
          return;
        }
        setOffline(true); // keep the last known state on screen and try again
      }
      timer = setTimeout(poll, POLL_MS);
    };
    poll();

    // Runs when the page is left, so we don't keep asking in the background.
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
  }, [id, keepPolling]);

  // When the summary is ready, go straight to it.
  useEffect(() => {
    if (status === "done") router.replace(`/books/${id}/summary`);
  }, [status, id, router]);

  const handleRetry = async () => {
    setRetrying(true);
    setRetryError(null);
    try {
      setBook(await retryBook(id)); // back to "queued", so polling starts again
    } catch (e) {
      setRetryError(e instanceof ApiError ? e.message : "Couldn’t start again.");
    }
    setRetrying(false);
  };

  if (notFound) {
    return (
      <Shell title="Book not found">
        <p className="font-serif text-lg text-slate">
          This book doesn’t exist, or it was deleted.
        </p>
        <LibraryLink />
      </Shell>
    );
  }

  if (!book || !book.job) {
    return (
      <Shell title="Loading…">
        {offline && <OfflineNote />}
      </Shell>
    );
  }

  const job = book.job;

  if (job.status === "failed") {
    return (
      <Shell title={book.title} subtitle="Kru had to stop">
        <p role="alert" className="rounded-lg bg-danger-soft px-4 py-3 font-serif text-danger">
          {job.error ?? "Something went wrong while summarizing this book."}
        </p>
        <p className="mt-4 font-serif text-slate">
          The notes made so far are saved. Trying again continues from where it stopped.
        </p>
        {retryError && (
          <p role="alert" className="mt-4 text-danger">
            {retryError}
          </p>
        )}
        <button
          onClick={handleRetry}
          disabled={retrying}
          className="mt-6 cursor-pointer rounded-lg bg-pen px-6 py-3 text-lg font-semibold text-white hover:bg-pen-dark disabled:opacity-60"
        >
          {retrying ? "Starting…" : "Try again"}
        </button>
        <LibraryLink />
      </Shell>
    );
  }

  if (job.status === "too_long" || job.status === "scanned_not_supported") {
    return (
      <Shell
        title={book.title}
        subtitle={job.status === "too_long" ? "This book is too long" : "This PDF is scanned"}
      >
        <p className="font-serif text-lg text-slate">
          {job.error}
          {job.status === "too_long" && book.page_estimate
            ? ` This one is about ${book.page_estimate} pages.`
            : ""}
        </p>
        <Link
          href="/upload"
          className="mt-6 inline-block rounded-lg bg-pen px-6 py-3 text-lg font-semibold text-white hover:bg-pen-dark"
        >
          Upload a different book
        </Link>
        <LibraryLink />
      </Shell>
    );
  }

  if (job.status === "done") {
    return <Shell title={book.title} subtitle="Opening your summary…" />;
  }

  const percent = progressPercent(job.status, job.progress_current, job.progress_total);
  const queued = job.status === "queued";
  const activeStep = STEPS.findIndex((step) => step.statuses.includes(job.status));

  return (
    <Shell
      title={book.title}
      subtitle={book.author !== "Unknown" ? `by ${book.author}` : undefined}
    >
      <p className="text-xl font-bold" aria-live="polite">
        {queued
          ? job.queue_position === 1
            ? "You’re next in line"
            : `Waiting in line: number ${job.queue_position}`
          : job.progress_message || "Working on it"}
      </p>

      <div
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={percent}
        aria-label="Summary progress"
        className="mt-4 h-4 overflow-hidden rounded-full bg-line"
      >
        <div
          className="h-full rounded-full bg-pen transition-[width] duration-700"
          style={{ width: `${Math.max(percent, 2)}%` }}
        />
      </div>

      <p className="mt-3 min-h-6 text-slate">
        {queued
          ? "Books are summarized one at a time. Yours starts when the ones before it finish."
          : job.eta_seconds !== null
            ? `${formatTimeLeft(job.eta_seconds)} left`
            : "Working out how long this will take…"}
      </p>

      <ol className="mt-8 space-y-3">
        {STEPS.map((step, i) => {
          const done = !queued && i < activeStep;
          const active = !queued && i === activeStep;
          return (
            <li key={step.label} className="flex items-center gap-3">
              <span
                aria-hidden
                className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-sm font-bold ${
                  done
                    ? "bg-pen text-white"
                    : active
                      ? "bg-mark text-ink"
                      : "border border-line text-faded"
                }`}
              >
                {done ? "✓" : i + 1}
              </span>
              <span className={active ? "font-bold" : done ? "text-slate" : "text-faded"}>
                {step.label}
                {active && job.status === "summarizing" && job.progress_total > 0
                  ? ` (${job.progress_current} of ${job.progress_total} done)`
                  : ""}
              </span>
            </li>
          );
        })}
      </ol>

      {offline && <OfflineNote />}

      <p className="mt-10 font-serif text-slate">
        You can close this page. The summary will be waiting in{" "}
        <Link href="/library" className="font-semibold text-pen underline underline-offset-2">
          your library
        </Link>
        .
      </p>
    </Shell>
  );
}

function Shell({
  title,
  subtitle,
  children,
}: {
  title: string;
  subtitle?: string;
  children?: React.ReactNode;
}) {
  return (
    <div className="mx-auto max-w-2xl px-5 py-12">
      <h1 className="text-4xl leading-tight font-extrabold tracking-tight break-words">{title}</h1>
      {subtitle && <p className="mt-2 font-serif text-lg text-slate">{subtitle}</p>}
      <div className="mt-8">{children}</div>
    </div>
  );
}

function OfflineNote() {
  return (
    <p role="status" className="mt-6 rounded-lg bg-mark/50 px-4 py-3">
      Can’t reach Kru right now. Trying again every few seconds.
    </p>
  );
}

function LibraryLink() {
  return (
    <p className="mt-6">
      <Link href="/library" className="font-semibold text-pen underline underline-offset-2">
        Back to my library
      </Link>
    </p>
  );
}
