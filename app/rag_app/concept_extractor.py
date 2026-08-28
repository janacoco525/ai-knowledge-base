"""
AI知识库 - 规则优先概念提取器
从受控文本或文件中抽取最小图谱 nodes / edges
"""
from __future__ import annotations

import re
import sys
from collections import Counter, OrderedDict
from pathlib import Path
from typing import Any, Iterable

import jieba
import jieba.analyse

from app.rag_app.config import Config
from app.rag_app.llm_client_factory import token_budget
from app.rag_app.parser import DocumentParser

HEADER_PATTERN = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)
ENGLISH_CONCEPT_PATTERN = re.compile(
    r"\b(?:[A-Z][A-Za-z0-9-]{1,}|[A-Z]{2,})(?:\s+[A-Z][A-Za-z0-9-]{1,}){0,3}\b"
)
CN_CONCEPT_PATTERN = re.compile(
    r"(?:注意力|自注意力|多头注意力|Transformer|大模型|知识图谱|机器学习|深度学习"
    r"|模型训练|模型推理|微调|预训练|提示工程|思维链|检索增强|RAG|向量数据库"
    r"|自然语言处理|计算机视觉|强化学习|生成式|扩散模型|量化|蒸馏"
    r"|Agent|智能体|多模态|对齐|RLHF|上下文学习|In-Context|LoRA|QLoRA"
    r"|推理|语义理解|文本生成|图像生成|语音识别|语音合成"
    r"|神经网络|卷积|循环|嵌入|Embedding|Token|分词"
    r"|API|SDK|开源|闭源|部署|推理加速|模型压缩"
    r")"
)
CN_KEYWORD_PATTERN = re.compile(r"^[\u4e00-\u9fffA-Za-z0-9][\u4e00-\u9fffA-Za-z0-9\- ]{0,28}$")

ENGLISH_STOPWORDS = {
    "The", "And", "For", "With", "This", "That", "From", "Then", "When",
    "Given", "JSON", "API", "GET", "POST", "HTML", "CSS", "JS",
}


def _slugify(label: str) -> str:
    import hashlib
    ascii_slug = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")
    if ascii_slug:
        return ascii_slug
    return "concept-" + hashlib.md5(label.encode("utf-8")).hexdigest()[:8]


_SKIP_LABELS = frozenset({
    # 文档元信息/出版术语
    "前言", "目录", "附录", "参考文献", "索引", "序言", "后记", "致谢",
    "本书", "白皮书", "章节", "部分", "作者", "出版", "版权", "版本",
    "作品", "内容", "介绍", "概述", "总结", "结论",
    "前言与概述", "推荐阅读路线", "导航", "封面",
    # 人名/组织名（非概念）
    "鲲鹏", "花叔", "Andrew Ng", "Nous Research",
    # 通用词
    "新增", "修改", "删除", "更新", "优化", "调整", "改进",
    "缰绳", "记忆", "工具", "版本",
    "开源", "署名", "搭建", "测试", "配置", "安装", "部署",
    "注册", "登录", "下载", "上传", "保存", "加载", "刷新",
    "点击", "选择", "输入", "输出", "显示", "隐藏",
    # 通用功能词/代词（避免成为节点）- 通用型修复
    "什么", "怎么", "为什么", "怎样", "如何", "这个", "那个", "这些", "那些",
    "自己", "他们", "我们", "你们", "她们", "它们", "有人", "人们",
    "可以", "应该", "必须", "需要", "可能", "一定", "一直",
    "没有", "不能", "不会", "不要", "不想", "没在",
    "之后", "之前", "当时", "现在", "今天", "明天", "后来", "然后", "现在", "刚才",
    "没有", "正在", "终于", "已经", "正在", "马上", "立即", "立刻", "突然",
    "东西", "事情", "地方", "时间", "时候", "分钟", "小时", "星期", "月份",
    "启动", "嵌入", "返回", "退出", "打开", "关闭", "进入", "离开",
    "起来", "出来", "下来", "过来", "回去", "坐下", "站住",
    "感觉", "觉得", "以为", "发现", "明白", "知道", "认识",
    "脸上", "身上", "头上", "心里", "脑中", "手里", "脚上",
    # 英文章节/标题噪声
    "The Complete Guide", "Building Skills", "Claude Contents Introduction",
    "Introduction", "Getting Started", "Overview", "Summary", "Conclusion",
    "Instead", "They", "Whether", "What", "How", "When", "Where", "Why",
    "Technical", "Resources", "Fundamentals", "Planning", "Testing",
    "Distribution", "Patterns", "Chapter", "Section", "Part",
    "YouTube", "GitHub", "Twitter", "Discord", "Slack",
    "MIT", "Apache", "GPL", "BSD", "CC BY", "CC BY NC SA",
    # 年月日/版本号
    "2026", "2025", "2024", "v1", "v2", "v3",
    "Talk", "MCP", "MCP enhanced",
    # 页码/格式
    "pdf", "docx", "txt", "png", "jpg",
})

# ⛔ 2026-08-19：通用中文功能词黑名单（规则兜底图谱质量修复）
# 问题：_is_valid_concept 对含中文的 label 直接放行，jieba TF-IDF 吐出的
# 连词/代词/副词/形容词/通用动词（因为/比如/大多数/真正/变得/看到/年内等）
# 全部进入图谱节点，降级图谱变成"虚词高频顶满"（《2049》实测）。
# 对标：哈工大停用词表（goto456/stopwords）——功能词必须在关键词提取前过滤；
# arXiv 2305.02579——关键短语应输出短语级概念而非通用词。
# 注意：只做精确匹配，避免误伤"极度透明/脑机接口"等复合概念。
_GENERIC_CN_STOPWORDS = frozenset({
    # 连词/副词/介词
    "因为", "所以", "但是", "然而", "而且", "并且", "或者", "还是", "就是",
    "只是", "于是", "因此", "由于", "如果", "虽然", "即使", "无论", "不管",
    "只要", "除非", "既然", "何况", "况且", "哪怕", "以及", "及其", "同时",
    "另外", "此外", "例如", "比如", "譬如", "而言", "来说",
    # 代词/指代
    "他们", "她们", "它们", "我们", "你们", "自己", "大家", "别人", "有人",
    "人们", "这些", "那些", "这个", "那个", "什么", "怎么", "怎样", "如何",
    "为什么", "哪里", "谁",
    # 数量/程度
    "大多数", "少数", "许多", "很多", "少量", "一些", "有些", "个别",
    "若干", "全部", "所有", "每个", "任何", "一切", "部分", "大量", "更多",
    "十分", "非常", "特别", "相当", "比较", "更加", "最为", "几乎", "大约",
    "大概", "将近", "超过", "至少", "至多",
    # 形容词/副词（修饰性）
    "真正", "完全", "彻底", "基本", "不断", "逐渐", "渐渐", "越来越", "一直",
    "总是", "经常", "偶尔", "有时", "已经", "曾经", "正在", "将要", "即将",
    "马上", "立刻", "突然",
    # 通用动词（行为/状态，非领域概念）
    "变得", "成为", "变成", "看到", "认为", "觉得", "感到", "使得", "进行",
    "通过", "采用", "利用", "使用", "依靠", "围绕", "涉及", "存在", "出现",
    "发生", "产生", "带来", "推动", "促进", "实现", "达到", "完成", "开始",
    "结束", "继续", "保持", "提高", "降低", "增加", "减少", "改变", "取代",
    "普及", "搜集", "定义", "发挥", "提供", "赋予", "构建", "建立",
    # 时间/通用名词（信息量低）
    "今年", "明年", "去年", "今天", "明天", "昨天", "年内", "多年", "每年",
    "东西", "事情", "地方", "时候", "分钟", "小时", "助理",
})

