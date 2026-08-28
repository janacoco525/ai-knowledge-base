import React, { useState, useRef, useEffect, useCallback, useMemo } from "react";
import { GraphNode, GraphEdge, NodeCategory } from "../types";
import { Plus, Trash2, Link2, Layers, RotateCcw, Sparkles, ZoomIn, ZoomOut } from "lucide-react";

// ===== 分类色 =====
const COLORS: Record<NodeCategory, { bg: string; stroke: string; hex: string }> = {
  person:        { bg: "#fee2e2", stroke: "#f87171", hex: "#e11d48" },
  event:         { bg: "#ffedd5", stroke: "#fb923c", hex: "#ea580c" },
  concept:       { bg: "#fef9c3", stroke: "#eab308", hex: "#ca8a04" },
  organization:  { bg: "#dcfce7", stroke: "#4ade80", hex: "#16a34a" },
  system:        { bg: "#dbeafe", stroke: "#60a5fa", hex: "#2563eb" },
  tool:          { bg: "#ccfbf1", stroke: "#2dd4bf", hex: "#0d9488" },
  process:       { bg: "#f3e8ff", stroke: "#a78bfa", hex: "#7c3aed" },
  location:      { bg: "#f3f4f6", stroke: "#9ca3af", hex: "#6b7280" },
};

const CAT_NAMES: Record<NodeCategory, string> = {
  person: "人物", event: "事件", concept: "概念", organization: "组织",
  system: "系统", tool: "工具", process: "流程", location: "地点",
};

interface GraphSession { id: string; docTitle: string; docId: string; timestamp: string; nodeCount: number; edgeCount: number; }

interface KnowledgeGraphProps {
  nodes: GraphNode[];
  edges: GraphEdge[];
  libraryId: string;
  onAddNode: (label: string, category: NodeCategory) => void;
  onDeleteNode: (id: string) => void;
  onAddEdge: (source: string, target: string, label: string) => void;
  onDeleteEdge: (id: string) => void;
  onSelectNode: (node: GraphNode | null) => void;
  deletedNodes?: GraphNode[];
  onRestoreNode?: (id: string) => void;
  graphSessions?: GraphSession[];
  onLoadSession?: (sessionId: string) => void;
  onDeleteSession?: (sessionId: string) => void;
}

