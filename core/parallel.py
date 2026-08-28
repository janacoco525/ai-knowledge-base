#!/usr/bin/env python3
"""
Parallel Execution Utilities — Fan-out/Fan-in 并行模式
参考《Graph Engineering》§6 菱形：拆分 -> 并行 -> 合并

提供：
- parallel_map: 并行映射（带超时、重试、进度回调）
- FanOutFanIn: 显式扇出扇入编排器
- TokenBucketRateLimiter: 并发速率限制（保护 LLM API）
"""

import concurrent.futures
import threading
import time
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Callable, List, Dict, Any, Optional, Iterator, TypeVar, Generic
from functools import wraps

logger = logging.getLogger("ai_kb.parallel")

T = TypeVar("T")
R = TypeVar("R")


@dataclass
class ParallelResult(Generic[R]):
    """并行执行结果"""
    results: List[R] = field(default_factory=list)
    errors: List[tuple[int, Exception]] = field(default_factory=list)
    durations: List[float] = field(default_factory=list)
    total_duration: float = 0.0

    @property
    def success_count(self) -> int:
        return len(self.results)

    @property
    def error_count(self) -> int:
        return len(self.errors)

    @property
    def is_partial_failure(self) -> bool:
        return self.error_count > 0 and self.success_count > 0


class TokenBucketRateLimiter:
    """令牌桶限流器：保护 LLM API 等外部服务"""

    def __init__(self, rate: float, burst: int = 1):
        """
        Args:
            rate: 每秒允许的请求数（如 10 req/s）
            burst: 桶容量（突发允许）
        """
        self.rate = rate
        self.burst = burst
        self._tokens = float(burst)
        self._last_update = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self, tokens: int = 1, timeout: Optional[float] = None) -> bool:
        """获取令牌，阻塞直到可用或超时"""
        deadline = time.monotonic() + timeout if timeout else None
        while True:
            with self._lock:
                now = time.monotonic()
                elapsed = now - self._last_update
                self._tokens = min(self.burst, self._tokens + elapsed * self.rate)
                if self._tokens >= tokens:
                    self._tokens -= tokens
                    self._last_update = now
                    return True
                wait = (tokens - self._tokens) / self.rate
            if deadline and time.monotonic() + wait > deadline:
                return False
            time.sleep(min(wait, 0.1))

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *args):
        pass


def parallel_map(
    func: Callable[[T], R],
    items: List[T],
    max_workers: int = 4,
    timeout: Optional[float] = None,
    rate_limiter: Optional[TokenBucketRateLimiter] = None,
    on_progress: Optional[Callable[[int, int], None]] = None,
    preserve_order: bool = True,
) -> ParallelResult[R]:
    """
    并行映射：将 func 应用到每个 item，返回聚合结果

    Args:
        func: 处理函数，接收单个 item 返回结果
        items: 输入列表
        max_workers: 最大并发数
        timeout: 单任务超时（秒）
        rate_limiter: 可选限流器
        on_progress: 进度回调 (completed, total)
        preserve_order: 是否保持结果顺序与输入一致

    Returns:
        ParallelResult: 包含 results, errors, durations
    """
    if not items:
        return ParallelResult()

    start_time = time.monotonic()
    results: Dict[int, R] = {}
    errors: List[tuple[int, Exception]] = []
    durations: List[float] = [0.0] * len(items)
    completed = 0
    lock = threading.Lock()

    def _worker(index: int, item: T) -> tuple[int, Optional[R], Optional[Exception], float]:
        task_start = time.monotonic()
        try:
            if rate_limiter:
                rate_limiter.acquire()
            result = func(item)
            return index, result, None, time.monotonic() - task_start
        except Exception as e:
            return index, None, e, time.monotonic() - task_start

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_index = {
            executor.submit(_worker, i, item): i
            for i, item in enumerate(items)
        }

        for future in as_completed(future_to_index):
            index = future_to_index[future]
            try:
                idx, result, error, duration = future.result(timeout=timeout)
            except concurrent.futures.TimeoutError:
                with lock:
                    errors.append((index, TimeoutError(f"Task {index} timeout after {timeout}s")))
                    durations[index] = timeout or 0
                continue

            with lock:
                if error:
                    errors.append((index, error))
                else:
                    results[index] = result
                durations[idx] = duration
                completed += 1
                if on_progress:
                    on_progress(completed, len(items))

    ordered_results = [results[i] for i in range(len(items)) if i in results]
    total_duration = time.monotonic() - start_time

    return ParallelResult(
        results=ordered_results if preserve_order else list(results.values()),
        errors=errors,
        durations=[durations[i] for i in range(len(items)) if i in results or i < len(errors)],
        total_duration=total_duration,
    )


