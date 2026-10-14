"use client";

import { useRouter } from "next/navigation";
import { useRef, useState } from "react";
import { RequireAuth } from "@/components/auth";
import { ApiError, uploadBook } from "@/lib/api";
import { formatFileSize } from "@/lib/format";

const MAX_MB = Number(process.env.NEXT_PUBLIC_MAX_FILE_MB ?? 50);
const ALLOWED = ["pdf", "epub", "txt"];

/** Returns a message if the file can't be used, or null if it's fine. */
function checkFile(file: File): string | null {
  const extension = file.name.split(".").pop()?.toLowerCase() ?? "";
  if (!file.name.includes(".") || !ALLOWED.includes(extension)) {
    return "Kru reads PDF, EPUB, and TXT files. Please choose one of those.";
  }
  if (file.size > MAX_MB * 1024 * 1024) {
    const size = Math.round(file.size / (1024 * 1024));
    return `This file is ${size} MB. The limit is ${MAX_MB} MB.`;
  }
  if (file.size === 0) return "This file is empty.";
  return null;
}

function UploadForm() {
  const router = useRouter();
  const input = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [dragging, setDragging] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const choose = (chosen: File | undefined) => {
    if (!chosen) return;
    const problem = checkFile(chosen);
    setError(problem);
    setFile(problem ? null : chosen);
  };

  const handleUpload = async () => {
    if (!file) return;
    setUploading(true);
    setError(null);
    try {
      const book = await uploadBook(file);
      router.push(`/books/${book.id}`);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "The upload didn’t work. Please try again.");
      setUploading(false);
    }
  };

  return (
    <div className="mx-auto max-w-2xl px-5 py-12">
      <h1 className="text-4xl font-extrabold tracking-tight">Upload a book</h1>
      <p className="mt-3 font-serif text-lg text-slate">
        Books up to about 500 pages. PDF, EPUB, or TXT, at most {MAX_MB} MB.
      </p>

      <div
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          choose(e.dataTransfer.files[0]);
        }}
        className={`mt-8 rounded-xl border-2 border-dashed px-6 py-12 text-center transition-colors ${
          dragging ? "border-pen bg-pen-soft" : "border-line bg-sheet"
        }`}
      >
        <input
          ref={input}
          type="file"
          accept=".pdf,.epub,.txt"
          className="sr-only"
          onChange={(e) => choose(e.target.files?.[0])}
        />
        {file ? (
          <>
            <p className="text-xl font-bold break-words">{file.name}</p>
            <p className="mt-1 text-slate">{formatFileSize(file.size)}</p>
            <button
              onClick={() => input.current?.click()}
              disabled={uploading}
              className="mt-4 cursor-pointer font-semibold text-pen underline underline-offset-2"
            >
              Choose a different file
            </button>
          </>
        ) : (
          <>
            <p className="text-xl font-bold">Drop your book here</p>
            <p className="mt-1 text-slate">or</p>
            <button
              onClick={() => input.current?.click()}
              className="mt-3 cursor-pointer rounded-lg border border-pen px-5 py-2.5 font-semibold text-pen hover:bg-pen-soft"
            >
              Choose a file
            </button>
          </>
        )}
      </div>

      {error && (
        <p role="alert" className="mt-5 rounded-lg bg-danger-soft px-4 py-3 text-danger">
          {error}
        </p>
      )}

      <button
        onClick={handleUpload}
        disabled={!file || uploading}
        className="mt-6 w-full cursor-pointer rounded-lg bg-pen px-5 py-3.5 text-lg font-semibold text-white hover:bg-pen-dark disabled:cursor-not-allowed disabled:opacity-50"
      >
        {uploading ? "Uploading…" : "Summarize this book"}
      </button>

      <ul className="mt-8 space-y-2 font-serif text-slate">
        <li>
          A long book can take 20 to 40 minutes. You don’t have to keep the page open: the summary
          will be in your library.
        </li>
        <li>Books are summarized one at a time, so yours may wait in line first.</li>
        <li>Scanned PDFs (photos of pages) can’t be read yet.</li>
      </ul>
    </div>
  );
}

export default function UploadPage() {
  return (
    <RequireAuth>
      <UploadForm />
    </RequireAuth>
  );
}