# ⛔ 2026-08-19：中文片段/残词过滤（规则兜底图谱通用质量修复）
# 问题：jieba 与"学科强信号词"正则会把整句残片抓成节点（《原则》实测：
# "数据分析工具以及其他算法""意识到错误是事物演变过程"），小说模式会把
# 断词残片当人名（《三体》实测："骑着自行""等汪淼回""淼找到了"）。
# 设计（通用，非针对单本书）：
# - _CN_START_STOP_CHARS / _CN_END_STOP_CHARS：首/尾字符不得为功能字；
# - _CN_SUBSTRING_STOP_CHARS：整词内功能字 ≥2 个才拒（避免误伤
#   "目的/和谐/物种/通用目的技术"等仅含 1 个功能字的合法词）；
# - _GENERIC_CN_STOPWORDS 子串命中即拒（"以及/这些/可以/多种"等）。
_CN_START_STOP_CHARS = frozenset(
    "的了是但而并且也都就还这那们等若如之其所将把让没很更最我你他她它"
)
_CN_END_STOP_CHARS = frozenset(
    "的了是在但而并且也都就还这那们个次件名等着过上下中回吗呢吧与其之"
)
_CN_SUBSTRING_STOP_CHARS = frozenset(
    "的了是在和与及或但而且并也都就还这那们个种等若如之与其所对从向被把让使着过很更最些次件名位"
)

_ENGLISH_CONCEPT_WHITELIST = frozenset({
    'ai','agent','gpt','llm','rag','prompt','model','api','sdk',
    'ml','dl','nlp','rl','cv','gpu','cpu','tpu','iot','saas',
    'claude','skills','hermes','chatgpt','copilot','langchain',
    'transformer','bert','diffusion','gan','lstm','cnn','rnn',
    'python','fastapi','uvicorn','react','vite','typescript','javascript',
    'bm25','tf idf','svd','rrf','chromadb','sqlite','tailwind',
    'ai agent','hermes agent','deep learning','machine learning',
    'reinforcement learning','natural language processing','computer vision',
    'nous research',
})

# === 文档类型识别（启发式，无 LLM）===
# 来源：用户反馈"小说侧重人物关系和故事，学科侧重知识之间的串联"
# 调研：Heptabase 卡片视角 / Obsidian 双链 / TheBrain 关系层级
# 结论：文件名前缀+内容指纹，3 种类型分支
DOC_TYPE_NOVEL = "novel"
DOC_TYPE_ACADEMIC = "academic"
DOC_TYPE_GENERAL = "general"

# === 实体分类词表（2026-08-11 提升为模块级，供 _infer_category 与 llm_graph_extractor 共用，单一来源）===
# 来源：HanLP 人名识别（姓氏词典+上下文）、OpenSPG 领域词典辅助（调研：门禁有效性治理调研.md）
_ABSTRACT_CONCEPT_SUFFIXES = frozenset({
    "原则", "方法", "理论", "系统", "模型", "过程", "阶段",
    "结构", "类型", "机制", "方案", "策略", "工具", "技术",
    "算法", "公式", "分析", "研究", "产业", "领域", "公司",
    "组织", "机构", "国家", "时代", "社会", "国家", "经济",
    "文化", "历史", "主义", "思想", "概念", "理论", "科学",
    "技术", "应用", "发展", "影响", "作用", "意义", "方式",
    "因素", "问题", "现象", "内容", "观点", "材料", "信息",
    "流程", "透明", "开放", "求真", "独立", "深度", "理性",
    "逻辑", "判断", "决策", "反馈", "迭代", "进化", "成长",
    "学习", "思维", "认知", "习惯", "行为", "纪律", "责任",
    "勇气", "谦逊", "创造", "创新", "协作", "沟通", "信任",
    "尊重",
})
_ABSTRACT_CONCEPT_WORDS = frozenset({
    "方法", "问题", "内容", "方式", "因素", "作用", "意义", "影响",
    "分析", "研究", "发展", "应用", "理论", "系统", "过程", "关系",
    "结构", "功能", "目标", "价值", "状态", "结果", "效果", "程度",
    "方面", "层面", "阶段", "领域", "水平", "方向", "背景", "基础",
    "概念", "观念", "思想", "理论", "体系", "机制", "路径",
    "自然", "世界", "社会", "人类", "生命", "意识", "文明", "宇宙",
    "地球", "中国", "美国", "时间", "空间", "物质", "能量", "信息",
    "技术", "科学", "工程", "工业", "农业", "军事", "政治", "经济",
    "文化", "历史", "语言", "文字", "宗教", "哲学", "数学", "物理",
    "化学", "生物", "医学", "教育", "艺术", "音乐", "电影", "小说",
    "独立思考", "极度透明", "极度求真", "五步流程", "头脑极度开放",
})
_COMMON_SURNAMES = frozenset({
    "李", "王", "张", "刘", "陈", "杨", "赵", "黄", "周", "吴",
    "徐", "孙", "马", "胡", "朱", "郭", "何", "罗", "高", "林",
    "恩", "安", "拉", "苏", "伊", "法", "阿", "奥", "埃", "撒",
    "罗", "叶", "史", "云", "庄", "丁", "汪", "程", "常", "章",
})
_NOUN_SUFFIXES = frozenset({
    "性", "力", "度", "量", "率", "数", "值", "件", "体", "层",
    "面", "型", "类", "群", "组", "系", "链", "网", "核", "心",
    "观", "念", "识", "知", "感", "情", "欲", "权", "法", "则",
    "统", "维", "序", "理", "例", "证", "据", "实", "际", "在",
    "内", "外", "前", "后", "上", "下", "中", "间", "一", "二",
})

_NOVEL_FILENAME_HINTS = frozenset({
    "传", "白", "番外", "纪", "记", "三国", "红楼", "西游", "水浒", "金庸",
    "三体", "球状闪电", "超新星", "野狗", "斗罗", "斗破", "全职", "凡人",
    "epub", "novel", "fiction",
})

_ACADEMIC_FILENAME_HINTS = frozenset({
    "原理", "导论", "分析", "哲学", "教程", "学科", "教材", "概论", "基础",
    "数学", "物理", "化学", "生物", "历史", "哲学", "经济", "金融", "心理",
    "管理", "算法", "数据", "统计", "机器学习", "深度学习", "神经网络",
    "手册", "指南", "report", "paper", "thesis", "白皮书",
})

_NOVEL_TEXT_HINTS = [
    "说道", "问道", "答道", "喊道", "答道", "笑道", "问道",
    "缓缓", "轻轻", "微微", "低低",
    "目光", "心里", "嘴角", "眼中", "眼底",
    "先生", "女士", "小姐", "公子", "少爷", "老爷", "夫人",
    "微微一愣", "不禁", "心头", "脑海中",
]
# 小说特征：连续对话+描写密集，前 3000 字内出现 ≥3 个小说特征词
# （注意：用组合词而非单词，避免散文/学术里"说""问"误触发）

