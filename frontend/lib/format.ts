// Small helpers that turn numbers and dates into friendly text.
import type { JobStatus } from "./api";

/** 95 -> "about 2 minutes", 30 -> "less than a minute". */
export function formatTimeLeft(seconds: number): string {
  if (seconds < 60) return "less than a minute";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `about ${minutes} minute${minutes === 1 ? "" : "s"}`;
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return `about ${hours} h ${rest} min`;
}

/** 1536 -> "2 KB", 5_400_000 -> "5.1 MB" */
export function formatFileSize(bytes: number): string {
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/** "12 Oct 2026" */
export function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString("en-GB", {
    day: "numeric",
    month: "short",
    year: "numeric",
  });
}

export const STATUS_LABELS: Record<JobStatus, string> = {
  queued: "Waiting",
  extracting: "Reading",
  analyzing: "Reading",
  summarizing: "Summarizing",
  merging: "Summarizing",
  finalizing: "Almost done",
  done: "Ready",
  failed: "Stopped",
  too_long: "Too long",
  scanned_not_supported: "Scanned PDF",
};

/** Statuses where the worker is still (or not yet) working on the book. */
export function isInProgress(status: JobStatus): boolean {
  return ["queued", "extracting", "analyzing", "summarizing", "merging", "finalizing"].includes(
    status,
  );
}

/** How full the progress bar is, from 0 to 100. Chunk notes are most of the work. */
export function progressPercent(status: JobStatus, current: number, total: number): number {
  switch (status) {
    case "queued":
      return 0;
    case "extracting":
      return 3;
    case "analyzing":
      return 7;
    case "summarizing":
      return total > 0 ? 10 + Math.round((current / total) * 75) : 10;
    case "merging":
      return 88;
    case "finalizing":
      return 93;
    case "done":
      return 100;
    default:
      return 0;
  }
}

/** "The full story" -> "the-full-story" (used to link the contents list to headings). */
export function slugify(text: string): string {
  return text
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
}

export type Heading = { text: string; id: string };

/** The "## " headings of a Markdown text, skipping anything inside ``` code blocks. */
export function extractHeadings(markdown: string): Heading[] {
  const headings: Heading[] = [];
  let inCode = false;
  for (const line of markdown.split("\n")) {
    if (line.trimStart().startsWith("```")) {
      inCode = !inCode;
      continue;
    }
    const match = !inCode && line.match(/^##\s+(.+?)\s*#*$/);
    if (match) {
      const text = match[1].replace(/[*_`]/g, "");
      headings.push({ text, id: slugify(text) });
    }
  }
  return headings;
}
