import { Component, type ReactNode } from "react";

/** Keeps a rendering bug from blanking the page during an emergency. */
export default class ErrorBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state = { error: null as Error | null };
  static getDerivedStateFromError(error: Error) {
    return { error };
  }
  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div className="flex h-[100dvh] items-center justify-center p-6 text-center">
        <div className="max-w-md">
          <h1 className="text-xl font-bold">Something went wrong on this page</h1>
          <p className="mt-2 text-sm text-ink-2">{this.state.error.message}</p>
          <button className="mt-4 rounded-xl bg-slate-900 px-4 py-2 font-semibold text-white" onClick={() => location.reload()}>Reload</button>
          <p className="mt-4 text-sm text-ink-3">If you are in danger, call 911 and follow local officials and weather.gov.</p>
        </div>
      </div>
    );
  }
}