export default function KnowledgeGraph({
  nodes, edges, libraryId,
  onAddNode, onDeleteNode, onAddEdge, onDeleteEdge, onSelectNode,
  deletedNodes = [], onRestoreNode,
  graphSessions = [], onLoadSession, onDeleteSession,
}: KnowledgeGraphProps) {
  const [nodeName, setNodeName] = useState("");
  const [nodeCategory, setNodeCategory] = useState<NodeCategory>("concept");
  const [hoveredNodeId, setHoveredNodeId] = useState<string | null>(null);
  const [isLinking, setIsLinking] = useState(false);
  const [linkSourceId, setLinkSourceId] = useState<string | null>(null);
  const [linkLabel, setLinkLabel] = useState("");
  const [draggedNodeId, setDraggedNodeId] = useState<string | null>(null);

  // SVG Zoom & Pan
  const svgRef = useRef<SVGSVGElement | null>(null);
  const [scale, setScale] = useState(1);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const [isPanning, setIsPanning] = useState(false);
  const [panStart, setPanStart] = useState({ x: 0, y: 0 });

  // Node positions
  const [positions, setPositions] = useState<Record<string, { x: number; y: number }>>({});
  const [showClearConfirm, setShowClearConfirm] = useState(false);  // 通用型修复：避开 window.confirm 被 iframe/嵌入拦截

  // 计算每个节点的度（连接数），用于决定节点大小和所处环
  const nodeDegree = useMemo(() => {
    const deg: Record<string, number> = {};
    nodes.forEach(n => { deg[n.id] = 0; });
    edges.forEach(e => {
      deg[e.source] = (deg[e.source] || 0) + 1;
      deg[e.target] = (deg[e.target] || 0) + 1;
    });
    return deg;
  }, [nodes, edges]);

  // ===== 辐射式布局：以中心节点为圆心，按 BFS 距离分圈散开 =====
  // 思维状态：核心 → 直接关联 → 间接关联 → 外围，从一个点发散出去
  const computeLayout = useCallback(() => {
    if (nodes.length === 0) { setPositions({}); return; }
    const CENTER = { x: 300, y: 250 };
    const BASE_RADIUS = 100;
    const NODE_SPACING = 55;

    const deg: Record<string, number> = {};
    nodes.forEach(n => { deg[n.id] = 0; });
    edges.forEach(e => {
      deg[e.source] = (deg[e.source] || 0) + 1;
      deg[e.target] = (deg[e.target] || 0) + 1;
    });
    const centerNode = nodes.reduce((a, b) => (deg[a.id] || 0) >= (deg[b.id] || 0) ? a : b);
    const centerId = centerNode.id;

    const ring: Record<string, number> = {};
    ring[centerId] = 0;
    const queue: string[] = [centerId];
    const visited = new Set<string>([centerId]);
    while (queue.length > 0) {
      const cur = queue.shift()!;
      const neighbors = edges
        .filter(e => e.source === cur || e.target === cur)
        .map(e => e.source === cur ? e.target : e.source);
      for (const nb of neighbors) {
        if (!visited.has(nb)) {
          visited.add(nb);
          ring[nb] = (ring[cur] || 0) + 1;
          queue.push(nb);
        }
      }
    }
    nodes.forEach(n => { if (ring[n.id] === undefined) ring[n.id] = 3; });

    const ringBuckets: Record<number, string[]> = {};
    nodes.forEach(n => {
      const r = ring[n.id];
      (ringBuckets[r] = ringBuckets[r] || []).push(n.id);
    });

    const pos: Record<string, { x: number; y: number }> = {};
    Object.keys(ringBuckets).forEach(rk => {
      const r = parseInt(rk);
      const ids = ringBuckets[r];
      const circumference = ids.length * NODE_SPACING;
      const minRadius = circumference / (2 * Math.PI);
      const radius = r === 0 ? 0 : Math.max(BASE_RADIUS + (r - 1) * 75, minRadius);
      ids.forEach((id, i) => {
        if (r === 0) {
          pos[id] = { x: CENTER.x, y: CENTER.y };
        } else {
          const angle = (i / ids.length) * Math.PI * 2 - Math.PI / 2;
          pos[id] = {
            x: CENTER.x + Math.cos(angle) * radius,
            y: CENTER.y + Math.sin(angle) * radius,
          };
        }
      });
    });

    setPositions(pos);
  }, [nodes, edges]);

  useEffect(() => {
    computeLayout();
  }, [computeLayout]);

  // 动态计算 viewBox 以适应所有节点
  const viewBox = useMemo(() => {
    const posArr = Object.values(positions);
    if (posArr.length === 0) return "0 0 600 500";
    const padding = 80;
    const xs = posArr.map(p => p.x);
    const ys = posArr.map(p => p.y);
    const minX = Math.min(...xs) - padding;
    const minY = Math.min(...ys) - padding;
    const maxX = Math.max(...xs) + padding;
    const maxY = Math.max(...ys) + padding;
    return `${minX} ${minY} ${maxX - minX} ${maxY - minY}`;
  }, [positions]);

  // Pan / Drag handlers
  const handleSvgMouseDown = (e: React.MouseEvent) => {
    if (draggedNodeId) return;
    setIsPanning(true);
    setPanStart({ x: e.clientX - pan.x, y: e.clientY - pan.y });
  };

  const handleMouseMove = (e: React.MouseEvent) => {
    if (!svgRef.current) return;
    const rect = svgRef.current.getBoundingClientRect();
    const vb = viewBox.split(' ').map(Number);
    if (draggedNodeId) {
      const x = ((e.clientX - rect.left) / rect.width * vb[2] + vb[0] - pan.x) / scale;
      const y = ((e.clientY - rect.top) / rect.height * vb[3] + vb[1] - pan.y) / scale;
      setPositions(p => ({ ...p, [draggedNodeId]: { x, y } }));
    } else if (isPanning) {
      setPan({ x: e.clientX - panStart.x, y: e.clientY - panStart.y });
    }
  };

  const handleMouseUp = () => {
    setDraggedNodeId(null);
    setIsPanning(false);
  };

  useEffect(() => {
    if (draggedNodeId || isPanning) window.addEventListener("mouseup", handleMouseUp);
    return () => window.removeEventListener("mouseup", handleMouseUp);
  }, [draggedNodeId, isPanning]);

  // Wheel zoom
  useEffect(() => {
    const el = svgRef.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const rect = el.getBoundingClientRect();
      const mx = (e.clientX - rect.left) / rect.width * 600;
      const my = (e.clientY - rect.top) / rect.height * 500;
      const f = Math.exp(-e.deltaY * 0.001);
      const ns = Math.max(0.2, Math.min(5, scale * f));
      setPan(prev => ({ x: mx - (mx - prev.x) * (ns / scale), y: my - (my - prev.y) * (ns / scale) }));
      setScale(ns);
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, [scale]);

  const handleNodeClick = (node: GraphNode) => {
    onSelectNode(node);
    if (isLinking) {
      if (!linkSourceId) setLinkSourceId(node.id);
      else if (linkSourceId !== node.id) {
        onAddEdge(linkSourceId, node.id, linkLabel.trim() || "关联");
        setIsLinking(false); setLinkSourceId(null); setLinkLabel("");
      }
    }
  };

  const handleSubmitNode = (e: React.FormEvent) => {
    e.preventDefault();
    if (!nodeName.trim()) return;
    onAddNode(nodeName.trim(), nodeCategory);
    setNodeName("");
  };

  return (
    <div className="flex flex-col bg-white border border-zinc-200 rounded-xl overflow-hidden shadow-sm">
      {/* Linking banner */}
      {isLinking && (
        <div className="bg-emerald-50 border-b border-emerald-200 px-4 py-2 text-xs text-emerald-800 flex items-center gap-3 select-none">
          <span className="w-2 h-2 rounded-full bg-emerald-500" />
          {!linkSourceId ? "点击起点节点 →" : "现在点击目标节点完成连线 →"}
          <input type="text" placeholder="如：作用于/衍生自" value={linkLabel}
            onChange={e => setLinkLabel(e.target.value)}
            className="px-2 py-0.5 bg-white border border-emerald-300 rounded text-[11px] w-32" />
          <button onClick={() => { setIsLinking(false); setLinkSourceId(null); }}
            className="text-[10px] text-zinc-400 hover:text-zinc-600">取消</button>
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-4 flex-1 min-h-0">
        {/* Canvas */}
        <div className="col-span-3 relative bg-[#fafbfc] overflow-hidden" style={{ minHeight: "calc(100vh - 160px)" }}>
          {/* Legend —— 用和节点同款"浅底+深描边"风格，圆圈样式直接对照 */}
          <div className="absolute top-2 left-2 bg-white/90 backdrop-blur border border-zinc-200 px-3 py-2 rounded-lg z-10 text-[10px] select-none flex items-center gap-x-3 gap-y-1.5 flex-wrap max-w-[60%]">
            <span className="text-zinc-500 font-bold pr-2 border-r border-zinc-200">图例</span>
            {Object.entries(CAT_NAMES).map(([k, v]) => {
              const c = COLORS[k as NodeCategory];
              return (
                <div key={k} className="flex items-center gap-1.5">
                  <svg width="12" height="12" className="shrink-0">
                    <circle cx="6" cy="6" r="5" fill={c.bg} stroke={c.hex} strokeWidth="1.5" />
                  </svg>
                  <span className="text-zinc-700 font-medium">{v}</span>
                </div>
              );
            })}
          </div>

          {/* Zoom controls */}
          <div className="absolute bottom-3 left-3 flex flex-col gap-1 z-10">
            <button onClick={() => { const ns = Math.min(5, scale * 1.3); setPan(p => ({ x: 250 - (250 - p.x) * ns / scale, y: 200 - (200 - p.y) * ns / scale })); setScale(ns); }}
              className="w-7 h-7 bg-white border rounded shadow-sm flex items-center justify-center hover:bg-zinc-50"><ZoomIn className="w-3.5 h-3.5" /></button>
            <button onClick={() => { const ns = Math.max(0.2, scale / 1.3); setPan(p => ({ x: 250 - (250 - p.x) * ns / scale, y: 200 - (200 - p.y) * ns / scale })); setScale(ns); }}
              className="w-7 h-7 bg-white border rounded shadow-sm flex items-center justify-center hover:bg-zinc-50"><ZoomOut className="w-3.5 h-3.5" /></button>
            <button onClick={() => { setScale(1); setPan({ x: 0, y: 0 }); }}
              className="w-7 h-7 bg-white border rounded shadow-sm flex items-center justify-center hover:bg-zinc-50 mt-1"><RotateCcw className="w-3.5 h-3.5" /></button>
          </div>

          {/* SVG Canvas —— viewBox 扩大以容纳辐射布局的外圈 */}
          <svg ref={svgRef} viewBox={viewBox} className="w-full h-full select-none outline-none cursor-grab active:cursor-grabbing"
            onMouseMove={handleMouseMove} onMouseDown={handleSvgMouseDown}>
              <defs>
              <marker id="arrowhead" viewBox="0 0 10 10" refX="11" refY="5" markerWidth="3.5" markerHeight="3.5" orient="auto-start-reverse">
                <path d="M0 0 L10 5 L0 10 z" fill="#cbd5e1" />
              </marker>
              <marker id="arrowhead-hl" viewBox="0 0 10 10" refX="11" refY="5" markerWidth="4" markerHeight="4" orient="auto-start-reverse">
                <path d="M0 0 L10 5 L0 10 z" fill="#0d9488" />
              </marker>
              <filter id="node-shadow">
                <feDropShadow dx="0" dy="1" stdDeviation="1.5" floodOpacity="0.12" />
              </filter>
            </defs>
            <rect width="100%" height="100%" fill="url(#dot-grid)" />

            <g transform={`translate(${pan.x},${pan.y}) scale(${scale})`}>
              {/* Edges */}
              {edges.map(e => {
                const sp = positions[e.source];
                const tp = positions[e.target];
                if (!sp || !tp) return null;
                const hl = hoveredNodeId === e.source || hoveredNodeId === e.target;
                const label = e.label || "";
                // 边标签宽度自适应：中文 10px/字，其他 6px/字，最小 30 最大 90
                const cnCount = (label.match(/[\u4e00-\u9fff]/g) || []).length;
                const otherCount = label.length - cnCount;
                const edgeLabelWidth = label ? Math.max(30, Math.min(90, cnCount * 10 + otherCount * 6 + 10)) : 0;
                // 贝塞尔曲线控制点：中点向法线方向偏移，弯曲程度固定，避免直线交叉
                const mx = (sp.x + tp.x) / 2;
                const my = (sp.y + tp.y) / 2;
                const dx = tp.x - sp.x, dy = tp.y - sp.y;
                const len = Math.sqrt(dx * dx + dy * dy) || 1;
                // 法线方向偏移（固定 18，方向由 source->target 决定，保证双向边不重叠）
                const curveOffset = 18;
                const cx = mx + (-dy / len) * curveOffset;
                const cy = my + (dx / len) * curveOffset;
                const pathD = `M ${sp.x} ${sp.y} Q ${cx} ${cy} ${tp.x} ${tp.y}`;

                return (
                  <g key={e.id} style={{ opacity: hl ? 1 : 0.5 }}>
                    {/* Invisible hit area */}
                    <path d={pathD} fill="none" stroke="transparent" strokeWidth="16"
                      onMouseEnter={() => setHoveredNodeId(e.source)}
                      onMouseLeave={() => setHoveredNodeId(null)} />
                    {/* Static line */}
                    <path d={pathD} fill="none"
                      stroke={hl ? "#0d9488" : "#cbd5e1"} strokeWidth={hl ? "3" : "1.5"}
                      markerEnd={hl ? "url(#arrowhead-hl)" : "url(#arrowhead)"} />
                    {/* Edge label — only show on hover */}
                    {label && hl && (
                      <g transform={`translate(${cx},${cy})`}>
                        <rect x={-edgeLabelWidth / 2} y={-10} width={edgeLabelWidth} height={20}
                          rx="4" fill="white" stroke="#0d9488" strokeWidth="1" opacity="0.95" />
                        <text textAnchor="middle" dy="4" fontSize="10" fontWeight="700"
                          fill="#0d9488" className="pointer-events-none select-none">
                          {label}
                        </text>
                      </g>
                    )}
                  </g>
                );
              })}

              {/* Nodes */}
              {nodes.map(n => {
                const pos = positions[n.id] || { x: 250, y: 200 };
                const c = COLORS[n.category] || COLORS.concept;
                const hl = hoveredNodeId === n.id;
                const label = n.label || "未命名";
                // ⛔ 2026-08-13：节点大小优先用提取端 god score（weight），
                // 图内连接数（度）只作兜底——否则"可信度加权"这类全书核心概念
                // 只要在当前子图连接少，就和其他节点一样大，关键点无法突出。
                const weight = typeof n.weight === "number" ? n.weight : 0;
                const deg = nodeDegree[n.id] || 0;
                // weight 0.4~1.0 → 半径 10~24；无 weight 时退回度驱动（度0=10, 度5+=22）
                const nodeRadius = weight > 0
                  ? Math.max(10, Math.min(24, 10 + weight * 14))
                  : Math.max(10, Math.min(22, 10 + deg * 2.2));
                const isCore = weight >= 0.6 || (weight === 0 && deg >= 3);
                // 按字符数估算文字框宽度：中文 11px/字，英文/数字 6.5px/字，最小 44 最大 160
                const cnCount = (label.match(/[\u4e00-\u9fff]/g) || []).length;
                const otherCount = label.length - cnCount;
                const textWidth = Math.max(44, Math.min(160, cnCount * 11 + otherCount * 6.5 + 12));
                // 标签贴在节点右侧，不再压到底部（避免被穿过节点的边遮挡）
                const labelOffsetX = nodeRadius + 4;
                const fontSize = isCore ? 10.5 : 9.5;

                return (
                  <g key={n.id} transform={`translate(${pos.x},${pos.y})`}
                    className="cursor-pointer group"
                    onMouseDown={e => { e.preventDefault(); e.stopPropagation(); setDraggedNodeId(n.id); }}
                    onClick={() => handleNodeClick(n)}
                    onMouseEnter={() => setHoveredNodeId(n.id)}
                    onMouseLeave={() => setHoveredNodeId(null)}>
                    {/* 核心节点外层光晕：1px 偏移的同色淡圈，营造「被强调」感 */}
                    {isCore && (
                      <circle cx="0" cy="0" r={nodeRadius + 4} fill="none"
                        stroke={c.hex} strokeWidth="1" opacity={hl ? 0.5 : 0.25}
                        className="transition-all duration-200"
                        style={{ transform: hl ? 'scale(1.08)' : 'scale(1)' }} />
                    )}
                    {/* Hover ring */}
                    <circle cx="0" cy="0" r={nodeRadius + (hl ? 5 : 3)}
                      fill="none" stroke={hl ? "#0d9488" : "transparent"} strokeWidth="1.5"
                      className="transition-all duration-200" opacity={hl ? 0.6 : 0} />
                    {/* Node circle —— 核心节点用 2.5px 实色描边 + 浅色填充；普通节点细描边 */}
                    <circle cx="0" cy="0" r={nodeRadius} fill={c.bg}
                      stroke={c.hex}
                      strokeWidth={isCore ? 2.5 : 1.5}
                      filter="url(#node-shadow)" className="transition-all duration-200"
                      style={{ transform: hl ? 'scale(1.12)' : 'scale(1)' }} />
                    {/* 节点圆内的分类小点（顶部 12 点钟位置）—— 一眼看出分类 */}
                    <circle cx="0" cy={-nodeRadius + 3} r="1.8" fill={c.hex} opacity="0.7"
                      className="pointer-events-none" />
                    {/* Text label —— 移到节点右侧 4px，连线穿过节点时不会再压住文字 */}
                    <g transform={`translate(${labelOffsetX}, 0)`}>
                      <text textAnchor="start" dy="3.5"
                        fontSize={fontSize} fontWeight={isCore ? 700 : 600}
                        fill={hl ? "#0d9488" : (isCore ? c.hex : "#1e293b")}
                        className="pointer-events-none select-none"
                        style={{ paintOrder: "stroke", stroke: "white", strokeWidth: 3, strokeLinejoin: "round" }}>
                        {label}
                      </text>
                    </g>
                  </g>
                );
              })}
            </g>
          </svg>

          {/* Empty state */}
          {nodes.length === 0 && (
            <div className="absolute inset-0 flex flex-col items-center justify-center pointer-events-none select-none gap-3">
              <div className="w-16 h-16 rounded-full bg-zinc-100 flex items-center justify-center border-2 border-dashed border-zinc-200">
                <span className="text-2xl text-zinc-300">🕸</span>
              </div>
              <p className="text-sm font-bold text-zinc-500">暂无知识图谱</p>
              <p className="text-xs text-zinc-400">从右侧勾选文档 → 点击"生成图谱"</p>
            </div>
          )}
        </div>

        {/* Side panel */}
        <div className="p-4 flex flex-col bg-zinc-50/20 select-none overflow-y-auto">
          <h4 className="text-xs font-bold text-zinc-700 uppercase tracking-widest mb-3">添加实体</h4>
          <form onSubmit={handleSubmitNode} className="space-y-3">
            <input type="text" placeholder="实体名称" value={nodeName}
              onChange={e => setNodeName(e.target.value)}
              className="w-full px-3 py-1.5 bg-white border border-zinc-200 text-xs rounded-lg" />
            <select value={nodeCategory} onChange={e => setNodeCategory(e.target.value as NodeCategory)}
              className="w-full px-2.5 py-1.5 bg-white border border-zinc-200 text-xs rounded-lg">
              {Object.entries(CAT_NAMES).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </select>
            <button type="submit"
              className="w-full py-1.5 bg-zinc-800 hover:bg-zinc-900 text-white text-xs font-bold rounded-lg flex items-center justify-center gap-1">
              <Plus className="w-3 h-3" />添加节点</button>
          </form>

          <button onClick={() => { setIsLinking(!isLinking); setLinkSourceId(null); setLinkLabel(""); }}
            className={`w-full mt-3 py-1.5 rounded-lg text-xs font-bold transition-all flex items-center justify-center gap-1 ${
              isLinking ? "bg-emerald-500 text-white" : "bg-white border border-zinc-200 text-zinc-600 hover:bg-zinc-50"}`}>
            <Link2 className="w-3 h-3" />{isLinking ? "取消连线" : "构建连线"}</button>

          {/* Sessions */}
          <div className="mt-5 pt-4 border-t border-zinc-200">
            <h5 className="text-[10px] text-zinc-400 font-bold uppercase tracking-wider mb-2 flex items-center justify-between">
              <span>生成历史</span>
              {graphSessions.length > 0 && !showClearConfirm && (
                <button
                  onClick={() => setShowClearConfirm(true)}
                  className="text-[9px] text-zinc-300 hover:text-rose-500 normal-case font-normal cursor-pointer"
                  title="清空全部生成历史"
                >清空</button>
              )}
              {showClearConfirm && (
                <div className="flex items-center gap-1">
                  <button
                    onClick={() => {
                      // ⛔ 2026-08-19：改为真全清（原 slice(1) 保留最新一条，用户误以为"清不掉"）；
                      // 备份由 App 的 useEffect 同步清理，杜绝重启复活。
                      graphSessions.forEach(s => onDeleteSession?.(s.id));
                      setShowClearConfirm(false);
                    }}
                    className="text-[9px] text-rose-500 hover:text-rose-700 font-bold cursor-pointer px-1 py-0.5 bg-rose-50 rounded"
                  >确定</button>
                  <button
                    onClick={() => setShowClearConfirm(false)}
                    className="text-[9px] text-zinc-400 hover:text-zinc-600 font-bold cursor-pointer px-1 py-0.5 bg-zinc-100 rounded"
                  >取消</button>
                </div>
              )}
            </h5>
            {graphSessions.length > 0 ? (
              <div className="space-y-1 max-h-28 overflow-y-auto">
                {graphSessions.slice(0, 8).map(s => (
                  <div key={s.id} className="flex items-center justify-between p-1.5 bg-zinc-50 border border-zinc-200 rounded text-[10px] group hover:bg-white">
                    <button onClick={() => onLoadSession?.(s.id)} className="flex-1 text-left min-w-0 cursor-pointer">
                      <div className="truncate font-medium text-zinc-700">{s.docTitle}</div>
                      <div className="text-[9px] text-zinc-400">
                        {s.nodeCount}节点·{s.edgeCount}连线
                        {s.timestamp ? ` · ${s.timestamp.slice(5, 16)}` : ""}
                      </div>
                    </button>
                    <button onClick={() => onDeleteSession?.(s.id)} className="text-zinc-300 hover:text-rose-500 p-0.5 opacity-0 group-hover:opacity-100">✕</button>
                  </div>
                ))}
              </div>
            ) : <p className="text-[9px] text-zinc-400 italic">生成图谱后自动保存</p>}
          </div>

          <div className="pt-3 mt-3 border-t border-zinc-200 text-xs text-zinc-500 space-y-1">
            <div className="flex justify-between"><span>节点:</span><span className="font-bold">{nodes.length}</span></div>
            <div className="flex justify-between"><span>连线:</span><span className="font-bold">{edges.length}</span></div>
          </div>
        </div>
      </div>
    </div>
  );
}
