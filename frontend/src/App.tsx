import { useState } from "react";
import { TopBar, IndexStrip, type TabKey } from "./components/TopBar";
import { WorkspacePage } from "./pages/WorkspacePage";
import { ScreenerPage } from "./pages/ScreenerPage";
import { BacktestPage } from "./pages/BacktestPage";
import { MonitorPage } from "./pages/MonitorPage";
import type { ScreenerItem } from "./types";

export default function App() {
  const [tab, setTab] = useState<TabKey>("workspace");
  const [code, setCode] = useState("600519");
  const [breakoutResults, setBreakoutResults] = useState<ScreenerItem[]>([]);
  const [bottomResults, setBottomResults] = useState<ScreenerItem[]>([]);

  const highCount =
    breakoutResults.filter((i) => i.score >= 80).length +
    bottomResults.filter((i) => i.score >= 80).length;

  const goWorkspace = (c: string) => {
    setCode(c);
    setTab("workspace");
  };

  return (
    <div className="min-h-screen flex flex-col">
      <header className="grad-head border-b border-ink-700 sticky top-0 z-30">
        <TopBar tab={tab} onTabChange={setTab} />
        <IndexStrip />
      </header>

      {tab === "workspace" && (
        <WorkspacePage
          code={code}
          onSelect={setCode}
          onScanResults={(r) => {
            setBreakoutResults(r.breakout);
            setBottomResults(r.bottom);
          }}
          scanCounts={{
            breakout: breakoutResults.length,
            bottom: bottomResults.length,
            high: highCount,
          }}
          breakoutResults={breakoutResults}
          bottomResults={bottomResults}
        />
      )}

      {tab === "screener" && <ScreenerPage onPickStock={goWorkspace} />}
      {tab === "backtest" && <BacktestPage defaultCode={code} />}
      {tab === "monitor" && <MonitorPage onPickStock={goWorkspace} />}

      <footer className="h-8 border-t border-ink-800 grad-head flex items-center px-4 text-[11px] text-ink-500 gap-4">
        <span className="flex items-center gap-1.5">
          <span className="dot bg-cn-dn" /> akshare 在线
        </span>
        <span className="flex items-center gap-1.5">
          <span className="dot bg-cn-dn" /> 算法引擎 v0.2.0
        </span>
        <span>已扫描 {breakoutResults.length + bottomResults.length} 信号</span>
        <span className="flex-1" />
        <span>
          <span className="kbd">F</span> 重新画线
        </span>
        <span>
          <span className="kbd">S</span> 切筛选
        </span>
        <span className="text-ink-600">© PivotLab · 开源数据 · 仅供研究</span>
      </footer>
    </div>
  );
}
