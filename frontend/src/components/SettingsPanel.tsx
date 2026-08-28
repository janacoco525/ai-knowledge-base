import React, { useState, useEffect } from "react";
import {
  RefreshCw, Server, CheckCircle2, Loader2, Settings, Download, Upload, FileText
} from "lucide-react";
import { buildLocalDataBundle, applyLocalDataBundle } from "../lib/userData";

interface SettingsPanelProps {
  // Server state
  serverHealthy: boolean | null;
  setServerHealthy: React.Dispatch<React.SetStateAction<boolean | null>>;
  healthMessage: string;
  serverLoading: boolean;

  // Providers state
  apiProviders: Array<{
    id: string; name: string; base_url: string; models: string[]; docs: string;
    keyConfigured?: boolean; keyPreview?: string; configuredModel?: string;
  }>;
  setApiProviders: React.Dispatch<React.SetStateAction<Array<any>>>;
  selectedProvider: string;
  setSelectedProvider: React.Dispatch<React.SetStateAction<string>>;
  
  // Active settings
  activeProviderId: string;
  setActiveProviderId: React.Dispatch<React.SetStateAction<string>>;
  activeModelName: string;
  setActiveModelName: React.Dispatch<React.SetStateAction<string>>;
  activeKeyPreview: string;
  setActiveKeyPreview: React.Dispatch<React.SetStateAction<string>>;

  currentProviderKeyPreview: string;
  setCurrentProviderKeyPreview: React.Dispatch<React.SetStateAction<string>>;
  currentProviderHasKey: boolean;
  setCurrentProviderHasKey: React.Dispatch<React.SetStateAction<boolean>>;
}