def parallelize(max_workers: int = 4, timeout: Optional[float] = None):
    """装饰器：将处理单个 item 的函数变为批量并行处理"""
    def decorator(func: Callable[[T], R]) -> Callable[[List[T]], ParallelResult[R]]:
        @wraps(func)
        def wrapper(items: List[T], **kwargs) -> ParallelResult[R]:
            return parallel_map(func, items, max_workers=max_workers, timeout=timeout, **kwargs)
        return wrapper
    return decorator


@dataclass
class FanOutFanInTask(Generic[T, R]):
    """扇出扇入任务定义"""
    partition_key: str
    items: List[T]
    process_fn: Callable[[List[T]], List[R]]
    weight: int = 1


class FanOutFanIn(Generic[T, R]):
    """
    显式扇出扇入编排器

    用法：
        executor = FanOutFanIn[str, Dict]()
        executor.add_partition("domain_1", items_1, process_fn)
        executor.add_partition("domain_2", items_2, process_fn)
        result = executor.execute(max_workers=4)
        merged = result.merge(key_fn=lambda x: x["id"])
    """

    def __init__(self, max_workers: int = 4, rate_limiter: Optional[TokenBucketRateLimiter] = None):
        self.max_workers = max_workers
        self.rate_limiter = rate_limiter
        self.partitions: List[FanOutFanInTask] = []
        self._results: Dict[str, ParallelResult] = {}

    def add_partition(
        self,
        partition_key: str,
        items: List[T],
        process_fn: Callable[[List[T]], List[R]],
        weight: int = 1,
    ) -> "FanOutFanIn":
        self.partitions.append(FanOutFanInTask(
            partition_key=partition_key,
            items=items,
            process_fn=process_fn,
            weight=weight,
        ))
        return self

    def execute(
        self,
        on_partition_progress: Optional[Callable[[str, int, int], None]] = None,
        on_partition_complete: Optional[Callable[[str, ParallelResult], None]] = None,
    ) -> "FanOutFanInResult[R]":
        self._results = {}

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {}
            for partition in self.partitions:
                if not partition.items:
                    continue

                def run_partition(p=partition):
                    return p.partition_key, parallel_map(
                        lambda batch: p.process_fn(batch),
                        [p.items],
                        max_workers=1,
                        rate_limiter=self.rate_limiter,
                        on_progress=lambda c, t: on_partition_progress(p.partition_key, c, t) if on_partition_progress else None,
                    )

                futures[executor.submit(run_partition)] = partition.partition_key

            for future in as_completed(futures):
                partition_key = futures[future]
                try:
                    key, result = future.result()
                    self._results[key] = result
                    if on_partition_complete:
                        on_partition_complete(key, result)
                except Exception as e:
                    logger.error(f"Partition {partition_key} failed: {e}")
                    self._results[partition_key] = ParallelResult(errors=[(-1, e)])

        return FanOutFanInResult(self._results)


