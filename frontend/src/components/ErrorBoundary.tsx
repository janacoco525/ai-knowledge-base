import React, { Component, ErrorInfo, ReactNode } from "react";
import { AlertTriangle, RefreshCw } from "lucide-react";

interface Props {
  children: ReactNode;
  fallbackTitle?: string;
}

interface State {
  hasError: boolean;
  error: Error | null;
  errorInfo: ErrorInfo | null;
}

export default class ErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false, error: null, errorInfo: null };

  static getDerivedStateFromError(error: Error): Partial<State> {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, errorInfo: ErrorInfo) {
    this.setState({ errorInfo });
    console.error("[ErrorBoundary] Caught:", error, errorInfo);
  }

  handleReset = () => {
    this.setState({ hasError: false, error: null, errorInfo: null });
  };

  handleHardRefresh = () => {
    // 清除浏览器缓存（service worker + localStorage 保留），强制重新拉取 index.html
    if ('serviceWorker' in navigator) {
      navigator.serviceWorker.getRegistrations().then(regs => {
        regs.forEach(r => r.unregister());
      });
    }
    // 跳转到同 URL 跳过缓存刷新
    window.location.reload();
  };

  isChunkError = (): boolean => {
    const msg = this.state.error?.message || "";
    return msg.includes("dynamically imported module") || 
           msg.includes("Loading chunk") ||
           msg.includes("Failed to fetch");
  };

  render() {
    if (this.state.hasError) {
      const isChunk = this.isChunkError();
      return (
        <div className="flex flex-col items-center justify-center h-full p-8 text-center bg-red-50/50">
          <AlertTriangle className="w-10 h-10 text-red-400 mb-3" />
          <h2 className="text-red-700 font-bold text-sm mb-1">
            {this.props.fallbackTitle || "组件渲染出错"}
          </h2>
          <p className="text-red-500 text-xs max-w-md mb-4 leading-relaxed">
            {this.state.error?.message || "未知错误"}
          </p>
          <div className="flex gap-2">
            <button
              onClick={this.handleReset}
              className="px-4 py-2 bg-red-600 hover:bg-red-700 text-white rounded-lg text-xs font-bold flex items-center gap-1.5 transition-colors cursor-pointer"
            >
              <RefreshCw className="w-3 h-3" />
              重试
            </button>
            {isChunk && (
              <button
                onClick={this.handleHardRefresh}
                className="px-4 py-2 bg-zinc-700 hover:bg-zinc-800 text-white rounded-lg text-xs font-bold transition-colors cursor-pointer"
                title="代码更新后浏览器缓存了旧 chunk 路径"
              >
                清除缓存刷新
              </button>
            )}
          </div>
          {this.state.errorInfo && (
            <details className="mt-4 text-left max-w-lg w-full">
              <summary className="text-[10px] text-red-400 cursor-pointer hover:text-red-600">
                查看错误详情
              </summary>
              <pre className="mt-2 p-3 bg-white border border-red-200 rounded text-[10px] text-red-600 overflow-auto max-h-48 font-mono">
                {this.state.error?.stack}
                {"\n\n"}
                {this.state.errorInfo.componentStack}
              </pre>
            </details>
          )}
        </div>
      );
    }
    return this.props.children;
  }
}
