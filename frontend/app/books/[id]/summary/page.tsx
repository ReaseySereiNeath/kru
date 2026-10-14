import { Suspense } from "react";
import { RequireAuth } from "@/components/auth";
import { SummarySkeleton } from "@/components/skeleton";
import { SummaryView } from "@/components/summary-view";

// The page itself lives in the components/ folder: Tailwind does not pick up
// class names from files inside a folder with [brackets] in its name.
export const metadata = { title: "Summary" };

// See app/books/[id]/page.tsx for why <Suspense> is needed here.
export default function SummaryPage() {
  return (
    <Suspense fallback={<SummarySkeleton />}>
      <RequireAuth>
        <SummaryView />
      </RequireAuth>
    </Suspense>
  );
}
