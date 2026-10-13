import type { JobStatus } from "@/lib/api";
import { STATUS_LABELS, isInProgress } from "@/lib/format";

export function StatusBadge({ status }: { status: JobStatus }) {
  const style =
    status === "done"
      ? "bg-pen-soft text-pen-dark"
      : isInProgress(status)
        ? "bg-mark/60 text-ink"
        : "bg-danger-soft text-danger";
  return (
    <span className={`inline-block rounded-full px-2.5 py-0.5 text-sm font-medium ${style}`}>
      {STATUS_LABELS[status]}
    </span>
  );
}
