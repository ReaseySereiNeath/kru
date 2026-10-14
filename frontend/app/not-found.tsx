import Link from "next/link";

export default function NotFound() {
  return (
    <div className="mx-auto max-w-2xl px-5 py-16">
      <h1 className="text-4xl font-extrabold tracking-tight">This page doesn’t exist</h1>
      <p className="mt-4 font-serif text-lg text-slate">
        The address may be mistyped, or the page was moved.
      </p>
      <p className="mt-6 flex flex-wrap gap-x-6 gap-y-2 font-semibold text-pen">
        <Link href="/" className="underline underline-offset-2">
          Go to the home page
        </Link>
        <Link href="/library" className="underline underline-offset-2">
          Go to my library
        </Link>
      </p>
    </div>
  );
}
