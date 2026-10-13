"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { useAuth } from "@/components/auth";
import { getSupabase } from "@/lib/supabase";

export default function LoginPage() {
  const router = useRouter();
  const { session, loading } = useAuth();
  const [mode, setMode] = useState<"login" | "signup">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  // Already logged in (or just logged in): go to the library.
  useEffect(() => {
    if (!loading && session) router.replace("/library");
  }, [loading, session, router]);

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    setNotice(null);
    const supabase = getSupabase();
    const { data, error: authError } =
      mode === "login"
        ? await supabase.auth.signInWithPassword({ email, password })
        : await supabase.auth.signUp({ email, password });
    setBusy(false);

    if (authError) {
      setError(
        authError.message === "Invalid login credentials"
          ? "That email and password don’t match. Check them, or create an account."
          : authError.message,
      );
    } else if (!data.session) {
      // Sign up worked, but this Supabase project wants the email confirmed first.
      setNotice("Check your email and click the link to finish creating your account.");
    }
    // With a session, the effect above sends the user to the library.
  };

  const isLogin = mode === "login";

  return (
    <div className="mx-auto max-w-md px-5 py-14">
      <h1 className="text-4xl font-extrabold tracking-tight">
        {isLogin ? "Log in" : "Create your account"}
      </h1>
      <p className="mt-3 font-serif text-slate">
        {isLogin
          ? "Your library of summaries is waiting."
          : "Free. You only need an email and a password."}
      </p>

      <form onSubmit={handleSubmit} className="mt-8 space-y-5">
        <label className="block">
          <span className="font-medium">Email</span>
          <input
            type="email"
            required
            autoComplete="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className="mt-1.5 w-full rounded-lg border border-line bg-sheet px-3.5 py-3 text-base"
          />
        </label>
        <label className="block">
          <span className="font-medium">Password</span>
          <input
            type="password"
            required
            minLength={6}
            autoComplete={isLogin ? "current-password" : "new-password"}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="mt-1.5 w-full rounded-lg border border-line bg-sheet px-3.5 py-3 text-base"
          />
          {!isLogin && (
            <span className="mt-1.5 block text-sm text-slate">At least 6 characters.</span>
          )}
        </label>

        {error && (
          <p role="alert" className="rounded-lg bg-danger-soft px-4 py-3 text-danger">
            {error}
          </p>
        )}
        {notice && (
          <p role="status" className="rounded-lg bg-pen-soft px-4 py-3">
            {notice}
          </p>
        )}

        <button
          type="submit"
          disabled={busy}
          className="w-full cursor-pointer rounded-lg bg-pen px-5 py-3.5 text-lg font-semibold text-white hover:bg-pen-dark disabled:cursor-wait disabled:opacity-60"
        >
          {busy ? "One moment…" : isLogin ? "Log in" : "Create account"}
        </button>
      </form>

      <p className="mt-6 text-slate">
        {isLogin ? "New to Kru?" : "Already have an account?"}{" "}
        <button
          onClick={() => {
            setMode(isLogin ? "signup" : "login");
            setError(null);
            setNotice(null);
          }}
          className="cursor-pointer font-semibold text-pen underline underline-offset-2"
        >
          {isLogin ? "Create an account" : "Log in"}
        </button>
      </p>
    </div>
  );
}
