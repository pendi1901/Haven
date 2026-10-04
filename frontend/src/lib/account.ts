// Optional account: Google sign-in through Supabase Auth, and syncing the onboarding
// profile to public.profiles. Without Supabase configured, or signed out, Haven works
// exactly as before with the profile on this device only.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { fromRemote, newerSide, toRemote, type RemoteProfile } from "./profileSync";
import { emptyProfile, storage } from "./storage";
import { supabase } from "./supabase";
import type { Profile } from "./types";

export interface Account {
  configured: boolean;
  user: { id: string; email: string | null; name: string | null } | null;
  /** True once the first sync for the signed-in user has finished. */
  synced: boolean;
  /** True when the account already held a saved profile. */
  restored: boolean;
  includeSensitive: boolean;
  syncing: boolean;
  error: string | null;
  signIn: () => void;
  signOut: () => void;
  setIncludeSensitive: (v: boolean) => void;
}

export function useAccount(profile: Profile, applyProfile: (p: Profile, updatedAt: string) => void) {
  const [user, setUser] = useState<Account["user"]>(null);
  const [synced, setSynced] = useState(false);
  const [restored, setRestored] = useState(false);
  const [includeSensitive, setIncludeSensitiveRaw] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const profileRef = useRef(profile);
  profileRef.current = profile;

  // Fires once with the stored session (INITIAL_SESSION), then on every sign-in/out.
  useEffect(() => {
    if (!supabase) return;
    const { data } = supabase.auth.onAuthStateChange((_event, session) => {
      const u = session?.user;
      setUser(u ? {
        id: u.id,
        email: u.email ?? null,
        name: (u.user_metadata?.full_name as string | undefined) ?? (u.user_metadata?.name as string | undefined) ?? null,
      } : null);
    });
    return () => data.subscription.unsubscribe();
  }, []);

  const upload = useCallback(async (userId: string, p: Profile, include: boolean) => {
    if (!supabase) return;
    setSyncing(true);
    const { error: e } = await supabase.from("profiles").upsert({
      user_id: userId,
      profile: toRemote(p, include),
      include_sensitive: include,
      updated_at: new Date().toISOString(),
    });
    setSyncing(false);
    setError(e ? `Couldn't save your profile to your account: ${e.message}` : null);
  }, []);

  // First sync after sign-in: the more recent of this device and the account wins.
  const userId = user?.id ?? null;
  useEffect(() => {
    setSynced(false);
    setRestored(false);
    if (!supabase || !userId) return;
    let live = true;
    setSyncing(true);
    supabase.from("profiles").select("profile, include_sensitive, updated_at").eq("user_id", userId).maybeSingle()
      .then(async ({ data, error: e }) => {
        if (!live) return;
        setSyncing(false);
        if (e) {
          setError(`Couldn't load your saved profile: ${e.message}`);
          setSynced(true);
          return;
        }
        const row = data as RemoteProfile | null;
        const include = row?.include_sensitive ?? false;
        setIncludeSensitiveRaw(include);
        const side = newerSide(storage.profileUpdatedAt(), row);
        if (side === "remote" && row) applyProfile(fromRemote(row, storage.profile(), emptyProfile()), row.updated_at);
        if (side === "local") await upload(userId, storage.profile(), include);
        if (!live) return;
        setRestored(!!row);
        setSynced(true);
      });
    return () => { live = false; };
  }, [userId, applyProfile, upload]);

  /** Called whenever the profile changes on this device. */
  const pushProfile = useCallback((p: Profile) => {
    if (userId) void upload(userId, p, includeSensitive);
  }, [userId, includeSensitive, upload]);

  const setIncludeSensitive = useCallback((v: boolean) => {
    setIncludeSensitiveRaw(v);
    // Turning it off rewrites the row without those answers, removing them from the server.
    if (userId) void upload(userId, profileRef.current, v);
  }, [userId, upload]);

  const signIn = useCallback(() => {
    if (!supabase) return;
    setError(null);
    // Land on /welcome: it never redirects before the session is read from the URL.
    // select_account: always show Google's account picker. Otherwise a browser already
    // signed in to Google skips it, and on a shared computer the next person would land
    // in the previous user's Haven account.
    void supabase.auth.signInWithOAuth({
      provider: "google",
      options: { redirectTo: `${window.location.origin}/welcome`, queryParams: { prompt: "select_account" } },
    })
      .then(({ error: e }) => e && setError(`Sign-in failed: ${e.message}`));
  }, []);

  const signOut = useCallback(() => {
    if (!supabase) return;
    // The account keeps the profile, so clear it from this device (shared computers)
    // and start over at the welcome screen. A full reload resets every bit of state.
    void supabase.auth.signOut({ scope: "local" }).finally(() => {
      storage.clearPersonal();
      window.location.assign("/welcome");
    });
  }, []);

  const account = useMemo<Account>(() => ({
    configured: !!supabase, user, synced, restored, includeSensitive, syncing, error, signIn, signOut, setIncludeSensitive,
  }), [user, synced, restored, includeSensitive, syncing, error, signIn, signOut, setIncludeSensitive]);
  return { account, pushProfile };
}