export default function SettingsPanel({
  serverHealthy,
  setServerHealthy,
  healthMessage,
  serverLoading,
  apiProviders,
  setApiProviders,
  selectedProvider,
  setSelectedProvider,
  activeProviderId,
  setActiveProviderId,
  activeModelName,
  setActiveModelName,
  activeKeyPreview,
  setActiveKeyPreview,
  currentProviderKeyPreview,
  setCurrentProviderKeyPreview,
  currentProviderHasKey,
  setCurrentProviderHasKey,
}: SettingsPanelProps) {
  // API Key 只保留在受控输入框中，不写入浏览器 localStorage。
  const [inputApiKey, setInputApiKey] = useState("");
  const [inputApiEndpoint, setInputApiEndpoint] = useState(() => localStorage.getItem("kb_llm_api_endpoint") || "");
  const [inputModelName, setInputModelName] = useState(() => localStorage.getItem("kb_llm_model_name") || "");
  const [endpointNote, setEndpointNote] = useState("");
  const [isClearingConfig, setIsClearingConfig] = useState(false);
  const [isSwitchingProvider, setIsSwitchingProvider] = useState(false);
  const [isVerifyingKey, setIsVerifyingKey] = useState(false);
  const [verificationFeedback, setVerificationFeedback] = useState<{
    type: "success" | "error" | null;
    text: string;
  }>({ type: null, text: "" });

  // 429 Cooldown
  const [validationCooldownUntil, setValidationCooldownUntil] = useState<number>(0);
  const isValidationCoolingDown = validationCooldownUntil > Date.now();
  const validationCooldownSeconds = Math.max(0, Math.ceil((validationCooldownUntil - Date.now()) / 1000));

  // 扫描路径管理（T25 前端接入，2026-08-05）
  const [scanPaths, setScanPaths] = useState<string[]>([]);
  const [scanPathsLoading, setScanPathsLoading] = useState(true);
  const [newPathInput, setNewPathInput] = useState("");
  const [scanPathsMsg, setScanPathsMsg] = useState<{ type: "success" | "error" | null; text: string }>({ type: null, text: "" });
  const loadScanPaths = async () => {
    try {
      const r = await fetch("/api/scan/paths");
      if (r.ok) {
        const d = await r.json();
        setScanPaths(d.paths || []);
      }
    } catch {
      /* 服务不可达时静默，避免设置页报错 */
    } finally {
      setScanPathsLoading(false);
    }
  };
  useEffect(() => { loadScanPaths(); }, []);
  const handleAddScanPath = async () => {
    const p = newPathInput.trim();
    if (!p) return;
    try {
      const r = await fetch("/api/scan/paths", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path: p }),
      });
      const d = await r.json();
      if (r.ok) {
        setScanPaths(d.paths || scanPaths);
        setNewPathInput("");
        setScanPathsMsg({ type: "success", text: d.message || "已添加" });
      } else {
        setScanPathsMsg({ type: "error", text: d.detail || `HTTP ${r.status}` });
      }
    } catch (e: any) {
      setScanPathsMsg({ type: "error", text: `添加失败: ${e.message || e}` });
    }
  };
  const handleDeleteScanPath = async (index: number) => {
    try {
      const r = await fetch(`/api/scan/paths/${index}`, { method: "DELETE" });
      const d = await r.json();
      if (r.ok) {
        setScanPaths(d.paths || scanPaths);
        setScanPathsMsg({ type: "success", text: d.message || "已删除" });
      } else {
        setScanPathsMsg({ type: "error", text: d.detail || `HTTP ${r.status}` });
      }
    } catch (e: any) {
      setScanPathsMsg({ type: "error", text: `删除失败: ${e.message || e}` });
    }
  };

  // Sync validation cooldown timer
  useEffect(() => {
    if (!isValidationCoolingDown) return;
    const interval = setInterval(() => {
      if (Date.now() >= validationCooldownUntil) {
        setValidationCooldownUntil(0);
        clearInterval(interval);
      }
    }, 1000);
    return () => clearInterval(interval);
  }, [validationCooldownUntil, isValidationCoolingDown]);

  // Normalize endpoint helper
  const normalizeEndpoint = (url: string): string => {
    return url
      .replace(/\/+$/, '')
      .replace(/\/chat\/completions\/?$/, '')
      .replace(/\/completions\/?$/, '')
      .replace(/\/v1\/?$/, m => m.endsWith('/') ? '/v1' : m);
  };

  // Sync inputs when activeProvider changes
  useEffect(() => {
    const prov = apiProviders.find(p => p.id === selectedProvider);
    if (prov) {
      if (!inputApiEndpoint) setInputApiEndpoint(prov.base_url);
      if (!inputModelName) setInputModelName(prov.configuredModel || prov.models[0] || "");
    } else if (selectedProvider !== "custom" && apiProviders.length > 0) {
      setSelectedProvider(activeProviderId || apiProviders[0].id);
    }
  }, [selectedProvider, apiProviders, activeProviderId]);

  const handleProviderSelect = (pid: string) => {
    setSelectedProvider(pid);
    setVerificationFeedback({ type: null, text: "" });
    if (pid === "custom") {
      setInputApiKey("");
      setCurrentProviderKeyPreview("");
      setCurrentProviderHasKey(false);
      return;
    }
    const prov = apiProviders.find(p => p.id === pid);
    if (prov) {
      setInputApiEndpoint(prov.base_url);
      setInputModelName(prov.configuredModel || prov.models[0] || "");
      localStorage.setItem("kb_llm_api_endpoint", prov.base_url);
      localStorage.setItem("kb_llm_model_name", prov.configuredModel || prov.models[0] || "");
      if (prov.keyConfigured && prov.keyPreview) {
        setInputApiKey("");
        setCurrentProviderKeyPreview(prov.keyPreview);
        setCurrentProviderHasKey(true);
      } else {
        setInputApiKey("");
        setCurrentProviderKeyPreview("");
        setCurrentProviderHasKey(false);
      }
    }
  };

  const handleSwitchProvider = async (providerId: string) => {
    setIsSwitchingProvider(true);
    setVerificationFeedback({ type: null, text: "" });
    try {
      const resp = await fetch("/api/providers/switch", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ providerId }),
      });
      const data = await resp.json();
      if (data.success) {
        setActiveProviderId(data.providerId);
        setActiveModelName(data.model);
        setServerHealthy(true);
        setVerificationFeedback({
          type: "success",
          text: data.message + "（已即时生效，无需重启）",
        });
        const prov = apiProviders.find(p => p.id === providerId);
        if (prov) {
          setInputApiEndpoint(prov.base_url);
          setInputModelName(data.model || "");
          localStorage.setItem("kb_llm_api_endpoint", prov.base_url);
          localStorage.setItem("kb_llm_model_name", data.model || "");
          setActiveKeyPreview(prov.keyPreview || "");
        }
        const hResp = await fetch("/health").then(r => r.json());
        if (hResp.providers) {
          setApiProviders(hResp.providers);
          setActiveProviderId(hResp.activeProviderId || "");
          setActiveModelName(hResp.modelName || "");
          setActiveKeyPreview(hResp.apiKeyPreview || "");
          setServerHealthy(hResp.apiKeyPresent);
        }
      } else {
        setVerificationFeedback({ type: "error", text: data.error || "切换失败" });
      }
    } catch (err: any) {
      setVerificationFeedback({ type: "error", text: "切换失败: " + (err.message || err) });
    } finally {
      setIsSwitchingProvider(false);
    }
  };

  const handleExportLocalData = () => {
    const payload = {
      ...buildLocalDataBundle(),
      scope: "browser-local-data",
      generator: "AI-RAG-Knowledge-Database",
    };
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `kb-local-data-${new Date().toISOString().split("T")[0]}.json`;
    link.click();
    URL.revokeObjectURL(url);
  };

  const handleImportLocalData = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = (event) => {
      try {
        const data = JSON.parse(event.target?.result as string);
        if (!data || typeof data !== "object" || data.scope !== "browser-local-data") {
          alert("❌ 无法导入：这不是本应用导出的本地数据文件。");
          return;
        }
        if (!window.confirm("导入将覆盖当前浏览器的本地数据（知识卡/词条/图谱/文件夹/标注等）。确定继续吗？")) return;
        const restored = applyLocalDataBundle(data.data || data);
        alert(`✅ 已导入 ${restored} 项本地数据，请刷新页面使其生效。`);
      } catch {
        alert("❌ 无法导入：JSON 解析失败。");
      } finally {
        e.target.value = "";
      }
    };
    reader.readAsText(file);
  };

  // ⛔ 2026-08-14（任务三十）：从服务端下载迁移指南（单一数据源 docs/数据迁移指南.md）
  const handleDownloadMigrationGuide = async () => {
    try {
      const resp = await fetch("/api/migration-guide");
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();
      const blob = new Blob([data.content || ""], { type: "text/markdown;charset=utf-8" });
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = data.filename || "数据迁移指南.md";
      link.click();
      URL.revokeObjectURL(url);
    } catch {
      alert("❌ 下载失败：无法获取迁移指南，请确认服务已启动。");
    }
  };

  const handleSaveAndValidateKey = async () => {
    if (!inputApiKey.trim()) {
      setVerificationFeedback({ type: "error", text: "API Key 不能为空" });
      return;
    }
    if (isValidationCoolingDown) {
      setVerificationFeedback({ type: "error", text: `该提供商正在冷却中，请 ${validationCooldownSeconds} 秒后再试` });
      return;
    }
    setIsVerifyingKey(true);
    setVerificationFeedback({ type: null, text: "" });

    const afterSave = async () => {
      localStorage.setItem("kb_llm_api_endpoint", inputApiEndpoint);
      localStorage.setItem("kb_llm_model_name", inputModelName);
      localStorage.removeItem("kb_llm_api_key");
      const hResp = await fetch("/health").then(r => r.json());
      if (hResp.providers) {
        setApiProviders(hResp.providers);
        const prov = hResp.providers.find((p: any) => p.id === selectedProvider);
        if (prov && prov.keyConfigured && prov.keyPreview) {
          setInputApiKey("");
          setCurrentProviderKeyPreview(prov.keyPreview);
          setCurrentProviderHasKey(true);
        }
      }
    };

    try {
      const body = JSON.stringify({
        apiKey: inputApiKey.trim(),
        providerId: selectedProvider,
        model: inputModelName,
        baseUrl: selectedProvider === "custom" ? inputApiEndpoint : "",
      });

      const resp = await fetch("/api/providers/validate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body,
      });

      const resData = await resp.json();

      if (resData.success || resData.saved) {
        setServerHealthy(true);
        setActiveProviderId(selectedProvider || "custom");
        setActiveModelName(inputModelName);
        setActiveKeyPreview(inputApiKey.trim().slice(0, 8) + "***");
        setVerificationFeedback({
          type: "success",
          text: resData.message || (resData.saved ? "配置已保存（验证因网络波动跳过，稍后可用）" : "配置已保存，立即生效"),
        });
        await afterSave();
        const hResp = await fetch("/health").then(r => r.json());
        if (hResp.providers) {
          setApiProviders(hResp.providers);
          setActiveProviderId(hResp.activeProviderId || "");
          setActiveModelName(hResp.modelName || "");
          setActiveKeyPreview(hResp.apiKeyPreview || "");
          setServerHealthy(hResp.apiKeyPresent);
        }
      } else {
        const errText = resData.error || "验证失败";
        // ⛔ 2026-08-14（任务二十二）：429/限流 → 设置 30s 冷却倒计时（对齐 ChatPanel 先例）
        if (/429|限流|rate/i.test(errText)) {
          setValidationCooldownUntil(Date.now() + 30000);
        }
        setVerificationFeedback({ type: "error", text: errText });
      }
    } catch (err: any) {
      const errText = "网络错误: " + (err.message || err);
      if (/429|限流|rate/i.test(errText)) setValidationCooldownUntil(Date.now() + 30000);
      setVerificationFeedback({ type: "error", text: errText });
    } finally {
      setIsVerifyingKey(false);
    }
  };

  const handleDeleteProvider = async (providerId: string) => {
    const prov = apiProviders.find(p => p.id === providerId);
    const name = prov?.name || providerId;
    if (!window.confirm(`确定要删除「${name}」的 API Key 配置吗？`)) return;
    try {
      const resp = await fetch("/api/providers/delete", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ providerId }),
      });
      const data = await resp.json();
      if (data.success) {
        if (selectedProvider === providerId) {
          setSelectedProvider(activeProviderId && activeProviderId !== providerId ? activeProviderId : "");
        }
        const hResp = await fetch("/health").then(r => r.json());
        if (hResp.providers) {
          setApiProviders(hResp.providers);
          if (activeProviderId === providerId) {
            setActiveProviderId(hResp.activeProviderId || "");
            setActiveModelName(hResp.modelName || "");
            setActiveKeyPreview(hResp.apiKeyPreview || "");
          }
        }
      }
    } catch {}
  };

  const handleClearConfig = async () => {
    if (!window.confirm("确定要清除当前模型配置吗？")) return;
    setIsClearingConfig(true);
    try {
      const resp = await fetch("/api/providers/clear", { method: "POST" });
      const data = await resp.json();
      if (data.success) {
        setActiveProviderId("");
        setActiveModelName("");
        setActiveKeyPreview("");
        setServerHealthy(false);
        setInputApiKey("");
        setCurrentProviderKeyPreview("");
        setCurrentProviderHasKey(false);
        setInputApiEndpoint("");
        setInputModelName("");
        setVerificationFeedback({ type: null, text: "" });
        localStorage.removeItem("kb_llm_api_key");
        localStorage.removeItem("kb_llm_api_endpoint");
        localStorage.removeItem("kb_llm_model_name");
        
        const d = await fetch("/health").then(r => r.json());
        if (d.providers) {
          setApiProviders(d.providers);
          setActiveProviderId(d.activeProviderId || "");
          setActiveModelName(d.modelName || "");
          setActiveKeyPreview(d.apiKeyPreview || "");
          setServerHealthy(d.apiKeyPresent);
        }
      }
    } catch {
      // Silent catch
    } finally {
      setIsClearingConfig(false);
    }
  };

  return (
    <div className="h-full w-full p-5 bg-zinc-100/40 overflow-y-auto animate-fadeIn select-none space-y-6">
      
      {/* Header section */}
      <div className="bg-white border border-zinc-200 rounded-xl p-6 shadow-sm text-left">
        <h2 className="text-zinc-900 font-extrabold text-base flex items-center gap-2">
          {/* ⛔ 2026-08-14（任务二十六）：标题与现状对齐——统计/备份已删，剩模型配置+扫描路径 */}
          <Settings className="w-5 h-5 text-emerald-600" />
          设置管理
        </h2>
        <p className="text-xs text-zinc-500 mt-1">
          {/* ⛔ 2026-08-14（任务二十六）：备份面板已删，文案收敛为当前实际功能 */}
          在此配置 LLM 模型与知识库扫描路径，并查看服务连接状态。
          <span style={{color:"#10b981",fontWeight:700,fontSize:10,marginLeft:8}}>● {serverLoading ? "连接检测中…" : (healthMessage || "服务已连接")}</span>
        </p>
      </div>

      {/* ⛔ 2026-08-14（任务二十五）：数据流控制与备份面板已按用户要求移除 */}
      {/* Panel: 模型配置 */}
        <div className="bg-white border border-zinc-200 rounded-xl p-5 space-y-4 text-left shadow-sm">
          <div className="border-b border-zinc-100 pb-3 flex justify-between items-start">
            <div>
              <h3 className="text-zinc-850 font-bold text-xs uppercase tracking-widest flex items-center gap-1.5">
                <Server className="w-4 h-4 text-emerald-600" />
                模型配置
              </h3>
              <p className="text-[11px] text-zinc-400 mt-0.5">
                配置 API Key 以启用 AI 问答和 analytical 功能
              </p>
            </div>
            <div>
              <span className={`text-[10px] uppercase font-bold tracking-wider px-2 py-0.5 rounded-full ${
                serverHealthy === true 
                  ? "bg-emerald-50 text-emerald-700 border border-emerald-200" 
                  : "bg-orange-50 text-orange-700 border border-orange-200"
              }`}>
                {serverHealthy === true ? "API 已连接" : "未配置 API"}
              </span>
            </div>
          </div>

          {/* Dynamic Provider-Based API Config */}
          <div className="p-3 bg-zinc-50 border border-zinc-200 rounded-lg space-y-3">
            {/* 当前生效状态 */}
            <div style={{display:"flex", alignItems:"center", justifyContent:"space-between", fontSize:11}}>
              <div style={{display:"flex", alignItems:"center", gap:6, minWidth:0}}>
                <span style={{fontWeight:600, color:"#6b7280", flexShrink:0}}>当前生效:</span>
                {activeProviderId ? (
                  <>
                    <span style={{display:"inline-flex", alignItems:"center", gap:4, padding:"2px 8px", borderRadius:999, fontSize:10, fontWeight:700, background:"#ecfdf5", color:"#047857", border:"1px solid #a7f3d0"}}>
                      <span style={{width:6, height:6, borderRadius:"50%", background:"#10b981", flexShrink:0}} />
                      {apiProviders.find(p => p.id === activeProviderId)?.name || activeProviderId}
                    </span>
                    <span style={{fontFamily:"monospace", fontSize:11, fontWeight:600, color:"#374151", overflow:"hidden", textOverflow:"ellipsis", whiteSpace:"nowrap"}}>{activeModelName}</span>
                    {activeKeyPreview && (
                      <span style={{fontFamily:"monospace", fontSize:10, color:"#9ca3af", flexShrink:0}}>{activeKeyPreview}</span>
                    )}
                  </>
                ) : (
                  <span style={{fontSize:11, color:"#9ca3af"}}>未配置</span>
                )}
              </div>
              {activeProviderId && (
                <button type="button" onClick={handleClearConfig}
                disabled={isClearingConfig}
                style={{fontSize:10, color:"#9ca3af", border:"none", background:"none", cursor:"pointer"}}
                >{isClearingConfig ? "清除中..." : "清除"}</button>
              )}
            </div>

            {/* Provider Selector */}
            <div className="flex items-center gap-1.5 flex-wrap">
              <span className="text-[11px] font-semibold text-zinc-500 shrink-0">提供商:</span>
              {apiProviders.map(p => {
                const isActive = activeProviderId === p.id;
                return (
                  <button
                    key={p.id}
                    type="button"
                    onClick={() => handleProviderSelect(p.id)}
                    className={`px-2 py-0.5 rounded text-[11px] font-semibold border transition-all whitespace-nowrap relative ${
                      selectedProvider === p.id
                        ? "bg-emerald-600 text-white border-emerald-700"
                        : isActive
                          ? "bg-white text-emerald-600 border-emerald-400 ring-1 ring-emerald-300"
                          : "bg-white text-zinc-500 border-zinc-200 hover:border-emerald-300 hover:text-emerald-600"
                    }`}
                  >
                    {p.name}
                    {isActive && (
                      <span className="absolute -top-1.5 -right-1.5 text-[8px] bg-emerald-500 text-white px-1 py-px rounded-full leading-none">
                        生效
                      </span>
                    )}
                  </button>
                );
              })}
              <button
                type="button"
                onClick={() => handleProviderSelect("custom")}
                className={`px-2 py-0.5 rounded text-[11px] font-semibold border transition-all whitespace-nowrap relative ${
                  selectedProvider === "custom"
                    ? "bg-zinc-700 text-white border-zinc-800"
                    : activeProviderId === "custom"
                      ? "bg-white text-zinc-600 border-zinc-400 ring-1 ring-zinc-300"
                      : "bg-white text-zinc-400 border-zinc-200 hover:border-zinc-400 hover:text-zinc-600"
                }`}
              >
                自定义
                {activeProviderId === "custom" && (
                  <span className="absolute -top-1.5 -right-1.5 text-[8px] bg-zinc-500 text-white px-1 py-px rounded-full leading-none">
                    生效
                  </span>
                )}
              </button>
            </div>

            {/* Provider detail details */}
            {selectedProvider !== "custom" && (() => {
              const prov = apiProviders.find(p => p.id === selectedProvider);
              if (!prov) return null;
              return (
                <div className="space-y-2 pt-1 border-t border-zinc-200">
                  <div className="flex items-center gap-2 text-[10px]">
                    <span className="text-zinc-400 shrink-0">端点:</span>
                    <code className="text-[10px] font-mono text-zinc-600 bg-white px-1.5 py-0.5 rounded border border-zinc-200 truncate">{prov.base_url}</code>
                    {prov.keyConfigured && (
                      <button type="button" onClick={() => handleDeleteProvider(prov.id)}
                        className="text-[10px] text-red-400 hover:text-red-600 shrink-0 ml-auto cursor-pointer"
                        title="删除此提供商的 API Key"
                      >删除 Key</button>
                    )}
                    {prov.docs && !prov.keyConfigured && (
                      <a href={prov.docs} target="_blank" rel="noopener noreferrer"
                        className="text-[10px] text-blue-500 hover:text-blue-600 shrink-0 ml-auto"
                      >
                        获取 Key →
                      </a>
                    )}
                  </div>
                  <div className="flex items-center gap-1 flex-wrap">
                    <span className="text-[10px] text-zinc-400 shrink-0">模型:</span>
                    {prov.models.map(m => (
                      <button
                        key={m}
                        type="button"
                        onClick={() => setInputModelName(m)}
                        className={`px-1.5 py-0.5 rounded text-[10px] font-mono border transition-all ${
                          inputModelName === m
                            ? "bg-emerald-100 text-emerald-700 border-emerald-300"
                            : "bg-white text-zinc-500 border-zinc-150 hover:border-emerald-300 hover:text-emerald-600"
                        }`}
                      >
                        {m}
                      </button>
                    ))}
                  </div>
                </div>
              );
            })()}

            {/* Key input */}
            <div className="flex items-start gap-2">
              <div className="flex-1 min-w-0 relative">
                <input
                  type="password"
                  placeholder={currentProviderHasKey ? "已配置密钥 (可直接输入新密钥更换)" : "粘贴 API Key"}
                  value={inputApiKey}
                  onChange={(e) => {
                    // ⛔ 2026-08-14（任务二十二）：输入新 Key 即解除"已配置"态，
                    // 按钮切回"验证并保存"（旧实现死比较永假，换 Key 流程卡死）
                    if (currentProviderHasKey) setCurrentProviderHasKey(false);
                    setInputApiKey(e.target.value);
                  }}
                  className="flex-1 min-w-0 px-2 py-1.5 border border-zinc-200 rounded text-[12px] font-mono bg-white focus:outline-none focus:ring-1 focus:ring-emerald-500 w-full"
                />
              </div>
            </div>

            {/* Key status */}
            <div style={{display:"flex", alignItems:"center", gap:8, flexWrap:"wrap", fontSize:10}}>
              {(() => {
                const prov = apiProviders.find(p => p.id === selectedProvider);
                const hasKey = !!(currentProviderHasKey || (selectedProvider !== "custom" && prov?.keyConfigured));
                return (
                  <span style={{display:"inline-flex", alignItems:"center", gap:4, padding:"2px 8px", borderRadius:999, fontSize:10, fontWeight:700,
                    background: hasKey ? "#ecfdf5" : "#fafafa", color: hasKey ? "#047857" : "#9ca3af",
                    border: hasKey ? "1px solid #a7f3d0" : "1px solid #e5e7eb"}}>
                    {hasKey ? "✓ 已配置密钥" : "○ 未配置密钥"}
                  </span>
                );
              })()}
              {currentProviderKeyPreview && (
                <span style={{fontFamily:"monospace", fontSize:10, color:"#9ca3af"}}>{currentProviderKeyPreview}</span>
              )}
              {inputModelName && (
                <span style={{fontSize:10, color:"#9ca3af", marginLeft:"auto"}}>
                  当前选中模型: <code style={{fontFamily:"monospace", fontSize:10, color:"#4b5563"}}>{inputModelName}</code>
                </span>
              )}
            </div>

            {/* Custom provider input fields */}
            {selectedProvider === "custom" && (
              <div className="space-y-2 pt-1 border-t border-zinc-200">
                <input
                  type="text"
                  value={inputApiEndpoint}
                  onChange={(e) => { setInputApiEndpoint(e.target.value); setEndpointNote(""); }}
                  onBlur={(e) => {
                    const raw = e.target.value;
                    const clean = normalizeEndpoint(raw);
                    if (clean !== raw) {
                      setInputApiEndpoint(clean);
                      setEndpointNote(`已自动修正: ${raw} → ${clean}`);
                    }
                  }}
                  placeholder="Base URL，例: https://api.xiaomimimo.com/v1"
                  className="w-full px-2 py-1.5 border border-zinc-200 rounded text-[11px] font-mono bg-white focus:outline-none focus:ring-1 focus:ring-emerald-500"
                />
                {endpointNote && (
                  <div className="text-[10px] text-amber-600 mt-1">{endpointNote}</div>
                )}
                <input
                  type="text"
                  value={inputModelName}
                  onChange={(e) => setInputModelName(e.target.value)}
                  placeholder="模型名称 (例: gpt-4)"
                  className="w-full px-2 py-1.5 border border-zinc-200 rounded text-[11px] font-mono bg-white focus:outline-none focus:ring-1 focus:ring-emerald-500"
                />
              </div>
            )}

            {/* Verification feedback */}
            {verificationFeedback.type && (
              <div className={`p-3 rounded-lg border text-xs flex items-start gap-2 ${
                verificationFeedback.type === "success"
                  ? "bg-emerald-50 border-emerald-200 text-emerald-800"
                  : "bg-rose-50 border-rose-200 text-rose-800"
              }`}>
                <span className="font-extrabold shrink-0">{verificationFeedback.type === "success" ? "✓" : "✗"}</span>
                <p className="leading-relaxed">{verificationFeedback.text}</p>
              </div>
            )}

            {/* Save/Validate action button */}
            {(() => {
              const selectedProv = apiProviders.find(p => p.id === selectedProvider);
              const selectedHasKey = selectedProv?.keyConfigured || currentProviderHasKey;
              const selectedIsActive = selectedProvider === activeProviderId;

              if (selectedIsActive && selectedHasKey) {
                return (
                  <button disabled
                    className="w-full px-4 py-2 bg-emerald-50 border border-emerald-200 text-emerald-700 text-xs font-bold rounded-lg flex items-center justify-center gap-2 cursor-default"
                  >
                    <CheckCircle2 className="w-3.5 h-3.5" />
                    当前已生效
                  </button>
                );
              }

              if (selectedHasKey && activeProviderId) {
                return (
                  <button
                    onClick={() => handleSwitchProvider(selectedProvider)}
                    disabled={isSwitchingProvider}
                    className="w-full px-4 py-2 bg-amber-500 hover:bg-amber-600 disabled:bg-zinc-200 disabled:text-zinc-400 text-white text-xs font-bold rounded-lg transition-all flex items-center justify-center gap-2"
                  >
                    {isSwitchingProvider ? (
                      <><Loader2 className="w-3.5 h-3.5 animate-spin" /> 切换中...</>
                    ) : (
                      <><RefreshCw className="w-3.5 h-3.5" /> 切换至此模型</>
                    )}
                  </button>
                );
              }

              return (
                <button
                  onClick={handleSaveAndValidateKey}
                  disabled={isVerifyingKey || !inputApiKey.trim() || isValidationCoolingDown}
                  className="w-full px-4 py-2 bg-emerald-600 hover:bg-emerald-700 disabled:bg-zinc-200 disabled:text-zinc-400 text-white text-xs font-bold rounded-lg transition-all flex items-center justify-center gap-2"
                >
                  {isVerifyingKey ? <><Loader2 className="w-3.5 h-3.5 animate-spin" /> 校验中...</> : isValidationCoolingDown ? `冷却中 ${validationCooldownSeconds}秒` : "验证并保存配置"}
                </button>
              );
            })()}
          </div>

        </div>

      {/* 扫描路径管理（T25 前端接入） */}
      <div className="bg-white border border-zinc-200 rounded-xl p-5 space-y-4 text-left shadow-sm">
        <div className="border-b border-zinc-100 pb-3">
          <h3 className="text-zinc-850 font-bold text-xs uppercase tracking-widest flex items-center gap-1.5">
            <Settings className="w-4 h-4 text-emerald-600" />
            扫描路径管理
          </h3>
          <p className="text-[11px] text-zinc-400 mt-0.5">
            配置知识库扫描的文件夹路径。路径将写入 .env 的 SCAN_PATHS，扫描时按此列表索引文档。
          </p>
        </div>

        {/* 添加路径 */}
        <div className="flex items-center gap-2">
          <input
            value={newPathInput}
            onChange={(e) => setNewPathInput(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") handleAddScanPath(); }}
            placeholder="输入要扫描的文件夹路径"
            className="flex-1 px-3 py-2 bg-zinc-50 border border-zinc-200 rounded-lg text-xs focus:outline-none focus:border-emerald-500 font-mono"
          />
          <button
            onClick={handleAddScanPath}
            disabled={!newPathInput.trim()}
            className="px-4 py-2 bg-emerald-600 hover:bg-emerald-700 disabled:bg-zinc-200 text-white text-xs font-bold rounded-lg transition-all shrink-0"
          >
            添加路径
          </button>
        </div>

        {/* 路径列表 */}
        <div className="space-y-2">
          {scanPathsLoading ? (
            <p className="text-[11px] text-zinc-400">加载中...</p>
          ) : scanPaths.length === 0 ? (
            <p className="text-[11px] text-zinc-400">暂无扫描路径，可添加文件夹路径开始索引。</p>
          ) : (
            scanPaths.map((p, i) => (
              <div key={i} className="flex items-center justify-between gap-2 px-3 py-2 bg-zinc-50 border border-zinc-200 rounded-lg">
                <span className="text-[11px] text-zinc-700 font-mono truncate flex-1">{p}</span>
                <button
                  onClick={() => handleDeleteScanPath(i)}
                  className="px-2 py-1 text-[10px] text-rose-500 hover:bg-rose-50 rounded-md transition-all shrink-0"
                >
                  删除
                </button>
              </div>
            ))
          )}
        </div>

        {scanPathsMsg.text && (
          <p className={`text-[11px] ${scanPathsMsg.type === "error" ? "text-rose-500" : "text-emerald-600"}`}>
            {scanPathsMsg.text}
          </p>
        )}
      </div>

      {/* ⛔ 2026-08-14（任务二十七）：数据迁移——换电脑/换浏览器带走历史数据 */}
      <div className="bg-white border border-zinc-200 rounded-xl p-5 space-y-4 text-left shadow-sm">
        <div className="border-b border-zinc-100 pb-3">
          <h3 className="text-zinc-850 font-bold text-xs uppercase tracking-widest flex items-center gap-1.5">
            <Download className="w-4 h-4 text-emerald-600" />
            数据迁移
          </h3>
          <p className="text-[11px] text-zinc-400 mt-0.5">
            {/* ⛔ 2026-08-14（任务二十八）：本地数据会自动同步到服务端数据目录，手动导出/导入仅作离线兜底 */}
            标注/图谱/卡片/词条等会自动同步到服务端数据目录（随文件夹迁移）；此处可手动导出/导入，供离线迁移兜底。
          </p>
        </div>

        <div className="flex flex-col gap-2">
          <div className="flex justify-between items-center p-3 bg-zinc-50 border border-zinc-200 rounded-lg">
            <div className="max-w-xs">
              <span className="text-xs font-bold text-zinc-800 block">导出本地数据</span>
              <span className="text-[10px] text-zinc-400 leading-normal block mt-0.5">下载浏览器中保存的全部知识资产（不包含服务端文档/索引）。</span>
            </div>
            <button
              onClick={handleExportLocalData}
              className="px-3.5 py-1.5 bg-zinc-800 hover:bg-zinc-900 text-white text-xs font-bold rounded-lg flex items-center gap-1.5 shadow-sm transition-all"
            >
              <Download className="w-3.5 h-3.5" />
              导出
            </button>
          </div>

          <div className="flex justify-between items-center p-3 bg-zinc-50 border border-zinc-200 rounded-lg">
            <div className="max-w-xs">
              <span className="text-xs font-bold text-zinc-800 block">导入恢复本地数据</span>
              <span className="text-[10px] text-zinc-400 leading-normal block mt-0.5">选择导出的 JSON 恢复（覆盖当前浏览器数据），完成后请刷新页面。</span>
            </div>
            <label className="cursor-pointer px-3.5 py-1.5 bg-white border border-zinc-200 hover:bg-zinc-50 text-zinc-700 text-xs font-bold rounded-lg flex items-center gap-1.5 shadow-sm transition-all">
              <Upload className="w-3.5 h-3.5 text-zinc-500" />
              选择文件
              <input type="file" accept=".json" onChange={handleImportLocalData} className="hidden" />
            </label>
          </div>

          {/* ⛔ 2026-08-14（任务三十）：迁移指南随时可下载查看 */}
          <div className="flex justify-between items-center p-3 bg-zinc-50 border border-zinc-200 rounded-lg">
            <div className="max-w-xs">
              <span className="text-xs font-bold text-zinc-800 block">迁移操作指南</span>
              <span className="text-[10px] text-zinc-400 leading-normal block mt-0.5">下载《数据迁移指南》：整机复制文件夹与手动导出/导入的完整步骤。</span>
            </div>
            <button
              onClick={handleDownloadMigrationGuide}
              className="px-3.5 py-1.5 bg-white border border-zinc-200 hover:bg-zinc-50 text-zinc-700 text-xs font-bold rounded-lg flex items-center gap-1.5 shadow-sm transition-all"
            >
              <FileText className="w-3.5 h-3.5 text-zinc-500" />
              下载指南
            </button>
          </div>
        </div>

        <div className="p-3 bg-zinc-50 border border-zinc-200 rounded-lg">
          <div className="flex items-center gap-2 mb-1.5">
            <Server className="w-4 h-4 text-emerald-600" />
            <span className="text-xs font-bold text-zinc-800">换电脑还需迁移服务端数据</span>
          </div>
          <p className="text-[11px] text-zinc-500 leading-relaxed">
            服务端数据（文档源文件、索引、聊天记录、图谱概念卡）以及自动同步的标注/图谱/卡片/词条
            （user_state.json）都保存在运行服务的机器上。换电脑时：
            停止服务 → 复制 <code className="font-mono text-[10px] bg-white px-1 py-px rounded border border-zinc-200">chroma_db/</code>、
            <code className="font-mono text-[10px] bg-white px-1 py-px rounded border border-zinc-200">app/rag_app/data/</code>、
            <code className="font-mono text-[10px] bg-white px-1 py-px rounded border border-zinc-200">.env</code> 到新机器同位置 → 重新启动服务。
          </p>
        </div>
      </div>

    </div>
  );
}
