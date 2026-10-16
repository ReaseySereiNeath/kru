import type { Metadata } from "next";
import { Bricolage_Grotesque, Literata } from "next/font/google";
import { AuthProvider } from "@/components/auth";
import { Header } from "@/components/header";
import "./globals.css";

// Bricolage Grotesque: headings, buttons, and the interface.
const bricolage = Bricolage_Grotesque({
  variable: "--font-bricolage",
  subsets: ["latin"],
});

// Literata was designed for reading e-books: used for summaries and long text.
const literata = Literata({
  variable: "--font-literata",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: { default: "Kru: your book teacher", template: "%s | Kru" },
  description:
    "Upload a book and get a short, plain-language summary with diagrams: the full story of a novel, or the core ideas of a textbook.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className={`${bricolage.variable} ${literata.variable} h-full antialiased`}>
      <body className="flex min-h-full flex-col">
        <AuthProvider>
          <Header />
          <main className="flex-1">{children}</main>
          <footer className="border-t border-line">
            <p className="mx-auto max-w-5xl px-5 py-6 text-sm text-slate">
              Kru means “teacher” in Khmer. Summaries are written by an AI model and can contain
              mistakes.
            </p>
          </footer>
        </AuthProvider>
      </body>
    </html>
  );
}
