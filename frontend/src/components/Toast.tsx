/**
 * 全局 Toast 通知系统
 * 用法：
 *   import { toast } from "./Toast";
 *   toast.success("已保存");
 *   toast.error("操作失败");
 *   toast.info("提示信息");
 *
 * 在 App 根组件放一次 <ToastContainer />
 */

import { useEffect, useState } from "react";

type ToastType = "success" | "error" | "info" | "warn";

interface ToastItem {
  id: number;
  type: ToastType;
  message: string;
  duration: number;
}

let _id = 0;
let _listeners: ((items: ToastItem[]) => void)[] = [];
let _items: ToastItem[] = [];

function notify() {
  _listeners.forEach((fn) => fn([..._items]));
}

function add(type: ToastType, message: string, duration = 3500) {
  const item: ToastItem = { id: ++_id, type, message, duration };
  _items = [..._items, item];
  notify();
  setTimeout(() => {
    _items = _items.filter((i) => i.id !== item.id);
    notify();
  }, duration);
}

export const toast = {
  success: (msg: string, ms?: number) => add("success", msg, ms),
  error: (msg: string, ms?: number) => add("error", msg, ms ?? 5000),
  info: (msg: string, ms?: number) => add("info", msg, ms),
  warn: (msg: string, ms?: number) => add("warn", msg, ms ?? 4000),
};

const ICON: Record<ToastType, string> = {
  success: "✓",
  error: "✕",
  info: "ℹ",
  warn: "⚠",
};

const COLOR: Record<ToastType, string> = {
  success: "border-green-600 bg-green-950/90 text-green-200",
  error: "border-red-600 bg-red-950/90 text-red-200",
  info: "border-blue-600 bg-blue-950/90 text-blue-200",
  warn: "border-amber-600 bg-amber-950/90 text-amber-200",
};

const ICON_COLOR: Record<ToastType, string> = {
  success: "bg-green-700 text-white",
  error: "bg-red-700 text-white",
  info: "bg-blue-700 text-white",
  warn: "bg-amber-700 text-white",
};

export function ToastContainer() {
  const [items, setItems] = useState<ToastItem[]>([]);

  useEffect(() => {
    _listeners.push(setItems);
    return () => {
      _listeners = _listeners.filter((fn) => fn !== setItems);
    };
  }, []);

  if (items.length === 0) return null;

  return (
    <div className="fixed top-4 right-4 z-[9999] flex flex-col gap-2 pointer-events-none">
      {items.map((item) => (
        <div
          key={item.id}
          className={
            "pointer-events-auto flex items-center gap-3 px-4 py-3 rounded-lg border shadow-xl backdrop-blur-sm " +
            "animate-slide-in min-w-[280px] max-w-[420px] " +
            COLOR[item.type]
          }
        >
          <span
            className={
              "w-6 h-6 rounded-full flex items-center justify-center text-xs font-bold shrink-0 " +
              ICON_COLOR[item.type]
            }
          >
            {ICON[item.type]}
          </span>
          <span className="text-sm leading-snug flex-1">{item.message}</span>
          <button
            onClick={() => {
              _items = _items.filter((i) => i.id !== item.id);
              notify();
            }}
            className="text-xs opacity-50 hover:opacity-100 shrink-0"
          >
            ✕
          </button>
        </div>
      ))}
    </div>
  );
}
