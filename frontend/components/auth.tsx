"use client";

// Login state for the whole site.
// <AuthProvider> (in app/layout.tsx) asks Supabase once who is logged in and
// listens for changes. Any component can then call useAuth().
// <RequireAuth> wraps pages that need a login and sends visitors to /login.
import type { Session } from "@supabase/supabase-js";
import { useRouter } from "next/navigation";
import { createContext, useContext, useEffect, useState } from "react";
import { getSupabase } from "@/lib/supabase";

type AuthState = {
  session: Session | null;
  loading: boolean; // true until we know whether someone is logged in
  signOut: () => Promise<void>;
};

const AuthContext = createContext<AuthState>({
  session: null,
  loading: true,
  signOut: async () => {},
});

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [session, setSession] = useState<Session | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const supabase = getSupabase();
    supabase.auth.getSession().then(({ data }) => {
      setSession(data.session);
      setLoading(false);
    });
    // Fires on log in, log out, and when the token is refreshed.
    const { data } = supabase.auth.onAuthStateChange((_event, newSession) => {
      setSession(newSession);
      setLoading(false);
    });
    return () => data.subscription.unsubscribe();
  }, []);

  const signOut = async () => {
    await getSupabase().auth.signOut();
  };

  return <AuthContext value={{ session, loading, signOut }}>{children}</AuthContext>;
}

export function useAuth(): AuthState {
  return useContext(AuthContext);
}

export function RequireAuth({ children }: { children: React.ReactNode }) {
  const { session, loading } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (!loading && !session) router.replace("/login");
  }, [loading, session, router]);

  if (loading || !session) {
    return <p className="py-16 text-center text-slate">Checking your login…</p>;
  }
  return <>{children}</>;
}
