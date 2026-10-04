// Optional accounts (Supabase Auth + Postgres). Both values are public by design: the
// publishable key only reaches rows that row level security allows.
import { createClient, type SupabaseClient } from "@supabase/supabase-js";

const url = import.meta.env.VITE_SUPABASE_URL as string | undefined;
const key = import.meta.env.VITE_SUPABASE_PUBLISHABLE_KEY as string | undefined;

/** Null when accounts aren't configured: Haven then keeps the profile on this device only. */
export const supabase: SupabaseClient | null = url && key ? createClient(url, key) : null;