_ACADEMIC_TEXT_HINTS = [
    "定理", "假设", "证明", "推论", "定义", "公式", "模型",
    "实验", "分析", "研究", "综述", "参考文献", "摘要", "关键词",
    "本章", "本章小结", "思考题", "习题", "目录", "引言",
    "参考文献", "DOI", "Abstract", "introduction",
]


def _detect_doc_type(file_path: str | None, text: str | None) -> str:
    """检测文档类型，用于决定概念提取策略。
    返回: novel / academic / general
    优先级：文件名前缀 > 文本指纹
    """
    filename = Path(file_path).name.lower() if file_path else ""
    base = Path(file_path).stem if file_path else ""

    # 1) 文件名强信号（优先级最高）
    if base and any(h in base for h in _NOVEL_FILENAME_HINTS):
        return DOC_TYPE_NOVEL
    if base and any(h in base for h in _ACADEMIC_FILENAME_HINTS):
        return DOC_TYPE_ACADEMIC

    # 2) 文本指纹（前 3000 字）
    sample = (text or "")[:3000]
    if sample:
        novel_score = sum(1 for h in _NOVEL_TEXT_HINTS if h in sample)
        academic_score = sum(1 for h in _ACADEMIC_TEXT_HINTS if h in sample)
        # 小说需要更高的特征分（避免散文/学术里偶尔出现"说""问"误触发）
        # 学术特征也加权：参考文献/摘要/定理 等学术词强信号
        if academic_score >= 3 and academic_score > novel_score * 1.5:
            return DOC_TYPE_ACADEMIC
        if novel_score >= 4 and novel_score > academic_score:
            return DOC_TYPE_NOVEL

    return DOC_TYPE_GENERAL


# === 小说模式专用：人物名抽取 ===
# 启发式：2-4 字中文专有名词（首字不在停用词表）+ 在文中出现 ≥2 次
_NOVEL_SKIP_FIRST_CHARS = frozenset({
    "我", "你", "他", "她", "它", "们", "的", "是", "在", "了", "有", "和",
    "就", "都", "也", "不", "没", "这", "那", "一", "个", "上", "下", "没",
    "把", "被", "对", "到", "从", "向", "以", "为", "而", "但", "或",
    # ⛔ 2026-08-19：断词残片首字（"号探测器"这类 2-4 字切片）
    "号", "去",
})
# ⛔ 2026-08-19：小说候选尾字黑名单——副词/口语尾缀（"淼平静地/默良久后/
# 能相信你/成员时必/轻警官微/文洁默默"），真实人名/专名不以这些字结尾
# （叶文洁/汪淼/球状闪电/红岸工程）
_NOVEL_END_STOP_CHARS = frozenset("地后你必微默轻着了说吧呢吗啊呀哦哈于答")

# 小说常见动作/关系词
_NOVEL_VERB_PATTERNS = re.compile(
    r"(?:[一-龥]{2,3})(说|问|笑|看|走|跑|想|知道|觉得|看着|听着|问道|回答|答|应|喊道)"
)
# 对话标记："X说道" / "X问" / "X笑了笑"
_NOVEL_DIALOG_PATTERNS = re.compile(
    r"([一-龥]{2,4})(说道|问道|笑道|答道|喊道|问|答|说|笑)"
)

def _is_valid_concept(label: str) -> bool:
    """过滤非知识概念的标签"""
    if len(label) <= 1:
        return False
    if label.lower() in _SKIP_LABELS:
        return False
    if label in _GENERIC_CN_STOPWORDS:
        return False
    # 含中文的通过
    if re.search(r'[\u4e00-\u9fff]', label):
        # ⛔ 2026-08-19：片段/残词质量门禁（通用，任何 doc_type 都走这层）
        if len(label) > 16:
            return False
        if label[0] in _CN_START_STOP_CHARS:
            return False
        if label[-1] in _CN_END_STOP_CHARS:
            return False
        # 4 字词中间位含功能字 → 断词残片（"骑着自行/盯着曲线/真对不起/冬的母亲"）；
        # 合法 4 字术语中间通常无功能字（"人际关系/球状闪电/极度透明"）
        if len(label) == 4 and any(ch in _CN_SUBSTRING_STOP_CHARS for ch in label[1:3]):
            return False
        # 整词内功能字 ≥2 个 → 句子残片（"意识到错误是事物演变过程"）；
        # 恰 1 个则放行（"目的/和谐/物种/通用目的技术"）
        if sum(1 for ch in label if ch in _CN_SUBSTRING_STOP_CHARS) >= 2:
            return False
        # 通用功能词作为子串出现（"以及/这些/可以/多种"）→ 残片
        if any(w in label for w in _GENERIC_CN_STOPWORDS if len(w) >= 2):
            return False
        return True
    # 纯英文多词技术概念（如"reinforcement learning"）也通过
    llabel = label.lower()
    if llabel in _ENGLISH_CONCEPT_WHITELIST:
        return True
    if re.fullmatch(r"[A-Z0-9]{2,8}(?: [A-Z0-9]{2,8}){0,2}", label):
        return True
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9]{2,24}", label) and any(ch.isupper() for ch in label[1:]):
        return True
    if re.fullmatch(r"[A-Za-z]{2,24}\d{1,6}", label):
        return True
    if ' ' in label and all(w[0].isupper() or w.lower() in {'of','and','for','the','in','to','a','an'} for w in label.split()):
        return False  # 标题风格
    # 驼峰或常见缩写且≥3字符
    if len(label) >= 3 and re.match(r'^[A-Z][a-z]+$', label):
        return False  # 纯大写开头英文单词，太多噪声
    return False


def _infer_category(label: str, *, doc_type: str = DOC_TYPE_GENERAL) -> str:
    """根据标签内容推断节点类别
    doc_type: novel 时优先按人物/事件分类；academic 时按方法/工具分类
    2026-08-06 增强：general 模式也识别地点/人物，避免全 concept
    """
    ll = label.lower()
    # 工具/框架/系统
    if any(k in ll for k in ("transformer", "bert", "gpt", "llm", "rag", "diffusion",
                              "gan", "lstm", "cnn", "rnn", "pytorch", "tensorflow",
                              "langchain", "vite", "react", "fastapi")):
        return "tool"
    # 组织/公司
    if any(k in ll for k in ("research", "lab", "university", "company", "openai",
                              "google", "meta", "nvidia", "microsoft",
                              "公司", "集团", "学会", "协会", "学院", "大学", "研究院", "委员会")):
        return "organization"
    # 过程/方法
    if any(k in ll for k in ("training", "inference", "fine-tuning", "finetuning",
                              "pre-training", "pretraining", "optimization",
                              "微调", "预训练", "推理", "训练")):
        return "process"
    # 地点：地理特征词（通用识别，不限 doc_type）
    if re.search(r"(国|省|市|县|城|河|江|山|海|洋|洲|港|岛|峰|谷|路|街|镇|村|湖|沙漠|平原|王国|帝国|地区|流域)", label):
        return "location"
    # 中文人名：2-4 字汉字 + 排除明显概念词尾 + 常见姓氏或对话语境
    if 2 <= len(label) <= 4 and re.fullmatch(r"[一-龥]+", label):
        concept_suffixes = _ABSTRACT_CONCEPT_SUFFIXES
        # 排除 2 字抽象概念（通用词）
        abstract = _ABSTRACT_CONCEPT_WORDS
        if label in abstract:
            return "concept"
        if not label.endswith(tuple(concept_suffixes)):
            # novel 模式判人物需更像人名：排除纯名词/动宾短语，保留常见姓氏开头
            COMMON_SURNAMES = _COMMON_SURNAMES
            # 名词性特征（最后一个字为常见名词后缀）→ 非人名
            noun_suffix = _NOUN_SUFFIXES
            if label[-1] in noun_suffix:
                return "concept"
            if doc_type == DOC_TYPE_NOVEL or label[0] in COMMON_SURNAMES:
                # 2 字需姓氏开头（更严格）；3-4 字 novel 模式更宽容但仍排除名词
                if len(label) >= 3 or label[0] in COMMON_SURNAMES:
                    return "person"
    # 事件（战争/运动/会议等）
    if re.search(r"(战争|革命|运动|事件|会议|条约|协议|危机|灾难|洪水|起义|改革)", label):
        return "event"
    # 概念（默认）
    return "concept"


