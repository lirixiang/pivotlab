// 通用弹窗组件：ConfirmModal (替代 window.confirm) + InputModal (替代 window.prompt)
import { useEffect, useRef, useState } from "react";

/* ── backdrop ── */
function Overlay({
  children,
  onClose,
}: {
  children: React.ReactNode;
  onClose: () => void;
}) {
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm"
      onClick={(e) => e.target === e.currentTarget && onClose()}
    >
      {children}
    </div>
  );
}

/* ── ConfirmModal ── */
export function ConfirmModal({
  title,
  message,
  confirmLabel = "确认",
  cancelLabel = "取消",
  danger = false,
  onConfirm,
  onCancel,
}: {
  title: string;
  message: string;
  confirmLabel?: string;
  cancelLabel?: string;
  danger?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const confirmRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    confirmRef.current?.focus();
  }, []);

  return (
    <Overlay onClose={onCancel}>
      <div className="bg-ink-900 border border-ink-700 rounded-xl shadow-2xl w-full max-w-md mx-4 overflow-hidden">
        <div className="px-6 pt-5 pb-4">
          <h3 className="text-base font-semibold text-white">{title}</h3>
          <p className="text-sm text-ink-400 mt-2 leading-relaxed">{message}</p>
        </div>
        <div className="px-6 pb-5 flex justify-end gap-3">
          <button
            onClick={onCancel}
            className="px-4 py-2 text-sm rounded-lg border border-ink-700 text-ink-300 hover:bg-ink-800 transition"
          >
            {cancelLabel}
          </button>
          <button
            ref={confirmRef}
            onClick={onConfirm}
            className={
              "px-4 py-2 text-sm rounded-lg font-medium transition " +
              (danger
                ? "bg-red-600 hover:bg-red-500 text-white"
                : "bg-gold hover:bg-gold/80 text-black")
            }
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </Overlay>
  );
}

/* ── InputModal ── */
export function InputModal({
  title,
  label,
  defaultValue = "",
  placeholder = "",
  confirmLabel = "确定",
  cancelLabel = "取消",
  onConfirm,
  onCancel,
}: {
  title: string;
  label?: string;
  defaultValue?: string;
  placeholder?: string;
  confirmLabel?: string;
  cancelLabel?: string;
  onConfirm: (value: string) => void;
  onCancel: () => void;
}) {
  const [value, setValue] = useState(defaultValue);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    inputRef.current?.focus();
    inputRef.current?.select();
  }, []);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (value.trim()) onConfirm(value.trim());
  };

  return (
    <Overlay onClose={onCancel}>
      <div className="bg-ink-900 border border-ink-700 rounded-xl shadow-2xl w-full max-w-md mx-4 overflow-hidden">
        <form onSubmit={handleSubmit}>
          <div className="px-6 pt-5 pb-4">
            <h3 className="text-base font-semibold text-white">{title}</h3>
            {label && (
              <p className="text-sm text-ink-400 mt-1">{label}</p>
            )}
            <input
              ref={inputRef}
              type="text"
              value={value}
              onChange={(e) => setValue(e.target.value)}
              placeholder={placeholder}
              className="mt-3 w-full px-3 py-2 text-sm rounded-lg bg-ink-800 border border-ink-600 text-white placeholder-ink-500 focus:border-gold focus:outline-none"
            />
          </div>
          <div className="px-6 pb-5 flex justify-end gap-3">
            <button
              type="button"
              onClick={onCancel}
              className="px-4 py-2 text-sm rounded-lg border border-ink-700 text-ink-300 hover:bg-ink-800 transition"
            >
              {cancelLabel}
            </button>
            <button
              type="submit"
              disabled={!value.trim()}
              className="px-4 py-2 text-sm rounded-lg font-medium bg-gold hover:bg-gold/80 text-black transition disabled:opacity-40"
            >
              {confirmLabel}
            </button>
          </div>
        </form>
      </div>
    </Overlay>
  );
}
