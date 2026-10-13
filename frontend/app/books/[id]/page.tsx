import { Suspense } from "react";
import { RequireAuth } from "@/components/auth";
import { ProgressView } from "@/components/progress-view";

// The page itself lives in the components/ folder: Tailwind does not pick up
// class names from files inside a folder with [brackets] in its name.
export const metadata = { title: "Progress" };

// ProgressView reads the book id from the address. Next.js can't know that
// id when it builds the page, so it needs a <Suspense> around it.
export default function ProgressPage() {
  return (
    <Suspense fallback={<p className="py-16 text-center text-slate">Loading…</p>}>
      <RequireAuth>
        <ProgressView />
      </RequireAuth>
    </Suspense>
  );
}
