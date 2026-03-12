interface MetricTileProps {
  label: string;
  value: string | number;
  color?: string;
  sub?: string;
}

export function MetricTile({ label, value, color, sub }: MetricTileProps) {
  return (
    <div className="bg-zinc-800/50 p-3 rounded border border-zinc-700/50">
      <div className="text-[10px] uppercase tracking-wider text-zinc-500 mb-1">
        {label}
      </div>
      <div className={`text-sm font-mono font-semibold ${color || "text-zinc-200"}`}>
        {value}
      </div>
      {sub && (
        <div className="text-[10px] text-zinc-500 mt-0.5">{sub}</div>
      )}
    </div>
  );
}
