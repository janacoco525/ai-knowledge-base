import React, { useState, useEffect } from "react";
import { RefreshCw } from "lucide-react";

// 全局扫描状态指示器（2026-08-05 自 App.tsx 提取）
export default function ScanStatusIndicator() {
  const [scanStatus, setScanStatus] = useState<{ is_scanning: boolean; indexed: number; total: number } | null>(null);

  useEffect(() => {
    // ⛔ 2026-08-19 降频：扫描中 3s 轮询，空闲 30s（原固定 3s 高频请求刷屏）
    let timer: number | undefined;
    let cancelled = false;
    const checkScan = () => {
      fetch("/api/scan/status")
        .then(r => r.json())
        .then(data => {
          if (cancelled) return;
          const isScanning = !!data.is_scanning;
          setScanStatus({
            is_scanning: isScanning,
            indexed: data.indexed || 0,
            total: data.total || 0,
          });
          timer = window.setTimeout(checkScan, isScanning ? 3000 : 30000);
        })
        .catch(() => { if (!cancelled) setScanStatus(null); });
    };
    checkScan();
    return () => { cancelled = true; if (timer) clearTimeout(timer); };
  }, []);

  if (!scanStatus || !scanStatus.is_scanning) return null;

  const progress = scanStatus.total > 0 ? Math.round((scanStatus.indexed / scanStatus.total) * 100) : 0;

  return (
    <div className="mt-3 p-2 bg-emerald-50 border border-emerald-200 rounded-lg">
      <div className="flex items-center justify-between mb-1">
        <span className="text-[10px] font-bold text-emerald-700 flex items-center gap-1">
          <RefreshCw className="w-3 h-3 animate-spin" />
          扫描中
        </span>
        <button
          onClick={() => fetch("/api/scan/stop", { method: "POST" })}
          className="text-[9px] text-rose-500 hover:text-rose-700 font-bold"
        >
          停止
        </button>
      </div>
      <div className="w-full bg-emerald-200 rounded-full h-1.5">
        <div className="bg-emerald-500 h-1.5 rounded-full transition-all" style={{ width: `${progress}%` }} />
      </div>
      <span className="text-[9px] text-emerald-600 mt-1 block">{scanStatus.indexed}/{scanStatus.total}</span>
    </div>
  );
}