@dataclass
class FanOutFanInResult(Generic[R]):
    """扇出扇入执行结果"""
    partition_results: Dict[str, ParallelResult]

    def merge(self, key_fn: Callable[[R], str], dedup: bool = True) -> List[R]:
        """Fan-in: 合并所有分区结果，按 key 去重"""
        merged: Dict[str, R] = {}
        for partition_key, presult in self.partition_results.items():
            for item in presult.results:
                items = item if isinstance(item, list) else [item]
                for sub_item in items:
                    k = key_fn(sub_item)
                    if dedup and k in merged:
                        continue
                    merged[k] = sub_item
        return list(merged.values())

    def merge_concat(self) -> List[R]:
        """Fan-in: 简单拼接所有结果（不去重）"""
        all_results = []
        for presult in self.partition_results.values():
            all_results.extend(presult.results)
        return all_results

    def get_errors(self) -> List[tuple[str, Exception]]:
        errors = []
        for partition_key, presult in self.partition_results.items():
            for idx, err in presult.errors:
                errors.append((partition_key, err))
        return errors

    def summary(self) -> Dict[str, Any]:
        total_success = sum(p.success_count for p in self.partition_results.values())
        total_errors = sum(p.error_count for p in self.partition_results.values())
        return {
            "partitions": len(self.partition_results),
            "total_success": total_success,
            "total_errors": total_errors,
            "partition_details": {
                k: {"success": v.success_count, "errors": v.error_count}
                for k, v in self.partition_results.items()
            },
        }


# ========== 便捷函数 ==========

def parallel_scan_files(
    scanner,
    files: List,
    max_workers: int = 4,
    on_progress: Optional[Callable[[int, int], None]] = None,
) -> ParallelResult:
    """并行扫描文件列表（每个文件独立入库）
    （2026-08-20 结构化：身份统一为 source_root=父目录, rel_path=文件名，
    不再用绝对路径当 physical_name，杜绝身份格式混存）"""
    def _scan_one(file_path):
        return scanner.kb.index_file_with_metadata(
            file_path=str(file_path),
            file_name=file_path.name,
            uploaded_at=time.strftime("%Y-%m-%d %H:%M:%S"),
            file_size=file_path.stat().st_size,
            domain=file_path.parent.name,
            physical_name=file_path.name,
            file_mtime=file_path.stat().st_mtime,
            source_root=str(file_path.parent.resolve()),
            rel_path=file_path.name,
            rel_dir=".",
        )
    return parallel_map(_scan_one, files, max_workers=max_workers, on_progress=on_progress)


def parallel_graph_generation(
    domains: List[str],
    generate_fn: Callable[[str], Dict],
    max_workers: int = 3,
    rate_limiter: Optional[TokenBucketRateLimiter] = None,
    on_progress: Optional[Callable[[int, int], None]] = None,
) -> ParallelResult:
    """并行生成多个域的图谱"""
    return parallel_map(
        generate_fn,
        domains,
        max_workers=max_workers,
        rate_limiter=rate_limiter,
        on_progress=on_progress,
    )


# 已删除（2026-08-06）：parallel_llm_extraction —— 内部二次分批与调用方预分批叠加会产生双层嵌套，
# 曾导致图谱 LLM 提取 7 批全灭并静默降级；调用方应预分批后直接用 parallel_map


if __name__ == "__main__":
    import random

    def slow_square(x):
        time.sleep(random.uniform(0.1, 0.3))
        return x * x

    result = parallel_map(slow_square, list(range(10)), max_workers=4, on_progress=lambda c, t: print(f"Progress: {c}/{t}"))
    print(f"Results: {result.results}")
    print(f"Duration: {result.total_duration:.2f}s")

    def process_domain(domain_items):
        return [{"domain": f"domain_{i}", "value": x * 2} for i, x in enumerate(domain_items)]

    executor = FanOutFanIn[int, Dict](max_workers=3)
    executor.add_partition("A", list(range(5)), process_domain)
    executor.add_partition("B", list(range(5, 10)), process_domain)
    executor.add_partition("C", list(range(10, 15)), process_domain)

    result = executor.execute(
        on_partition_progress=lambda k, c, t: print(f"  {k}: {c}/1"),
        on_partition_complete=lambda k, r: print(f"  {k} done: {r.success_count}"),
    )
    merged = result.merge(key_fn=lambda x: str(x["value"]))
    print(f"Merged: {len(merged)} items")
    print(f"Summary: {result.summary()}")