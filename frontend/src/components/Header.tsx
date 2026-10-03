import { Link, NavLink } from "react-router-dom";
import { useStore } from "../lib/store";

export default function Header({ onLocation }: { onLocation?: () => void }) {
  const { meta, location, assess } = useStore();
  const crisis = assess?.crisis;
  return (
    <header className="flex items-center gap-3 border-b border-line bg-white/90 px-4 py-2.5 backdrop-blur">
      <Link to="/" className="flex items-center gap-2" aria-label="Haven home">
        <img src="/icon.svg" alt="" className="h-7 w-7" />
        <span className="text-lg font-bold tracking-tight text-ink">Haven</span>
      </Link>
      {meta && (
        <span className={`rounded-full px-2 py-0.5 text-[11px] font-semibold ${meta.data_mode === "replay" ? "bg-sky-100 text-sky-800" : "bg-emerald-100 text-emerald-800"}`}>
          {meta.data_mode === "replay" ? "Replay" : "Live"}
        </span>
      )}
      <nav className="ml-auto flex items-center gap-1 text-sm">
        <NavLink to="/" end className={({ isActive }) => `rounded-lg px-2.5 py-1.5 ${isActive ? "bg-slate-100 font-semibold text-ink" : "text-ink-2 hover:bg-slate-50"}`}>Today</NavLink>
        <NavLink to="/crisis" className={({ isActive }) => `relative rounded-lg px-2.5 py-1.5 ${isActive ? "bg-slate-100 font-semibold text-ink" : "text-ink-2 hover:bg-slate-50"}`}>
          What to do
          {crisis && <span className="absolute -right-0.5 -top-0.5 h-2.5 w-2.5 rounded-full bg-red-600 ring-2 ring-white" aria-label="Crisis mode" />}
        </NavLink>
        <NavLink to="/responder" className={({ isActive }) => `hidden rounded-lg px-2.5 py-1.5 sm:block ${isActive ? "bg-slate-100 font-semibold text-ink" : "text-ink-2 hover:bg-slate-50"}`}>Responders</NavLink>
      </nav>
      {onLocation && (
        <button onClick={onLocation} className="hidden max-w-[180px] items-center gap-1.5 truncate rounded-full border border-line px-3 py-1 text-xs text-ink-2 hover:bg-slate-50 md:flex">
          <span className="h-2 w-2 rounded-full bg-blue-600" />
          {location ? location.label : "Set location"}
        </button>
      )}
    </header>
  );
}
