import type { Level } from "../types";

type Props = {
  levels: Level[];
  price: number;
};

export function LevelsPanel({ levels, price }: Props) {
  const resistances = levels.filter((l) => l.kind === "resistance");
  const supports = levels.filter((l) => l.kind === "support");

  const Row = ({ l }: { l: Level }) => {
    const color = l.kind === "resistance" ? "text-gold" : "text-sky2";
    const stars = "★".repeat(l.strength);
    const distColor = l.distance_pct >= 0 ? "text-cn-up" : "text-cn-dn";
    return (
      <div className="flex items-center gap-2 px-2 py-1.5 rounded hover:bg-ink-850">
        <span className={"num text-[11px] w-8 " + color}>{l.label}</span>
        <span className="num text-[12px] text-ink-100 w-16">{l.price.toFixed(2)}</span>
        <span className={"text-[10px] w-16 " + color}>{stars}</span>
        <span className="text-[10px] text-ink-500 flex-1 truncate">{l.note}</span>
        <span className={"num text-[11px] " + distColor}>
          {l.distance_pct >= 0 ? "+" : ""}
          {l.distance_pct.toFixed(2)}%
        </span>
      </div>
    );
  };

  return (
    <div className="p-4 border-b border-ink-800">
      <div className="flex items-center justify-between mb-3">
        <span className="tag text-ink-500">支撑压力位 · 日线</span>
        <span className="text-[11px] text-ink-500 num">现价 {price.toFixed(2)}</span>
      </div>
      <div className="space-y-1.5">
        {resistances.map((l, i) => (
          <Row key={"r" + i} l={l} />
        ))}
        {resistances.length > 0 && supports.length > 0 && <div className="divider my-2" />}
        {supports.map((l, i) => (
          <Row key={"s" + i} l={l} />
        ))}
        {levels.length === 0 && (
          <div className="text-[12px] text-ink-500 text-center py-4">暂无识别到的支撑压力位</div>
        )}
      </div>
    </div>
  );
}
