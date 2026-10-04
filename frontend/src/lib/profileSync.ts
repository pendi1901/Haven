// Rules for syncing the onboarding profile to an account. Pure functions so they can
// be tested without Supabase; the network side lives in account.ts.
import type { Profile } from "./types";

/** A row of public.profiles (supabase/migrations). */
export interface RemoteProfile {
  profile: Partial<Profile>;
  include_sensitive: boolean;
  updated_at: string;
}

/**
 * What gets uploaded. Household members (including limited mobility) and health
 * answers stay on the device unless the user opts in to syncing them.
 */
export function toRemote(p: Profile, includeSensitive: boolean): Partial<Profile> {
  if (includeSensitive) return p;
  const { household: _household, sensitive_health: _health, ...rest } = p;
  return rest;
}

/**
 * The profile to use after pulling the account copy. When the account doesn't hold
 * household and health answers, this device's own answers are kept.
 */
export function fromRemote(remote: RemoteProfile, local: Profile, empty: Profile): Profile {
  const merged: Profile = { ...empty, ...remote.profile };
  if (!remote.include_sensitive) {
    merged.household = local.household;
    merged.sensitive_health = local.sensitive_health;
  }
  return merged;
}

/**
 * On sign-in, the more recent edit wins. A device that never saved a profile defers
 * to the account; an account with no profile yet takes this device's.
 */
export function newerSide(localUpdatedAt: string | null, remote: RemoteProfile | null): "local" | "remote" | "none" {
  if (!remote) return localUpdatedAt ? "local" : "none";
  if (!localUpdatedAt) return "remote";
  return Date.parse(localUpdatedAt) > Date.parse(remote.updated_at) ? "local" : "remote";
}
