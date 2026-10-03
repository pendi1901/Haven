import { Link, NavLink } from "react-router-dom";
import { useStore } from "../lib/store";

export default function Header({ onLocation }: { onLocation?: () => void }) {
  const { meta, location, assess, dataMode, setDataMode } = useStore();
  const crisis = assess?.crisis;
  return (
    <header className="flex items-center gap-3 border-b border-line bg-white/90 px-4 py-2.5 backdrop-blur">
      <Link to="/" className="flex items-center gap-2" aria-label="Haven home">
        <img src="/icon.svg" alt="" className="h-7 w-7" />
        <span className="text-lg font-bold tracking-tight text-ink">Haven</span>
      </Link>
      <div role="group" aria-label="Data mode" className="flex rounded-full bg-slate-100 p-0.5 text-[11px] font-semibold">
        <button onClick={() => setDataMode("live")} aria-pressed={dataMode === "live"}
          className={`flex items-center gap-1 rounded-full px-2.5 py-1 ${dataMode === "live" ? "bg-white text-emerald-800 shadow-sm" : "text-ink-3"}`}>
          <span className={`h-1.5 w-1.5 rounded-full ${dataMode === "live" ? "animate-pulse bg-emerald-500" : "bg-slate-400"}`} />Live
        </button>
        {(meta?.modes.replay ?? true) && (
          <button onClick={() => setDataMode("replay")} aria-pressed={dataMode === "replay"} title="Replay Hurricane Helene in Asheville (Sept 2024)"
            className={`rounded-full px-2.5 py-1 ${dataMode === "replay" ? "bg-white text-sky-800 shadow-sm" : "text-ink-3"}`}>
            Helene replay
          </button>
        )}
      </div>
      <nav className="ml-auto flex items-center gap-1 text-sm">
        <NavLink to="/" end className={({ isActive }) => `rounded-lg px-2.5 py-1.5 ${isActive ? "bg-slate-100 font-semibold text-ink" : "text-ink-2 hover:bg-slate-50"}`}>Today</NavLink>
        <NavLink to="/crisis" className={({ isActive }) => `relative rounded-lg px-2.5 py-1.5 ${isActive ? "bg-slate-100 font-semibold text-ink" : "text-ink-2 hover:bg-slate-50"}`}>
          What to do
          {crisis && <span className="absolute -right-0.5 -top-0.5 h-2.5 w-2.5 rounded-full bg-red-600 ring-2 ring-white" aria-label="Crisis mode" />}
        </NavLink>
        <NavLink to="/responder" className={({ isActive }) => `hidden rounded-lg px-2.5 py-1.5 sm:block ${isActive ? "bg-slate-100 font-semibold text-ink" : "text-ink-2 hover:bg-slate-50"}`}>Responders</NavLink>
      </nav>
      {onLocation && (
        <button onClick={onLocation} className="hidden max-w-[260px] items-center gap-1.5 truncate rounded-full border border-line px-3 py-1 text-xs text-ink-2 hover:bg-slate-50 md:flex">
          <span className="h-2 w-2 rounded-full bg-blue-600" />
          {location
            ? meta && meta.data_mode === "live" && !meta.region.name.startsWith(location.label)
              ? `${location.label} · ${meta.region.name}` : location.label
            : "Set location"}
        </button>
      )}
    </header>
  );
}
