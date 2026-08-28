import React, { useState } from "react";
import { Loader2, Languages } from "lucide-react";

interface TranslatePanelProps {
  selectedText: string;      // 选中内容（翻译选中用）
  fullText: string;          // 全文（翻译全文用）
  onStatus: (msg: string) => void;  // 状态提示回调
}

// 全文翻译面板（2026-08-05 自 DocEditor 抽出，控制其规模膨胀）
// 调用后端 /api/translate：非中文 → 简体中文，同文本缓存省 token
export default function TranslatePanel({ selectedText, fullText, onStatus }: TranslatePanelProps) {
  const [translationResult, setTranslationResult] = useState("");
  const [isTranslating, setIsTranslating] = useState(false);

  const handleTranslate = async (target: "selected" | "full") => {
    const sourceText = target === "selected" ? selectedText : fullText;
    if (!sourceText.trim()) {
      onStatus("没有可翻译的内容");
      setTimeout(() => onStatus(""), 3000);
      return;
    }
    setIsTranslating(true);
    setTranslationResult("");
    onStatus("正在翻译为中文...");
    try {
      const resp = await fetch("/api/translate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: sourceText, target_lang: "zh" }),
      });
      if (!resp.ok) throw new Error("翻译服务暂时不可用");
      const data = await resp.json();
      if (data.skipped) {
        setTranslationResult("内容已是中文，无需翻译");
      } else {
        setTranslationResult(data.translation);
      }
      onStatus("翻译完成");
    } catch (err: any) {
      onStatus(`翻译失败: ${err.message || "请求异常"}`);
    } finally {
      setIsTranslating(false);
      setTimeout(() => onStatus(""), 4000);
    }
  };

  return (
    <>
      {/* 翻译行 */}
      <div className="flex gap-2">
        <button
          disabled={!selectedText.trim() || isTranslating}
          onClick={() => handleTranslate("selected")}
          className={`flex-1 py-1.5 rounded-lg text-[10px] font-bold transition-all flex items-center justify-center gap-1 ${
            isTranslating
              ? "bg-zinc-100 text-zinc-400 cursor-not-allowed"
              : "bg-sky-700 text-white hover:bg-sky-600 cursor-pointer"
          }`}
        >
          {isTranslating ? <Loader2 className="w-3 h-3 animate-spin" /> : <Languages className="w-3 h-3" />}
          翻译选中
        </button>
        <button
          disabled={isTranslating}
          onClick={() => handleTranslate("full")}
          className={`flex-1 py-1.5 rounded-lg text-[10px] font-bold transition-all flex items-center justify-center gap-1 ${
            isTranslating
              ? "bg-zinc-100 text-zinc-400 cursor-not-allowed"
              : "bg-sky-800 text-white hover:bg-sky-700 cursor-pointer"
          }`}
        >
          {isTranslating ? <Loader2 className="w-3 h-3 animate-spin" /> : <Languages className="w-3 h-3" />}
          翻译全文
        </button>
      </div>

      {/* 翻译结果 */}
      {translationResult ? (
        <div className="p-3 bg-white border border-sky-200 rounded-lg">
          <div className="flex items-center justify-between mb-2">
            <span className="text-[10px] font-bold text-sky-700">翻译结果</span>
            <button
              onClick={() => { navigator.clipboard.writeText(translationResult); onStatus("已复制"); setTimeout(() => onStatus(""), 2000); }}
              className="text-[9px] text-zinc-400 hover:text-zinc-600"
            >复制</button>
          </div>
          <div className="text-[11px] text-zinc-700 leading-relaxed max-h-60 overflow-y-auto pr-1 custom-scrollbar whitespace-pre-wrap">
            {translationResult}
          </div>
        </div>
      ) : null}
    </>
  );
}
