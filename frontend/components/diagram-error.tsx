// Shown in place of a diagram or chart the AI wrote incorrectly. The rest
// of the summary stays readable; the source is there for the curious.
export function DiagramError({ source }: { source: string }) {
  return (
    <div className="mb-5 rounded-lg border border-line bg-sheet px-4 py-3 font-sans text-[0.9375rem]">
      <p className="text-slate">Diagram could not be displayed</p>
      <details className="mt-1">
        <summary className="cursor-pointer text-sm text-pen">Show what the AI wrote</summary>
        <pre className="mt-2 !mb-0 !bg-paper !text-ink">{source}</pre>
      </details>
    </div>
  );
}