class ConceptExtractor:
    def __init__(self, config: Config | None = None):
        self.config = config or Config()
        self.parser = DocumentParser(self.config)

    def extract_from_request(
        self,
        *,
        text: str | None = None,
        file_path: str | None = None,
        source_file: str | None = None,
        domain: str | None = None,
        max_nodes: int = 8,
        graph_mode: str = "auto",
    ) -> dict[str, Any]:
        chunks = self._load_chunks(text=text, file_path=file_path, source_file=source_file, domain=domain)
        payload = self._build_graph_payload(chunks, max_nodes=max_nodes, graph_mode=graph_mode)
        return payload

    def extract_from_chunks(
        self,
        chunks: list[dict[str, Any]],
        *,
        max_nodes: int = 8,
        graph_mode: str = "auto",
    ) -> dict[str, Any]:
        return self._build_graph_payload(chunks, max_nodes=max_nodes, graph_mode=graph_mode)

    def _load_chunks(
        self,
        *,
        text: str | None,
        file_path: str | None,
        source_file: str | None,
        domain: str | None,
    ) -> list[dict[str, Any]]:
        if file_path:
            parsed = self.parser.parse_file(file_path)
            if not parsed:
                return []
            return [
                {
                    "text": chunk["text"],
                    "source_file": Path(file_path).name,
                    "source_chunk_id": f"{Path(file_path).name}_{chunk.get('chunk_index', index)}",
                    "domain": domain or chunk.get("domain") or "all",
                }
                for index, chunk in enumerate(parsed)
            ]

        if not text:
            return []

        resolved_source_file = source_file or "inline.txt"
        return [
            {
                "text": text,
                "source_file": resolved_source_file,
                "source_chunk_id": f"{resolved_source_file}_0",
                "domain": domain or "all",
            }
        ]

    def _build_graph_payload(self, chunks: list[dict[str, Any]], *, max_nodes: int, graph_mode: str = "auto") -> dict[str, Any]:
        """主入口：根据 graph_mode 选择提取策略"""
        # 自动检测：如果 chunks 中包含大量标题（>=30% chunks 含 h1/h2），用 structure 模式
        effective_mode = graph_mode
        if graph_mode == "auto":
            heading_count = sum(1 for c in chunks if HEADER_PATTERN.search(c.get("text", "")))
            if len(chunks) > 0 and heading_count / len(chunks) >= 0.3:
                effective_mode = "structure"
            else:
                effective_mode = "concept"

        if effective_mode == "structure":
            return self._build_structure_payload(chunks, max_nodes=max_nodes)
        else:
            return self._build_concept_payload(chunks, max_nodes=max_nodes)

    def _build_structure_payload(self, chunks: list[dict[str, Any]], *, max_nodes: int) -> dict[str, Any]:
        """内容架构模式：提取章节/标题层级，构建父子关系图"""
        # 从所有chunks中提取标题层级
        heading_tree: list[dict] = []  # [{level, title, source_file}]
        seen_titles = set()

        for chunk in chunks:
            text = chunk.get("text", "")
            source_file = chunk.get("source_file", "unknown")
            for match in HEADER_PATTERN.finditer(text):
                level = len(match.group(1))  # h1=1, h2=2, h3=3
                title = self._clean_label(match.group(2))
                if not title or len(title) < 2 or len(title) > 60:
                    continue
                key = f"{level}-{title}"
                if key in seen_titles:
                    continue
                seen_titles.add(key)
                heading_tree.append({
                    "level": level,
                    "title": title,
                    "source_file": source_file,
                    "source_chunk_id": chunk.get("source_chunk_id", ""),
                    "_ord": len(heading_tree),  # 原始出现顺序
                })

        if not heading_tree:
            # 无标题回退：用 jieba 关键词替代
            return self._build_concept_payload(chunks, max_nodes=max_nodes)

        # 按出现顺序排列（用预存 _ord 替代 O(n²) 的 index() 查找）
        heading_tree.sort(key=lambda h: (h.get("source_file", ""), h.get("_ord", 0)))

        # 去重并限制数量
        unique_headings = []
        seen = set()
        for h in heading_tree:
            if h["title"] not in seen:
                seen.add(h["title"])
                unique_headings.append(h)
        unique_headings = unique_headings[:max_nodes]

        # 构建节点
        nodes = []
        for h in unique_headings:
            node_id = _slugify(h["title"])
            nodes.append({
                "id": node_id,
                "label": h["title"],
                "type": f"heading-h{h['level']}",
                "category": "concept",
                "source_file": h["source_file"],
                "source_chunk_ids": [h["source_chunk_id"]],
                "weight": round(0.9 - h["level"] * 0.15, 2),
                "summary": f"第{h['level']}级标题，来源 {h['source_file']}",
                "heading_level": h["level"],
            })

        # 构建边：父子关系（同级间也加顺序边）
        edges = []
        for i, h in enumerate(unique_headings):
            node_id = _slugify(h["title"])
            # 找上一级父节点
            parent = None
            for j in range(i - 1, -1, -1):
                if unique_headings[j]["level"] < h["level"]:
                    parent = unique_headings[j]
                    break
            if parent:
                edges.append({
                    "id": f"{_slugify(parent['title'])}-to-{node_id}",
                    "from": _slugify(parent["title"]),
                    "to": node_id,
                    "label": "包含",
                    "relation_type": "contains",
                    "weight": 0.8,
                    "source_file": h["source_file"],
                    "source_chunk_ids": [h["source_chunk_id"]],
                })
            # 同级前一个加顺序边
            if i > 0 and unique_headings[i - 1]["level"] == h["level"]:
                prev = unique_headings[i - 1]
                edges.append({
                    "id": f"{_slugify(prev['title'])}-next-{node_id}",
                    "from": _slugify(prev["title"]),
                    "to": node_id,
                    "label": "下一章",
                    "relation_type": "next",
                    "weight": 0.5,
                    "source_file": h["source_file"],
                    "source_chunk_ids": [h["source_chunk_id"]],
                })

        domain = chunks[0]["domain"] if chunks else "all"
        return {
            "version": "graph-data.v1",
            "generated_at": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
            "source": {
                "domain": domain,
                "file_count": len({chunk["source_file"] for chunk in chunks}) if chunks else 0,
                "chunk_count": len(chunks),
                "extractor_mode": "structure-headings",
            },
            "nodes": nodes,
            "edges": edges,
            "meta": {
                "layout_hint": "force-network",
                "graph_title": "文档内容架构",
                "graph_mode": "structure",
                "warnings": [
                    "当前为内容架构图，展示文档的章节和标题层级关系。",
                    "父子关系表示包含，同层级相邻表示顺序。"
                ],
            },
        }

    def _build_concept_payload(self, chunks: list[dict[str, Any]], *, max_nodes: int) -> dict[str, Any]:
        # 通用型修复：按文档类型切换提取策略（小说/学科/通用）
        # 启发式：文件名前缀 + 文本指纹
        sample_text = "\n".join(c.get("text", "")[:2000] for c in chunks[:3])
        source_file = chunks[0].get("source_file", "") if chunks else ""
        doc_type = _detect_doc_type(source_file, sample_text)

        ordered_nodes: "OrderedDict[str, dict[str, Any]]" = OrderedDict()
        chunk_sequences: list[list[str]] = []

        for chunk in chunks:
            # 按 doc_type 选提取器
            if doc_type == DOC_TYPE_NOVEL:
                labels = [
                    l for l in self._extract_novel_labels(chunk["text"])
                    if _is_valid_concept(l)
                ]
            elif doc_type == DOC_TYPE_ACADEMIC:
                labels = [
                    l for l in self._extract_academic_labels(chunk["text"])
                    if _is_valid_concept(l)
                ]
            else:
                labels = [
                    l
                    for l in self._extract_labels(chunk["text"], include_headings=False)
                    if _is_valid_concept(l)
                ]
            unique_ids_in_chunk: list[str] = []
            for label in labels:
                node_id = _slugify(label)
                if node_id not in ordered_nodes:
                    ordered_nodes[node_id] = {
                        "id": node_id,
                        "label": label,
                        "type": "concept",
                        "category": _infer_category(label, doc_type=doc_type),
                        "source_file": chunk["source_file"],
                        "source_chunk_ids": [chunk["source_chunk_id"]],
                        "weight": 0.55,
                        "summary": f"规则优先抽取自 {chunk['source_file']}",
                    }
                else:
                    if chunk["source_chunk_id"] not in ordered_nodes[node_id]["source_chunk_ids"]:
                        ordered_nodes[node_id]["source_chunk_ids"].append(chunk["source_chunk_id"])
                ordered_nodes[node_id]["weight"] = min(
                    round(ordered_nodes[node_id]["weight"] + 0.1, 2),
                    0.95,
                )
                if node_id not in unique_ids_in_chunk:
                    unique_ids_in_chunk.append(node_id)

            if unique_ids_in_chunk:
                chunk_sequences.append(unique_ids_in_chunk)

        # 第一层：停用词过滤
        # ⛔ 2026-08-19：跨块复现门禁（通用，按文档类型区分）——
        # 非小说 ≥4 块的文档只保留出现在 ≥2 个分块的概念：真实主题/术语跨章节
        # 复现，仅 1 块出现的多为分词残片或单句噪声（《原则》"要更关注决策机制"）。
        # 小说不设此门槛（角色/专名散落在各章，选中块内常只出现 1 次，
        # 否则图谱被清空只剩人名——《三体》实测），改由 _NOVEL_END_STOP_CHARS
        # 等小说专属残片过滤器把关。短文档（<4 块）同样不设门槛。
        min_chunks_for_node = (
            2 if (doc_type != DOC_TYPE_NOVEL and len(chunks) >= 4) else 1
        )
        ordered_list = [
            n for n in ordered_nodes.values()
            if _is_valid_concept(n["label"])
            and len(n.get("source_chunk_ids", [])) >= min_chunks_for_node
        ]
        # 图谱生成保持规则优先，避免一次读图隐式触发慢速 LLM 审核。
        # 质量筛选由停用词和 max_nodes 完成，复杂语义审核不应阻塞图谱接口。
        # ⛔ 2026-08-19：排序加成——多字领域术语优先，抵消"虚词高频顶满"问题
        # （虚词已由 _GENERIC_CN_STOPWORDS 过滤；此处对 ≥3 字术语加权重，
        #  参照 arXiv 2305.02579：关键短语（多字概念）比通用单词更有知识价值）
        def _graph_rank(n: dict) -> tuple:
            label = str(n.get("label", ""))
            length_bonus = 0.0
            if re.search(r"[\u4e00-\u9fff]", label):
                # 3 字 +0.08、4 字 +0.16…封顶 +0.4；英文缩写/专名不额外加成
                length_bonus = 0.08 * min(max(len(label) - 2, 0), 5)
            return (
                float(n.get("weight", 0)) + length_bonus,
                len(n.get("source_chunk_ids", [])),
                len(label),
            )

        ordered_list.sort(key=_graph_rank, reverse=True)
        selected_nodes = ordered_list[:max_nodes]
        selected_ids = {node["id"] for node in selected_nodes}
        edges = self._build_edges(chunk_sequences, selected_nodes, selected_ids)

        # 通用型修复：句式模式匹配 + 同句共现
        # - 0 token 开销
        # - 提取 "X是Y" "X的Y" "X和Y" "X在Y" 等中文关系
        # - 同句内两个概念间建立有语义的关系边
        edges = self._enrich_edges_with_sentence_patterns(
            chunks, selected_nodes, selected_ids, edges, doc_type=doc_type
        )

        domain = chunks[0]["domain"] if chunks else "all"
        return {
            "version": "graph-data.v1",
            "generated_at": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
            "source": {
                "domain": domain,
                "file_count": len({chunk["source_file"] for chunk in chunks}) if chunks else 0,
                "chunk_count": len(chunks),
                "extractor_mode": f"rules-first:{doc_type}",  # 通用型修复：暴露文档类型
            },
            "nodes": selected_nodes,
            "edges": edges,
            "meta": {
                "layout_hint": "force-network",
                "graph_title": "规则优先概念提取结果",
                "doc_type": doc_type,  # 通用型修复：返回文档类型让前端展示
                "warnings": [
                    "当前为规则优先最小版，未接复杂 LLM 推理。",
                    "若概念较少或关系较浅，属于当前阶段正常现象。"
                ],
            },
        }

    def _llm_filter_concepts(self, labels: list[str]) -> list[str]:
        """用LLM过滤：只保留有知识学习价值的概念"""
        if len(labels) <= 5:
            return labels  # 太少，不过滤
        try:
            from app.rag_app.llm_client_factory import create_llm_client, token_budget
            from app.rag_app.config import Config
            c = Config()
            if not c.STEP_API_KEY:
                return labels
            client = create_llm_client()
            prompt = (
                "你是一个学术知识库的概念质量审核员。以下是文档中提取的候选词汇列表。\n\n"
                "你的任务是筛选出「领域专业术语」——即在论文/技术文档中有明确定义、"
                "核心原理可展开讲解、读者值得深入学习的专业概念。\n\n"
                "必须剔除：\n"
                "- 通用日常词汇（如'架构''路线''技能''社区''文档''学习''框架'）\n"
                "- 章节标题、文件名（如'The Complete Guide''Building Skills'）\n"
                "- 英文虚词/疑问词（如'What''How''Instead''They''Whether'）\n"
                "- 平台/产品/许可证名（如'YouTube''MIT''CC BY'）\n"
                "- 公司/组织名（如'Nous Research'）\n\n"
                "保留示例：AI, Agent, Transformer, BERT, RAG, LLM, 大模型, 深度学习, 强化学习\n\n"
                "请输出筛选后的概念名，每行一个，不要序号和解释。只输出筛选后的结果。\n\n"
                "候选词汇：\n" + "\n".join(labels[:80])
            )
            response = client.chat.completions.create(
                model=c.STEP_MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=token_budget(400),
                timeout=30,
            )
            text = (response.choices[0].message.content or "").strip()
            filtered = [line.strip() for line in text.split("\n") if line.strip()]
            # 模糊匹配：LLM 可能改写概念名（加空格、改大小写、加翻译）
            label_set = {l.lower().replace(" ", "") for l in labels}
            valid = []
            for l in filtered:
                norm = l.lower().replace(" ", "")
                # 精确匹配或包含匹配
                if norm in label_set:
                    valid.append(l)
                else:
                    # 检查是否是某个原始 label 的子串或超集
                    for orig in labels:
                        orig_norm = orig.lower().replace(" ", "")
                        if norm in orig_norm or orig_norm in norm:
                            valid.append(orig)
                            break
            if len(valid) >= 2:
                return list(dict.fromkeys(valid))  # 去重保序
            # LLM返回太少，用停用词表兜底
            print(f"[WARN] LLM filter returned only {len(valid)} concepts, using stopword-based filter instead", file=sys.stderr, flush=True)
        except Exception as e:
            import traceback
            print(f"[WARN] LLM concept filter failed: {type(e).__name__}: {e}", file=sys.stderr, flush=True)
            traceback.print_exc(file=sys.stderr)
        return labels

    def _extract_labels(self, text: str, *, include_headings: bool = True) -> list[str]:
        """从文本提取概念标签。中文优先：jieba关键词 > CN标题 > 英文专有名词"""
        labels: list[str] = []
        # 单个导入 chunk 可能来自 EPUB/PDF 的超长段落，图谱只需代表性片段。
        text = text[:8000]

        if include_headings:
            for _, title in HEADER_PATTERN.findall(text):
                cleaned = self._clean_label(title)
                if cleaned:
                    labels.append(cleaned)

        # 1) 中文关键词优先。使用 jieba TF-IDF 代替纯词频：
        #    - 纯词频给"什么""可以""东西"等通用词和专有名词相同权重
        #    - TF-IDF 自动压低文档内高频但全库常见词（通用词）的分数
        #    - 零额外 token 开销（纯统计方法）
        tfidf_keywords = jieba.analyse.extract_tags(
            text, topK=30, withWeight=True
        )
        for keyword, weight in tfidf_keywords:
            cleaned = self._clean_label(keyword)
            if cleaned and len(cleaned) >= 2 and CN_KEYWORD_PATTERN.fullmatch(cleaned):
                labels.append(cleaned)

        # 2) 硬编码中文概念模式（保底）
        for match in CN_CONCEPT_PATTERN.findall(text):
            cleaned = self._clean_label(match)
            if cleaned:
                labels.append(cleaned)

        # 3) 英文专有名词：仅当在白名单中或包含明显技术特征时保留
        for match in ENGLISH_CONCEPT_PATTERN.findall(text):
            cleaned = self._clean_label(match)
            if not cleaned or cleaned in ENGLISH_STOPWORDS:
                continue
            lowered = cleaned.lower()
            if lowered in _ENGLISH_CONCEPT_WHITELIST:
                labels.append(cleaned)
            elif re.fullmatch(r"[A-Z0-9]{2,8}(?: [A-Z0-9]{2,8}){0,2}", cleaned):
                labels.append(cleaned)

        return self._dedupe_preserve_order(labels)

    def _extract_novel_labels(self, text: str) -> list[str]:
        """小说模式：优先提取人物名（对话/动作上下文中的专有名词）
        启发式：
        - "X说道" / "X问" / "X笑" 中的 X（2-4 字中文）
        - 文中出现 ≥2 次的 2-4 字中文专有名词
        - 对话+动作密集段落里的人名
        """
        text = text[:12000]  # 小说取更多上下文
        labels: list[str] = []
        candidates = Counter()

        # 1) 对话/动作模式："X说道"、"X问" 等
        for match in _NOVEL_DIALOG_PATTERNS.finditer(text):
            name = match.group(1)
            if 2 <= len(name) <= 4 and name[0] not in _NOVEL_SKIP_FIRST_CHARS:
                candidates[name] += 2  # 对话标记加权

        # 2) 动作关联："X说" 前后的实体
        for match in _NOVEL_VERB_PATTERNS.finditer(text):
            name = match.group(1)
            if 2 <= len(name) <= 4 and name[0] not in _NOVEL_SKIP_FIRST_CHARS:
                candidates[name] += 1

        # 3) 高频 2-4 字专有名词
        for match in re.finditer(r"[一-龥]{2,4}", text):
            word = match.group()
            if word[0] in _NOVEL_SKIP_FIRST_CHARS:
                continue
            # ⛔ 2026-08-19：断词残片过滤（"骑着自行/等汪淼回/淼找到了"）。
            # 名字/专名不含功能字（"叶文洁/红岸工程/球状闪电"均无）；
            # 出现任意功能字即视为分词残片。
            if any(c in word for c in _CN_SUBSTRING_STOP_CHARS):
                continue
            # 副词/口语尾缀结尾 → 场景描写残片（"淼平静地/能相信你/轻警官微"）
            if word[-1] in _NOVEL_END_STOP_CHARS:
                continue
            candidates[word] += 1

        # ⛔ 2026-08-19：统一残片过滤——对话/动作/高频三条路径的候选都要过：
        # 功能字包含（"骑着自行"）或副词/口语尾缀（"淼平静地/能相信你/轻警官微/
        # 哈哈哈哈"）的一律视为分词/场景残片，不进入人名候选池。
        for word in list(candidates):
            if any(c in word for c in _CN_SUBSTRING_STOP_CHARS):
                del candidates[word]
            elif word[-1] in _NOVEL_END_STOP_CHARS:
                del candidates[word]

        # 4) 排序：对话加权 > 出现次数 > 长度
        sorted_candidates = sorted(
            candidates.items(),
            key=lambda x: (-x[1], -len(x[0]))
        )

        # 5) 过滤：至少出现 2 次，且不算停用词
        for word, count in sorted_candidates:
            if count < 2:
                break
            if not _is_valid_concept(word):
                continue
            labels.append(word)
            if len(labels) >= 30:  # 小说模式多取一些
                break

        return self._dedupe_preserve_order(labels)

    def _extract_academic_labels(self, text: str) -> list[str]:
        """学科模式：术语+概念+方法，弱化人名/对话
        与通用模式区别：更严格过滤非术语词，加大对方法/理论的权重
        """
        # 学科模式直接复用通用 _extract_labels，但额外加学术关键词模式
        text = text[:8000]
        labels: list[str] = []
        for _, title in HEADER_PATTERN.findall(text):
            cleaned = self._clean_label(title)
            if cleaned and len(cleaned) >= 3:
                labels.append(cleaned)

        # 学科关键词
        tfidf_keywords = jieba.analyse.extract_tags(text, topK=40, withWeight=True)
        for keyword, weight in tfidf_keywords:
            cleaned = self._clean_label(keyword)
            if cleaned and len(cleaned) >= 2 and CN_KEYWORD_PATTERN.fullmatch(cleaned):
                labels.append(cleaned)

        # 学科强信号词（2026-08-19：前缀收紧为 2-6 字，残片由 _is_valid_concept 统一过滤）
        for match in re.finditer(
            r"[一-龥]{2,6}(?:定理|定律|法则|理论|模型|方法|算法|公式|原理|机制|系统|结构|过程|阶段|类型)",
            text
        ):
            cleaned = self._clean_label(match.group())
            if cleaned:
                labels.append(cleaned)

        # 硬编码中文概念（保底）
        for match in CN_CONCEPT_PATTERN.findall(text):
            cleaned = self._clean_label(match)
            if cleaned:
                labels.append(cleaned)

        return self._dedupe_preserve_order(labels)

    def _clean_label(self, label: str) -> str | None:
        cleaned = re.sub(r"[`*_>#\-:()\[\]{}]+", " ", label).strip()
        cleaned = re.sub(r"\s+", " ", cleaned)
        if len(cleaned) < 2 or len(cleaned) > 60:
            return None
        # 过滤纯数字/纯标点
        if re.fullmatch(r"[0-9.,;:!?。，；：！？\s]+", cleaned):
            return None
        return cleaned

    def _dedupe_preserve_order(self, labels: Iterable[str]) -> list[str]:
        seen = set()
        ordered = []
        for label in labels:
            key = label.lower()
            if key in seen:
                continue
            seen.add(key)
            ordered.append(label)
        return ordered

    def _infer_relation(self, label_a: str, label_b: str) -> tuple[str, str]:
        """推断概念关系标签"""
        a, b = label_a.strip(), label_b.strip()
        if not a or not b:
            return f"{a or '?'}→{b or '?'}"[:12], "related_to"
        if b in a and len(b) >= 3:
            return f"含{b}", "contains"
        if a in b and len(a) >= 3:
            return f"属{a}", "belongs_to"
        ta = [w for w in jieba.lcut(a) if len(w) >= 2 and w not in {'的','和','与','或','是','在','了'}]
        tb = [w for w in jieba.lcut(b) if len(w) >= 2 and w not in {'的','和','与','或','是','在','了'}]
        shared = [w for w in ta if w in tb]
        if shared:
            return shared[0][:6], "share_term"
        return f"{a[:4]}…{b[:4]}", "related_to"

    def _llm_label_edges(self, pairs: list[tuple[str, str]]) -> dict[str, str]:
        """用 LLM 批量生成边标签，返回 {a|||b: label} 映射"""
        if len(pairs) <= 1:
            return {}
        try:
            from app.rag_app.llm_client_factory import create_llm_client, token_budget
            from app.rag_app.config import Config
            c = Config()
            if not c.STEP_API_KEY:
                return {}
            client = create_llm_client()
            pair_text = "\n".join([f"{i+1}. {a} <-> {b}" for i, (a, b) in enumerate(pairs)])
            prompt = f"""以下是知识图谱中节点之间的关系对。请为每对关系生成一个简短中文关系标签（≤6个字），描述它们之间的具体语义关系。
例如：'模型训练 训练出 模型'、'GPU 加速 深度学习'、'Python 实现 FastAPI'。
只返回编号和标签，格式：1. 关系标签

{pair_text}"""
            resp = client.chat.completions.create(
                model=c.STEP_MODEL,
                messages=[{"role":"user","content":prompt}],
                temperature=0.2, max_tokens=min(len(pairs)*10+50, 500), timeout=15,
            )
            text = resp.choices[0].message.content or ""
            result = {}
            for line in text.strip().split("\n"):
                m = re.match(r"(\d+)\.\s*(.+)", line.strip())
                if m:
                    idx = int(m.group(1)) - 1
                    label = m.group(2).strip()[:12]
                    if 0 <= idx < len(pairs):
                        key = f"{pairs[idx][0]}|||{pairs[idx][1]}"
                        result[key] = label
            return result
        except Exception:
            return {}

    def _enrich_edges_with_sentence_patterns(
        self,
        chunks: list[dict[str, Any]],
        selected_nodes: list[dict[str, Any]],
        selected_ids: set[str],
        existing_edges: list[dict[str, Any]],
        *,
        doc_type: str = DOC_TYPE_GENERAL,
    ) -> list[dict[str, Any]]:
        """通用型修复：句式模式匹配 + 同句共现，零 token 开销

        中文关系模式（按优先级）：
        1. X是Y的Z → X → Z, rel=是
        2. X的Y → X[领属]Y, rel=属于
        3. X是Y → X[是]Y, rel=是
        4. X和Y → X[和]Y, rel=和
        5. X在Y → X[位于]Y, rel=在
        6. X想Y → X[想]Y, rel=想
        7. X说Y → X[说]Y, rel=说
        8. X有Y → X[拥有]Y, rel=有
        9. 同句内任意两概念 → fallback rel=提及
        """
        # 通用型修复：用 [一-龥]{1,3} 替代 {1,4} 防止贪婪
        # - 中文 2-3 字人名/地名占绝大多数（陈异/苗靖/藤城/北京市）
        # - 贪婪 {1,4} 会吞掉"是/的/和"等字使分组错位（如"陈异和苗靖"→陈异,苗靖是最）
        # - {1,3} 严格限制 slot 长度
        PATTERNS = [
            # X是Y的Z：身份+领属
            (re.compile(r"([\u4e00-\u9fff]{1,3})是([\u4e00-\u9fff]{1,3})的([\u4e00-\u9fff]{1,3})"), "是_的"),
            # X是Y：身份
            (re.compile(r"([\u4e00-\u9fff]{1,3})是([\u4e00-\u9fff]{1,3})"), "是"),
            # X的Y：领属
            (re.compile(r"([\u4e00-\u9fff]{1,3})的([\u4e00-\u9fff]{1,3})"), "属于"),
            # X和Y / X与Y：并列
            (re.compile(r"([\u4e00-\u9fff]{1,3})和([\u4e00-\u9fff]{1,3})"), "和"),
            (re.compile(r"([\u4e00-\u9fff]{1,3})与([\u4e00-\u9fff]{1,3})"), "与"),
            # X在Y：位置
            (re.compile(r"([\u4e00-\u9fff]{1,3})在([\u4e00-\u9fff]{1,3})"), "在"),
            # X有/到/从/说/看/想 Y
            (re.compile(r"([\u4e00-\u9fff]{1,3})有([\u4e00-\u9fff]{1,3})"), "有"),
            (re.compile(r"([\u4e00-\u9fff]{1,3})到([\u4e00-\u9fff]{1,3})"), "到"),
            (re.compile(r"([\u4e00-\u9fff]{1,3})从([\u4e00-\u9fff]{1,3})"), "从"),
            (re.compile(r"([\u4e00-\u9fff]{1,3})说([\u4e00-\u9fff]{1,3})"), "说"),
            (re.compile(r"([\u4e00-\u9fff]{1,3})看([\u4e00-\u9fff]{1,3})"), "看"),
            (re.compile(r"([\u4e00-\u9fff]{1,3})想([\u4e00-\u9fff]{1,3})"), "想"),
        ]
        # 选中的概念 label → id 反查
        label_to_id = {n.get("label", ""): n["id"] for n in selected_nodes}

        pattern_edges: "OrderedDict[str, dict[str, Any]]" = OrderedDict()
        # 中文句子切分（句号、问号、感叹号、分号、引号等）
        SENT_SPLIT = re.compile(r"(?<=[。！？；\?\!\;\n])")

        for chunk in chunks:
            text = chunk.get("text", "")
            if not text:
                continue
            source_file = chunk.get("source_file", "")
            source_chunk_id = chunk.get("source_chunk_id", "")
            # 切成句子
            for sentence in SENT_SPLIT.split(text):
                if len(sentence) < 4 or len(sentence) > 200:
                    continue
                # 找该句中出现的所有选中的概念
                in_sent = []
                for label, nid in label_to_id.items():
                    if label in sentence:
                        in_sent.append((label, nid))
                if len(in_sent) < 2:
                    continue
                # 对每对概念试匹配关系
                for i, (la, nida) in enumerate(in_sent):
                    for lb, nidb in in_sent[i + 1:]:
                        # 找关系
                        rel = "同句"
                        relation_type = "co_occurrence"
                        # 小说模式：提取人名间的连接词（"看着""问道"等）
                        if doc_type == DOC_TYPE_NOVEL:
                            pa, pb = sentence.find(la), sentence.find(lb)
                            if pa >= 0 and pb >= 0 and pa != pb:
                                start, end = (pa, pb) if pa < pb else (pb, pa)
                                left_label = la if pa < pb else lb
                                between = sentence[start + len(left_label):end]
                                conn = re.sub(r"[，、。！？；："'"'"'""\s]+", "", between)
                                if conn and 1 <= len(conn) <= 4:
                                    rel = conn
                                    relation_type = "interaction"
                        # 试模式匹配（提高语义精度）
                        FUNCTION_WORDS = {"的", "是", "和", "在", "有", "到", "从", "说",
                                          "看", "想", "与", "也", "就", "都", "而", "但",
                                          "或", "把", "被", "给", "让", "叫", "做", "了",
                                          "过", "着", "呢", "吗", "啊", "哦", "哈", "呀",
                                          "吧", "嘛", "嗯", "啦", "中", "于", "上", "下",
                                          "里", "外", "前", "后"}
                        def _trim_function(g: str) -> str:
                            while g and g[0] in FUNCTION_WORDS:
                                g = g[1:]
                            while g and g[-1] in FUNCTION_WORDS:
                                g = g[:-1]
                            return g
                        for pat, pat_rel in PATTERNS:
                            if relation_type == "interaction":
                                break  # 小说连接词已确定，不覆盖
                            m = pat.search(sentence)
                            if not m:
                                continue
                            groups = m.groups()
                            trimmed = [_trim_function(g) for g in groups]
                            if la in trimmed and lb in trimmed:
                                rel = pat_rel
                                break
                        # 边 ID 唯一
                        edge_id = f"{nida}-pat-{rel}-{nidb}"
                        if edge_id in pattern_edges:
                            pattern_edges[edge_id]["weight"] = min(
                                round(pattern_edges[edge_id]["weight"] + 0.1, 2), 0.95
                            )
                            continue
                        pattern_edges[edge_id] = {
                            "id": edge_id,
                            "from": nida,
                            "to": nidb,
                            "_label_a": la,
                            "_label_b": lb,
                            "label": rel,
                            "relation_type": relation_type,
                            "weight": 0.7,
                            "source_file": source_file,
                            "source_chunk_ids": [source_chunk_id],
                        }

        # 合并：先全部返回已有的 co-occurrence 边，再追加有意义的模式边
        # 限制模式边数量，避免边爆炸
        max_pattern_edges = min(len(selected_nodes) * 3, 60)
        pattern_edge_list = list(pattern_edges.values())[:max_pattern_edges]
        # 去掉临时字段
        for e in pattern_edge_list:
            e.pop("_label_a", None)
            e.pop("_label_b", None)
        return list(existing_edges) + pattern_edge_list

    def _build_edges(
        self,
        chunk_sequences: list[list[str]],
        nodes: list[dict[str, Any]],
        selected_ids: set[str],
    ) -> list[dict[str, Any]]:
        node_lookup = {node["id"]: node for node in nodes}
        edges: "OrderedDict[str, dict[str, Any]]" = OrderedDict()

        for sequence in chunk_sequences:
            filtered = [node_id for node_id in sequence if node_id in selected_ids]
            for left, right in zip(filtered, filtered[1:]):
                edge_id = f"{left}-to-{right}"
                source_file = node_lookup[left]["source_file"]
                source_chunk_ids = sorted(
                    set(node_lookup[left]["source_chunk_ids"]) | set(node_lookup[right]["source_chunk_ids"])
                )
                if edge_id not in edges:
                    edges[edge_id] = {
                        "id": edge_id,
                        "from": left,
                        "to": right,
                        "_label_a": node_lookup[left].get("label", left),
                        "_label_b": node_lookup[right].get("label", right),
                        "label": "关联",
                        "relation_type": "related_to",
                        "weight": 0.6,
                        "source_file": source_file,
                        "source_chunk_ids": source_chunk_ids,
                    }
                else:
                    edges[edge_id]["weight"] = min(round(edges[edge_id]["weight"] + 0.08, 2), 0.9)
                    edges[edge_id]["source_chunk_ids"] = sorted(
                        set(edges[edge_id]["source_chunk_ids"]) | set(source_chunk_ids)
                    )

        # 使用启发式关系标签，图谱接口不隐式触发 LLM 请求。
        edge_list = list(edges.values())
        for e in edge_list:
            rel_label, rel_type = self._infer_relation(e["_label_a"], e["_label_b"])
            e["label"] = rel_label
            e["relation_type"] = rel_type
            del e["_label_a"]
            del e["_label_b"]

        # 通用型修复：单 chunk 共现不出边时 → 文档级相邻共现
        # 场景：野狗骨头/三体 等小说的 chunks 单个只有 1~2 个概念，zip 不出边
        # 策略：把每个节点在文档中的"上一个概念节点"和"下一个概念节点"连起来
        if not edge_list and len(selected_nodes) >= 2:
            # 按节点首次出现的 chunk 顺序排序，串联成一条链
            ordered_ids = [n["id"] for n in selected_nodes]
            for i in range(len(ordered_ids) - 1):
                left = ordered_ids[i]
                right = ordered_ids[i + 1]
                edge_id = f"{left}-chain-{right}"
                rel_label, rel_type = self._infer_relation(
                    node_lookup[left].get("label", left),
                    node_lookup[right].get("label", right),
                )
                edge_list.append({
                    "id": edge_id,
                    "from": left,
                    "to": right,
                    "label": rel_label,
                    "relation_type": rel_type,
                    "weight": 0.4,
                    "source_file": node_lookup[left].get("source_file", ""),
                    "source_chunk_ids": sorted(
                        set(node_lookup[left].get("source_chunk_ids", []))
                        | set(node_lookup[right].get("source_chunk_ids", []))
                    ),
                })

        return edge_list
