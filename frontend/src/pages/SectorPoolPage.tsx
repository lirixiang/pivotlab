// 赛道池 (Sector Pool) — 人工维护的"主线赛道 → 龙头股"映射
//
// 三栏布局：
//   左   ：分组树（按 category 聚合）
//   中   ：当前分组下的赛道列表（CRUD）
//   右   ：当前赛道下的个股表格（CRUD + 批量导入 + tier 切换）
//
// 与交易系统的联动：在 SystemPage 的「选股池」段里可选这些赛道做交集过滤。

import { useCallback, useEffect, useMemo, useState } from "react";
import { api, type SectorPool, type SectorPoolStock } from "../services/api";
import { toast } from "../components/Toast";
import { ConfirmModal, InputModal } from "../components/Modal";

type SortKey = "tier" | "code" | "name" | "industry";
type SortDir = "asc" | "desc";

const TIER_LABEL: Record<number, { txt: string; cls: string }> = {
  1: { txt: "龙一", cls: "bg-gold/20 text-gold border-gold/30" },
  2: { txt: "龙二", cls: "bg-blue-500/20 text-blue-300 border-blue-500/30" },
  3: { txt: "跟风", cls: "bg-ink-700 text-ink-300 border-ink-600" },
};

export function SectorPoolPage() {
  const [pools, setPools] = useState<SectorPool[]>([]);
  const [activeCategory, setActiveCategory] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [stocks, setStocks] = useState<SectorPoolStock[]>([]);
  const [loadingStocks, setLoadingStocks] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  // modals
  const [showNewSector, setShowNewSector] = useState(false);
  const [showEditSector, setShowEditSector] = useState<SectorPool | null>(null);
  const [confirmDeletePool, setConfirmDeletePool] = useState<SectorPool | null>(null);
  const [showAddStock, setShowAddStock] = useState(false);
  const [showBulkAdd, setShowBulkAdd] = useState(false);

  // sort
  const [sortKey, setSortKey] = useState<SortKey>("tier");
  const [sortDir, setSortDir] = useState<SortDir>("asc");

  // ── data load ──
  const reloadPools = useCallback(async () => {
    try {
      const r = await api.sectorPoolList();
      setPools(r.items);
      setErr(null);
      // 自动选中：当前选中失效则切第一个
      if (r.items.length === 0) {
        setSelectedId(null);
      } else if (!r.items.find((p) => p.id === selectedId)) {
        const first = activeCategory
          ? r.items.find((p) => (p.category || "未分组") === activeCategory)
          : r.items[0];
        setSelectedId((first || r.items[0]).id);
      }
    } catch (e: any) {
      setErr(String(e?.message || e));
    }
  }, [selectedId, activeCategory]);

  useEffect(() => {
    reloadPools();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const reloadStocks = useCallback(async () => {
    if (selectedId == null) {
      setStocks([]);
      return;
    }
    setLoadingStocks(true);
    try {
      const r = await api.sectorPoolStocks(selectedId);
      setStocks(r.items);
    } catch (e: any) {
      toast.error("加载个股失败: " + (e?.message || e));
    } finally {
      setLoadingStocks(false);
    }
  }, [selectedId]);

  useEffect(() => {
    reloadStocks();
  }, [reloadStocks]);

  // ── derived: grouped pools by category ──
  const grouped = useMemo(() => {
    const map = new Map<string, SectorPool[]>();
    for (const p of pools) {
      const key = p.category || "未分组";
      if (!map.has(key)) map.set(key, []);
      map.get(key)!.push(p);
    }
    return Array.from(map.entries()).sort((a, b) => a[0].localeCompare(b[0]));
  }, [pools]);

  const filteredPools = useMemo(() => {
    if (activeCategory == null) return pools;
    return pools.filter((p) => (p.category || "未分组") === activeCategory);
  }, [pools, activeCategory]);

  const selectedPool = useMemo(
    () => pools.find((p) => p.id === selectedId) || null,
    [pools, selectedId],
  );

  const sortedStocks = useMemo(() => {
    const arr = [...stocks];
    const dir = sortDir === "asc" ? 1 : -1;
    arr.sort((a, b) => {
      let av: string | number = a[sortKey] as any;
      let bv: string | number = b[sortKey] as any;
      if (typeof av === "string") av = av.toLowerCase();
      if (typeof bv === "string") bv = bv.toLowerCase();
      if (av < bv) return -1 * dir;
      if (av > bv) return 1 * dir;
      return 0;
    });
    return arr;
  }, [stocks, sortKey, sortDir]);

  const toggleSort = (k: SortKey) => {
    if (k === sortKey) setSortDir(sortDir === "asc" ? "desc" : "asc");
    else { setSortKey(k); setSortDir("asc"); }
  };

  // ── actions ──
  const createSector = async (name: string) => {
    setShowNewSector(false);
    try {
      const p = await api.sectorPoolCreate({
        name,
        category: activeCategory && activeCategory !== "未分组" ? activeCategory : "",
      });
      toast.success(`已创建赛道「${p.name}」`);
      await reloadPools();
      setSelectedId(p.id);
    } catch (e: any) {
      toast.error("创建失败: " + (e?.message || e));
    }
  };

  const updateSector = async (patch: { name?: string; category?: string; description?: string }) => {
    if (!showEditSector) return;
    try {
      await api.sectorPoolUpdate(showEditSector.id, patch);
      setShowEditSector(null);
      toast.success("已保存");
      await reloadPools();
    } catch (e: any) {
      toast.error("保存失败: " + (e?.message || e));
    }
  };

  const deletePool = async () => {
    if (!confirmDeletePool) return;
    try {
      await api.sectorPoolDelete(confirmDeletePool.id);
      toast.success("已归档");
      setConfirmDeletePool(null);
      if (selectedId === confirmDeletePool.id) setSelectedId(null);
      await reloadPools();
    } catch (e: any) {
      toast.error("删除失败: " + (e?.message || e));
    }
  };

  const addStock = async (code: string) => {
    if (!selectedId) return;
    setShowAddStock(false);
    try {
      await api.sectorPoolAddStock(selectedId, { code, tier: 2 });
      toast.success(`已加入 ${code}`);
      await reloadStocks();
      await reloadPools(); // 更新计数
    } catch (e: any) {
      toast.error(e?.message || String(e));
    }
  };

  const bulkAdd = async (text: string) => {
    if (!selectedId) return;
    setShowBulkAdd(false);
    const codes = Array.from(text.matchAll(/\d{6}/g)).map((m) => m[0]);
    if (codes.length === 0) {
      toast.error("未识别到任何 6 位股票代码");
      return;
    }
    try {
      const r = await api.sectorPoolBulkAdd(selectedId, codes, 2);
      toast.success(
        `已添加 ${r.added} 只 (跳过已存在 ${r.skipped_existing}, 未识别 ${r.skipped_unknown})`,
      );
      await reloadStocks();
      await reloadPools();
    } catch (e: any) {
      toast.error("批量添加失败: " + (e?.message || e));
    }
  };

  const changeTier = async (code: string, tier: number) => {
    if (!selectedId) return;
    try {
      await api.sectorPoolUpdateStock(selectedId, code, { tier });
      setStocks((prev) => prev.map((s) => (s.code === code ? { ...s, tier } : s)));
    } catch (e: any) {
      toast.error("修改 tier 失败: " + (e?.message || e));
    }
  };

  const removeStock = async (code: string) => {
    if (!selectedId) return;
    try {
      await api.sectorPoolRemoveStock(selectedId, code);
      await reloadStocks();
      await reloadPools();
    } catch (e: any) {
      toast.error("移除失败: " + (e?.message || e));
    }
  };

  // ── render ──
  return (
    <div className="flex-1 flex overflow-hidden">
      {/* 左：分组树 */}
      <aside className="w-44 border-r border-ink-800 bg-ink-900/50 flex flex-col">
        <div className="px-3 py-2.5 border-b border-ink-800 text-sm font-semibold text-white">
          分组
        </div>
        <div className="flex-1 overflow-y-auto py-1">
          <button
            onClick={() => setActiveCategory(null)}
            className={
              "w-full text-left px-3 py-2 text-xs transition " +
              (activeCategory == null
                ? "bg-ink-800 text-white"
                : "text-ink-300 hover:bg-ink-850")
            }
          >
            全部
            <span className="text-ink-500 ml-1">
              ({pools.length}赛道 / {pools.reduce((s, p) => s + (p.stock_count || 0), 0)}股)
            </span>
          </button>
          {grouped.map(([cat, items]) => {
            const catStocks = items.reduce((s, p) => s + (p.stock_count || 0), 0);
            return (
              <button
                key={cat}
                onClick={() => setActiveCategory(cat)}
                className={
                  "w-full text-left px-3 py-2 text-xs transition " +
                  (activeCategory === cat
                    ? "bg-ink-800 text-white"
                    : "text-ink-300 hover:bg-ink-850")
                }
              >
                {cat}
                <span className="text-ink-500 ml-1">
                  ({items.length}/{catStocks})
                </span>
              </button>
            );
          })}
        </div>
      </aside>

      {/* 中：赛道列表 */}
      <aside className="w-64 border-r border-ink-800 bg-ink-900/30 flex flex-col">
        <div className="flex items-center justify-between px-3 py-2.5 border-b border-ink-800">
          <div className="text-sm font-semibold text-white">
            赛道{" "}
            <span className="text-ink-500 text-xs font-normal">
              ({filteredPools.length}赛道 / {filteredPools.reduce((s, p) => s + (p.stock_count || 0), 0)}股)
            </span>
          </div>
          <button
            onClick={() => setShowNewSector(true)}
            className="text-xs px-2 py-1 rounded bg-gold/15 text-gold hover:bg-gold/25"
          >
            ＋ 新建
          </button>
        </div>
        <div className="flex-1 overflow-y-auto">
          {err && <div className="m-3 text-xs text-red-400">加载失败：{err}</div>}
          {filteredPools.length === 0 && !err && (
            <div className="px-3 py-6 text-xs text-ink-500 leading-relaxed text-center">
              {activeCategory ? `「${activeCategory}」` : ""}还没有赛道
              <br />点 <span className="text-gold">＋ 新建</span> 开始维护
            </div>
          )}
          {filteredPools.map((p) => (
            <div
              key={p.id}
              className={
                "group border-l-2 px-3 py-2 cursor-pointer transition " +
                (p.id === selectedId
                  ? "border-gold bg-ink-800"
                  : "border-transparent hover:bg-ink-850")
              }
              onClick={() => setSelectedId(p.id)}
            >
              <div className="flex items-center justify-between gap-2">
                <div className="text-sm text-white truncate">{p.name}</div>
                <div className="text-[11px] text-ink-500 font-mono">{p.stock_count}</div>
              </div>
              {p.description && (
                <div className="text-[11px] text-ink-500 mt-0.5 truncate">{p.description}</div>
              )}
              <div className="opacity-0 group-hover:opacity-100 transition flex gap-2 mt-1 text-[11px]">
                <button
                  onClick={(e) => { e.stopPropagation(); setShowEditSector(p); }}
                  className="text-ink-400 hover:text-gold"
                >编辑</button>
                <button
                  onClick={(e) => { e.stopPropagation(); setConfirmDeletePool(p); }}
                  className="text-ink-400 hover:text-red-400"
                >删除</button>
              </div>
            </div>
          ))}
        </div>
      </aside>

      {/* 右：个股表格 */}
      <main className="flex-1 flex flex-col overflow-hidden">
        {selectedPool == null ? (
          <div className="h-full flex items-center justify-center text-ink-500 text-sm">
            请选择一个赛道
          </div>
        ) : (
          <>
            {/* header */}
            <div className="border-b border-ink-800 px-4 py-3 flex items-start justify-between">
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <h2 className="text-base font-semibold text-white truncate">{selectedPool.name}</h2>
                  {selectedPool.category && (
                    <span className="text-[10px] px-1.5 py-0.5 rounded bg-ink-800 text-ink-400">
                      {selectedPool.category}
                    </span>
                  )}
                  <span className="text-xs text-ink-500">{stocks.length} 只</span>
                </div>
                {selectedPool.description && (
                  <p className="text-xs text-ink-400 mt-1 leading-relaxed">{selectedPool.description}</p>
                )}
              </div>
              <div className="flex gap-2 shrink-0">
                <button
                  onClick={() => setShowAddStock(true)}
                  className="text-xs px-3 py-1.5 rounded bg-gold/15 text-gold hover:bg-gold/25"
                >
                  ＋ 加股票
                </button>
                <button
                  onClick={() => setShowBulkAdd(true)}
                  className="text-xs px-3 py-1.5 rounded bg-ink-800 text-ink-300 hover:bg-ink-700"
                >
                  📋 批量导入
                </button>
              </div>
            </div>

            {/* table */}
            <div className="flex-1 overflow-y-auto">
              {loadingStocks ? (
                <div className="p-8 text-center text-ink-500 text-sm">加载中…</div>
              ) : stocks.length === 0 ? (
                <div className="p-8 text-center text-ink-500 text-sm">
                  暂无个股。点 <span className="text-gold">＋ 加股票</span> 或 <span className="text-gold">📋 批量导入</span> 开始
                </div>
              ) : (
                <table className="w-full text-sm">
                  <thead className="sticky top-0 bg-ink-900 z-10">
                    <tr className="text-left text-xs text-ink-400 border-b border-ink-800">
                      <SortHeader k="tier" label="Tier" sortKey={sortKey} sortDir={sortDir} onSort={toggleSort} />
                      <SortHeader k="code" label="代码" sortKey={sortKey} sortDir={sortDir} onSort={toggleSort} />
                      <SortHeader k="name" label="名称" sortKey={sortKey} sortDir={sortDir} onSort={toggleSort} />
                      <SortHeader k="industry" label="行业" sortKey={sortKey} sortDir={sortDir} onSort={toggleSort} />
                      <th className="px-3 py-2 w-20 text-right">操作</th>
                    </tr>
                  </thead>
                  <tbody>
                    {sortedStocks.map((s) => {
                      const tl = TIER_LABEL[s.tier] || TIER_LABEL[3];
                      return (
                        <tr key={s.code} className="border-b border-ink-850 hover:bg-ink-850/40">
                          <td className="px-3 py-2">
                            <select
                              value={s.tier}
                              onChange={(e) => changeTier(s.code, Number(e.target.value))}
                              className={
                                "text-[11px] px-1.5 py-0.5 rounded border bg-transparent outline-none cursor-pointer " +
                                tl.cls
                              }
                            >
                              <option value={1} className="bg-ink-900">龙一</option>
                              <option value={2} className="bg-ink-900">龙二</option>
                              <option value={3} className="bg-ink-900">跟风</option>
                            </select>
                          </td>
                          <td className="px-3 py-2 font-mono">
                            <a
                              href={`/stock/${s.code}`}
                              onClick={(e) => { e.preventDefault(); window.location.href = `/stock/${s.code}`; }}
                              className="text-gold hover:underline"
                              title="查看K线"
                            >
                              {s.code}
                            </a>
                          </td>
                          <td className="px-3 py-2">
                            {s.name ? (
                              <a
                                href={`/stock/${s.code}`}
                                onClick={(e) => { e.preventDefault(); window.location.href = `/stock/${s.code}`; }}
                                className="text-white hover:text-gold"
                                title="查看K线"
                              >
                                {s.name}
                              </a>
                            ) : (
                              <span className="text-ink-500">—</span>
                            )}
                          </td>
                          <td className="px-3 py-2 text-ink-400">{s.industry || "—"}</td>
                          <td className="px-3 py-2 text-right">
                            <button
                              onClick={() => removeStock(s.code)}
                              className="text-[11px] text-ink-500 hover:text-red-400"
                            >
                              移除
                            </button>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              )}
            </div>
          </>
        )}
      </main>

      {/* ── modals ── */}
      {showNewSector && (
        <InputModal
          title="新建赛道"
          label={activeCategory && activeCategory !== "未分组"
            ? `将归入分组「${activeCategory}」`
            : "未指定分组（可在编辑里设置）"}
          placeholder="如：CPO / 液冷 / AI服务器"
          onCancel={() => setShowNewSector(false)}
          onConfirm={createSector}
        />
      )}

      {showEditSector && (
        <EditSectorModal
          pool={showEditSector}
          onCancel={() => setShowEditSector(null)}
          onSave={updateSector}
        />
      )}

      {confirmDeletePool && (
        <ConfirmModal
          title="归档赛道"
          message={`确定归档「${confirmDeletePool.name}」？归档后不会出现在选股池里，但历史数据仍保留。再次删除则会物理删除。`}
          confirmLabel="归档"
          danger
          onCancel={() => setConfirmDeletePool(null)}
          onConfirm={deletePool}
        />
      )}

      {showAddStock && (
        <InputModal
          title="添加个股"
          label="输入 6 位股票代码"
          placeholder="如：300308"
          onCancel={() => setShowAddStock(false)}
          onConfirm={addStock}
        />
      )}

      {showBulkAdd && (
        <BulkAddModal
          onCancel={() => setShowBulkAdd(false)}
          onConfirm={bulkAdd}
        />
      )}
    </div>
  );
}

// ── helpers ─────────────────────────────────────────────────

function SortHeader({
  k, label, sortKey, sortDir, onSort,
}: {
  k: SortKey;
  label: string;
  sortKey: SortKey;
  sortDir: SortDir;
  onSort: (k: SortKey) => void;
}) {
  const active = k === sortKey;
  return (
    <th
      className="px-3 py-2 cursor-pointer select-none hover:text-gold"
      onClick={() => onSort(k)}
    >
      {label}
      {active && <span className="ml-1 text-gold">{sortDir === "asc" ? "▲" : "▼"}</span>}
    </th>
  );
}

function EditSectorModal({
  pool, onCancel, onSave,
}: {
  pool: SectorPool;
  onCancel: () => void;
  onSave: (patch: { name?: string; category?: string; description?: string }) => Promise<void>;
}) {
  const [name, setName] = useState(pool.name);
  const [category, setCategory] = useState(pool.category);
  const [description, setDescription] = useState(pool.description);
  const [saving, setSaving] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim()) return;
    setSaving(true);
    try {
      await onSave({ name: name.trim(), category: category.trim(), description });
    } finally {
      setSaving(false);
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60"
      onClick={(e) => e.target === e.currentTarget && onCancel()}
    >
      <form
        onSubmit={submit}
        className="bg-ink-900 border border-ink-700 rounded-xl shadow-2xl w-full max-w-md mx-4 overflow-hidden"
      >
        <div className="px-6 pt-5 pb-4 space-y-3">
          <h3 className="text-base font-semibold text-white">编辑赛道</h3>
          <label className="block">
            <div className="text-xs text-ink-400 mb-1">名称</div>
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              className="w-full px-3 py-2 text-sm rounded bg-ink-800 border border-ink-600 text-white focus:border-gold focus:outline-none"
            />
          </label>
          <label className="block">
            <div className="text-xs text-ink-400 mb-1">分组 (category)</div>
            <input
              value={category}
              onChange={(e) => setCategory(e.target.value)}
              placeholder="如：AI算力 / 新能源"
              className="w-full px-3 py-2 text-sm rounded bg-ink-800 border border-ink-600 text-white placeholder-ink-500 focus:border-gold focus:outline-none"
            />
          </label>
          <label className="block">
            <div className="text-xs text-ink-400 mb-1">备注（赛道逻辑/催化）</div>
            <textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              rows={3}
              className="w-full px-3 py-2 text-sm rounded bg-ink-800 border border-ink-600 text-white focus:border-gold focus:outline-none resize-none"
            />
          </label>
        </div>
        <div className="px-6 pb-5 flex justify-end gap-3">
          <button
            type="button"
            onClick={onCancel}
            className="px-4 py-2 text-sm rounded-lg border border-ink-700 text-ink-300 hover:bg-ink-800"
          >
            取消
          </button>
          <button
            type="submit"
            disabled={saving || !name.trim()}
            className="px-4 py-2 text-sm rounded-lg font-medium bg-gold hover:bg-gold/80 text-black disabled:opacity-40"
          >
            {saving ? "保存中…" : "保存"}
          </button>
        </div>
      </form>
    </div>
  );
}

function BulkAddModal({
  onCancel, onConfirm,
}: {
  onCancel: () => void;
  onConfirm: (text: string) => void;
}) {
  const [text, setText] = useState("");

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60"
      onClick={(e) => e.target === e.currentTarget && onCancel()}
    >
      <div className="bg-ink-900 border border-ink-700 rounded-xl shadow-2xl w-full max-w-lg mx-4 overflow-hidden">
        <div className="px-6 pt-5 pb-4 space-y-2">
          <h3 className="text-base font-semibold text-white">批量导入个股</h3>
          <p className="text-xs text-ink-400">
            粘贴任意文本，系统会自动提取所有 <span className="text-gold">6 位股票代码</span>。
            新加入的股票默认 <span className="text-blue-300">tier=2 (龙二)</span>，加入后可单独修改。
          </p>
          <textarea
            value={text}
            onChange={(e) => setText(e.target.value)}
            rows={8}
            placeholder="300308 300394 002463 ..."
            className="w-full px-3 py-2 text-sm font-mono rounded bg-ink-800 border border-ink-600 text-white placeholder-ink-500 focus:border-gold focus:outline-none resize-none"
          />
        </div>
        <div className="px-6 pb-5 flex justify-end gap-3">
          <button
            onClick={onCancel}
            className="px-4 py-2 text-sm rounded-lg border border-ink-700 text-ink-300 hover:bg-ink-800"
          >
            取消
          </button>
          <button
            onClick={() => onConfirm(text)}
            disabled={!text.trim()}
            className="px-4 py-2 text-sm rounded-lg font-medium bg-gold hover:bg-gold/80 text-black disabled:opacity-40"
          >
            导入
          </button>
        </div>
      </div>
    </div>
  );
}
