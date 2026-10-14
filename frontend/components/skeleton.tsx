// Grey placeholder bars shown while something loads, in the shape of what
// is coming. Calmer than a spinner, and the page doesn't jump when it arrives.
export function Bar({ className = "" }: { className?: string }) {
  return <div className={`animate-pulse rounded bg-line ${className}`} />;
}

export function LibrarySkeleton() {
  return (
    <div role="status" aria-label="Loading your books" className="mt-8 border-y border-line">
      {[0, 1, 2].map((row) => (
        <div key={row} className="border-b border-line py-5 last:border-b-0">
          <Bar className="h-6 w-2/3 max-w-sm" />
          <Bar className="mt-2.5 h-4 w-32" />
          <Bar className="mt-3.5 h-5 w-52" />
        </div>
      ))}
    </div>
  );
}

export function SummarySkeleton() {
  return (
    <div role="status" aria-label="Loading the summary" className="mx-auto max-w-5xl px-5 py-10">
      <Bar className="h-11 w-3/4 max-w-xl" />
      <Bar className="mt-4 h-5 w-48" />
      <div className="mt-12 max-w-[68ch] space-y-3">
        <Bar className="h-7 w-56" />
        <Bar className="h-4 w-full" />
        <Bar className="h-4 w-full" />
        <Bar className="h-4 w-4/5" />
        <Bar className="!mt-10 h-7 w-44" />
        <Bar className="h-4 w-full" />
        <Bar className="h-4 w-11/12" />
        <Bar className="h-4 w-2/3" />
      </div>
    </div>
  );
}

export function ProgressSkeleton() {
  return (
    <div role="status" aria-label="Loading" className="mx-auto max-w-2xl px-5 py-12">
      <Bar className="h-10 w-3/4" />
      <Bar className="mt-4 h-5 w-40" />
      <Bar className="mt-10 h-6 w-64" />
      <Bar className="mt-4 h-4 w-full rounded-full" />
    </div>
  );
}
