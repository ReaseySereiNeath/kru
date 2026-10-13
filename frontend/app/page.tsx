import Link from "next/link";

// A highlighted phrase in the demo. `delay` staggers the strokes so they are
// drawn one after another, like a reader going down the page.
function Mark({ delay, children }: { delay: number; children: React.ReactNode }) {
  return (
    <span className="highlight" style={{ "--delay": `${delay}s` } as React.CSSProperties}>
      {children}
    </span>
  );
}

// The opening of "Alice's Adventures in Wonderland" (public domain), with
// the phrases a careful reader would highlight.
function BookPageDemo() {
  return (
    <div className="relative">
      <figure className="rounded-sm border border-line bg-sheet px-6 py-7 shadow-[0_18px_40px_-24px_rgba(20,33,61,0.45)] sm:px-9 sm:py-9">
        <figcaption className="mb-4 font-sans text-sm text-slate">
          Alice’s Adventures in Wonderland, page 1
        </figcaption>
        <p className="font-serif text-[1.02rem] leading-[1.85] text-faded sm:text-[1.08rem]">
          <Mark delay={0.5}>Alice was beginning to get very tired</Mark> of sitting by her sister
          on the bank, and of <Mark delay={1.1}>having nothing to do</Mark>: once or twice she had
          peeped into the book her sister was reading, but it had no pictures or conversations in
          it, “and <Mark delay={1.7}>what is the use of a book</Mark>,” thought Alice “
          <Mark delay={2.2}>without pictures or conversations?</Mark>” So she was considering in
          her own mind (as well as she could, for the hot day made her feel very sleepy and
          stupid), whether the pleasure of making a daisy-chain would be worth the trouble of
          getting up and picking the daisies, when suddenly{" "}
          <Mark delay={2.9}>a White Rabbit with pink eyes ran close by her.</Mark>
        </p>
      </figure>

      <div
        className="note-in relative mx-4 -mt-4 rounded-lg bg-ink px-5 py-4 text-paper sm:ml-auto sm:mr-6 sm:max-w-sm"
        style={{ "--delay": "3.6s" } as React.CSSProperties}
      >
        <p className="text-sm text-paper/70">What Kru keeps</p>
        <p className="mt-1 font-serif text-[1.02rem] leading-relaxed">
          Alice is bored by a book with no pictures. A White Rabbit runs past, and she follows it.
        </p>
      </div>
    </div>
  );
}

const STEPS = [
  {
    title: "Upload your book",
    text: "A PDF, EPUB, or TXT file, up to about 500 pages.",
  },
  {
    title: "Kru reads every page",
    text: "It takes notes chapter by chapter, so nothing in the middle gets skipped. A long book takes 20 to 40 minutes. You can close the page and come back.",
  },
  {
    title: "Read the summary",
    text: "A short, plain-language summary with diagrams, ready in your library.",
  },
];

const FICTION = [
  "The whole story, including the ending",
  "Who the main characters are and how they change",
  "A map of how the characters are connected",
  "A timeline of the most important events",
  "What the book is really about",
];

const LEARNING = [
  "The core idea in one or two sentences",
  "The key concepts, each with an analogy and an example",
  "A concept map showing how the ideas connect",
  "Things you can apply right away",
  "Which chapters are worth reading in full",
];

function CheckList({ items }: { items: string[] }) {
  return (
    <ul className="mt-4 space-y-2.5">
      {items.map((item) => (
        <li key={item} className="flex gap-3 font-serif leading-snug">
          <span aria-hidden className="mt-[0.45em] h-2 w-4 shrink-0 rounded-sm bg-mark" />
          {item}
        </li>
      ))}
    </ul>
  );
}

export default function LandingPage() {
  return (
    <>
      <section className="mx-auto grid max-w-5xl items-center gap-12 px-5 pt-12 pb-20 md:grid-cols-[1fr_1.05fr] md:pt-20">
        <div>
          <h1 className="text-[2.6rem] leading-[1.05] font-extrabold tracking-tight sm:text-6xl">
            Understand the whole book in ten minutes.
          </h1>
          <p className="mt-6 max-w-md font-serif text-lg leading-relaxed text-slate">
            Upload a novel or a textbook. Kru reads every page and writes you a short summary in
            plain language, with diagrams: the full story, or the core ideas.
          </p>
          <div className="mt-8 flex flex-wrap items-center gap-x-5 gap-y-3">
            <Link
              href="/upload"
              className="rounded-lg bg-pen px-6 py-3.5 text-lg font-semibold text-white hover:bg-pen-dark"
            >
              Upload a book
            </Link>
            <span className="text-sm text-slate">Free. Books up to about 500 pages.</span>
          </div>
        </div>
        <BookPageDemo />
      </section>

      <section className="border-y border-line bg-sheet">
        <div className="mx-auto max-w-5xl px-5 py-16">
          <h2 className="text-3xl font-bold tracking-tight">How it works</h2>
          <ol className="mt-8 grid gap-8 md:grid-cols-3">
            {STEPS.map((step, i) => (
              <li key={step.title}>
                <span className="flex h-9 w-9 items-center justify-center rounded-full bg-mark text-lg font-bold">
                  {i + 1}
                </span>
                <h3 className="mt-4 text-xl font-bold">{step.title}</h3>
                <p className="mt-2 font-serif leading-relaxed text-slate">{step.text}</p>
              </li>
            ))}
          </ol>
        </div>
      </section>

      <section className="mx-auto max-w-5xl px-5 py-16">
        <h2 className="text-3xl font-bold tracking-tight">What you get</h2>
        <p className="mt-3 max-w-2xl font-serif text-lg leading-relaxed text-slate">
          Kru works out what kind of book it is and writes the summary that fits.
        </p>
        <div className="mt-8 grid gap-10 md:grid-cols-2">
          <div>
            <h3 className="text-xl font-bold">For a novel</h3>
            <CheckList items={FICTION} />
          </div>
          <div>
            <h3 className="text-xl font-bold">For a book you learn from</h3>
            <p className="mt-1 text-sm text-slate">Coding, business, science, self-help</p>
            <CheckList items={LEARNING} />
          </div>
        </div>
      </section>

      <section className="border-t border-line">
        <div className="mx-auto max-w-5xl px-5 py-16">
          <h2 className="text-3xl font-bold tracking-tight">Good to know</h2>
          <dl className="mt-8 grid gap-x-10 gap-y-7 font-serif md:grid-cols-2">
            <div>
              <dt className="font-sans font-bold">Your file is not kept</dt>
              <dd className="mt-1 leading-relaxed text-slate">
                Kru deletes the uploaded book when the summary is ready. Only the summary stays in
                your library, and only you can see it.
              </dd>
            </div>
            <div>
              <dt className="font-sans font-bold">It runs on open-source AI</dt>
              <dd className="mt-1 leading-relaxed text-slate">
                No paid AI service is involved. That also means it is slower than a chatbot: books
                are summarized one at a time, so you may wait in line.
              </dd>
            </div>
            <div>
              <dt className="font-sans font-bold">Scanned books don’t work yet</dt>
              <dd className="mt-1 leading-relaxed text-slate">
                If a PDF is made of photos of pages, Kru can’t read the text in it.
              </dd>
            </div>
            <div>
              <dt className="font-sans font-bold">A summary is not the book</dt>
              <dd className="mt-1 leading-relaxed text-slate">
                The AI can get details wrong. Use the summary to understand a book quickly, and
                check the book itself for anything that matters.
              </dd>
            </div>
          </dl>
        </div>
      </section>
    </>
  );
}
