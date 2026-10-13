// The Supabase client for the browser. It handles sign up, log in, and
// keeps the login in the browser's storage so a page reload stays logged in.
//
// It uses the ANON key, which is safe to show to the world: the database's
// Row Level Security rules only let a logged-in user read their own rows.
import { createClient, type SupabaseClient } from "@supabase/supabase-js";

let client: SupabaseClient | null = null;

// Created on first use (not when the file loads), so building the site
// doesn't fail when the environment values are missing.
export function getSupabase(): SupabaseClient {
  if (!client) {
    const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
    const anonKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;
    if (!url || !anonKey) {
      throw new Error(
        "NEXT_PUBLIC_SUPABASE_URL and NEXT_PUBLIC_SUPABASE_ANON_KEY are not set. " +
          "Copy frontend/.env.example to frontend/.env.local and fill them in.",
      );
    }
    client = createClient(url, anonKey);
  }
  return client;
}
