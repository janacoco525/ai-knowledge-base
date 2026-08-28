import { lazy, type ComponentType } from "react";

/**
 * 旧 chunk 保护：前端重新构建后，浏览器缓存的旧 JS 会引用已不存在的
 * chunk 文件名（如 GraphPanel-BcV-k17fjs），dynamic import 直接失败。
 * 此包装捕获该失败并自动 reload 一次拉取最新版本；10 秒内重复失败则照抛，
 * 防止无限刷新循环。
 */
export function lazyRetry<T extends ComponentType<unknown>>(
  factory: () => Promise<{ default: T }>
) {
  return lazy(() =>
    factory().catch((err) => {
      const key = "kb_lazy_reload_at";
      const now = Date.now();
      const last = Number(sessionStorage.getItem(key) || 0);
      if (now - last > 10000) {
        sessionStorage.setItem(key, String(now));
        window.location.reload();
        // reload 期间挂起，避免渲染错误组件
        return new Promise<{ default: T }>(() => {});
      }
      throw err;
    })
  );
}
