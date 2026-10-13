import { Suspense } from "react";
import { RequireAuth } from "@/components/auth";
import { SummaryView } from "@/components/summary-view";

// The page itself lives in the components/ folder: Tailwind does not pick up
// class names from files inside a folder with [brackets] in its name.
export const metadata = { title: "Summary" };

// See app/books/[id]/page.tsx for why <Suspense> is needed here.
export default function SummaryPage() {
  return (
    <Suspense fallback={<p className="py-16 text-center text-slate">Loading…</p>}>
      <RequireAuth>
        <SummaryView />
      </RequireAuth>
    </Suspense>
  );
}
