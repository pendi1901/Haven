import { Link } from "react-router-dom";
import { useStore } from "../lib/store";

/** Header control for the optional account. Renders nothing when accounts aren't configured. */
export default function AccountMenu() {
  const { account } = useStore();
  if (!account.configured) return null;

  if (!account.user) {
    return (
      <button onClick={account.signIn} className="shrink-0 rounded-full border border-line px-3 py-1 text-xs font-semibold text-ink-2 hover:bg-slate-50">
        Sign in
      </button>
    );
  }

  const label = account.user.name ?? account.user.email ?? "Your account";
  return (
    <details className="relative shrink-0">
      <summary className="flex h-8 w-8 cursor-pointer list-none items-center justify-center rounded-full bg-slate-900 text-xs font-bold uppercase text-white"
        aria-label={`Account: ${label}`} title={label}>
        {label.slice(0, 1)}
      </summary>
      <div className="absolute right-0 z-50 mt-2 w-72 rounded-xl border border-line bg-white p-3 text-sm shadow-lg">
        <p className="font-semibold text-ink">{account.user.name ?? "Signed in"}</p>
        {account.user.email && <p className="truncate text-ink-3">{account.user.email}</p>}
        <p className="mt-2 text-ink-2">
          {account.syncing ? "Saving your profile…" : "Your profile is saved to your account and follows you to other devices."}
        </p>
        <label className="mt-3 flex cursor-pointer items-start gap-2">
          <input type="checkbox" className="mt-0.5" checked={account.includeSensitive}
            onChange={(e) => account.setIncludeSensitive(e.target.checked)} />
          <span className="text-ink-2">
            Also sync who lives with you and health answers.
            <span className="block text-xs text-ink-3">Off by default. When off, those answers stay on this device.</span>
          </span>
        </label>
        {account.error && <p className="mt-2 rounded-lg bg-red-50 px-2 py-1.5 text-xs text-red-800">{account.error}</p>}
        <Link to="/welcome?edit=1" className="mt-3 block w-full rounded-lg bg-slate-900 py-1.5 text-center font-semibold text-white hover:bg-slate-800">
          Edit your answers
        </Link>
        <button onClick={account.signOut} className="mt-3 w-full rounded-lg border border-line py-1.5 font-semibold text-ink-2 hover:bg-slate-50">
          Sign out
        </button>
        <p className="mt-2 text-xs text-ink-3">
          Signing out removes your profile from this device. It stays in your account for next time.
          {!account.includeSensitive && " Household and health answers aren't synced, so they'll be erased."}
        </p>
      </div>
    </details>
  );
}
