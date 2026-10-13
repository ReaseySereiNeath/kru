"use client";

import Link from "next/link";
import { useAuth } from "./auth";

export function Header() {
  const { session, loading, signOut } = useAuth();

  const handleSignOut = async () => {
    await signOut();
    // A full page load to the home page. (A normal in-app move would lose a
    // race with the page being left, which sends logged-out visitors to /login.)
    // eslint-disable-next-line @next/next/no-location-assign-relative-destination
    window.location.assign("/");
  };

  return (
    <header className="sticky top-0 z-20 border-b border-line bg-paper/90 backdrop-blur">
      <div className="mx-auto flex h-16 max-w-5xl items-center justify-between px-5">
        <Link href="/" className="flex items-baseline gap-2.5 rounded">
          <span className="highlight-still text-2xl font-extrabold tracking-tight">Kru</span>
          <span className="hidden text-sm text-slate sm:inline">Your book teacher</span>
        </Link>

        {/* Empty while we don't know yet, so the links don't flicker. */}
        {!loading && (
          <nav className="flex items-center gap-1 text-[0.9375rem] font-medium sm:gap-2">
            {session ? (
              <>
                <Link href="/library" className="rounded px-3 py-2 hover:bg-pen-soft">
                  My library
                </Link>
                <Link href="/upload" className="rounded px-3 py-2 hover:bg-pen-soft">
                  Upload
                </Link>
                <button
                  onClick={handleSignOut}
                  className="cursor-pointer rounded px-3 py-2 text-slate hover:bg-pen-soft"
                >
                  Log out
                </button>
              </>
            ) : (
              <Link
                href="/login"
                className="rounded-lg bg-pen px-4 py-2 text-white hover:bg-pen-dark"
              >
                Log in
              </Link>
            )}
          </nav>
        )}
      </div>
    </header>
  );
}
