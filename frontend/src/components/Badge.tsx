interface BadgeProps {
  status: "WS LIVE" | "REST" | "LIVE" | "STALE" | "CONNECTING" | "DISCONNECTED";
  label?: string;
}

const STYLES: Record<string, string> = {
  "WS LIVE": "bg-emerald-400/20 text-emerald-400 border-emerald-400/30",
  LIVE: "bg-emerald-400/20 text-emerald-400 border-emerald-400/30",
  REST: "bg-yellow-400/20 text-yellow-400 border-yellow-400/30",
  STALE: "bg-red-400/20 text-red-400 border-red-400/30",
  CONNECTING: "bg-amber-400/20 text-amber-400 border-amber-400/30",
  DISCONNECTED: "bg-zinc-500/20 text-zinc-500 border-zinc-500/30",
};

export function Badge({ status, label }: BadgeProps) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-[11px] font-medium border ${STYLES[status] || STYLES.DISCONNECTED}`}
    >
      <span
        className={`w-1.5 h-1.5 rounded-full ${
          status === "WS LIVE" || status === "LIVE" ? "bg-emerald-400 animate-pulse" :
          status === "REST" ? "bg-yellow-400 animate-pulse" :
          status === "STALE" ? "bg-red-400" :
          status === "CONNECTING" ? "bg-amber-400 animate-pulse" :
          "bg-zinc-500"
        }`}
      />
      {label || status}
    </span>
  );
}
