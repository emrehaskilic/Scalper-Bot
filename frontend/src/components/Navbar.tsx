interface NavbarProps {
  page: "dashboard" | "backtest";
  setPage: (p: "dashboard" | "backtest") => void;
}

export function Navbar({ page, setPage }: NavbarProps) {
  const tab = (id: "dashboard" | "backtest", label: string) => (
    <button
      onClick={() => setPage(id)}
      className={`px-3 py-1.5 rounded text-xs font-medium transition-colors ${
        page === id
          ? "bg-zinc-700/60 text-zinc-100"
          : "text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800/50"
      }`}
    >
      {label}
    </button>
  );

  return (
    <nav className="bg-zinc-900/80 border-b border-zinc-800 px-4 md:px-6 py-2">
      <div className="max-w-7xl mx-auto flex items-center gap-4">
        <span className="text-sm font-bold text-zinc-100 mr-2">Scalper Bot</span>
        <div className="flex gap-1">
          {tab("dashboard", "Dashboard")}
          {tab("backtest", "Backtest")}
        </div>
      </div>
    </nav>
  );
}
