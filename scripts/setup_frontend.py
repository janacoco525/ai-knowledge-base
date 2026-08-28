import pathlib
# ⛔ 2026-08-19：不再硬编码绝对路径（项目曾迁移 集合→合集，旧路径已失效）；
# base = 脚本上级目录 = 项目根，任何位置/任何目录名下都正确。
base = pathlib.Path(__file__).resolve().parents[1]

# ===== Write gemini_adapter.py =====
adapter_code = '''\
"""Gemini frontend -> FastAPI adapter"""
from __future__ import annotations
import json, os, re as _re, time
from datetime import datetime
from pathlib import Path
from typing import Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(tags=["GeminiAdapter"])

class SaveKeyReq(BaseModel): apiKey: str
class ScrapeReq(BaseModel): url: str
class ChatReq(BaseModel): messages: list[dict] = []; documentContext: Optional[list[dict]] = None; webSearchEnabled: bool = False
class SummaryReq(BaseModel): title: str = ""; content: str
class ExtractReq(BaseModel): content: str
class CompareReq(BaseModel): documents: list[dict]
class DefineReq(BaseModel): term: str; context: str = ""
class CardReq(BaseModel): title: str = ""; content: str; docId: str = ""

@router.get("/api/health")
async def health():
    return {"status":"ok","apiKeyPresent":bool(os.getenv("STEP_API_KEY","")),"message":"AI-KB backend running"}

@router.post("/api/save-key")
async def save_key(r: SaveKeyReq):
    from app.rag_app.config import Config
    k = r.apiKey.strip()
    if not k: return {"success":False,"error":"Empty key"}
    try:
        from openai import OpenAI
        c = OpenAI(api_key=k, base_url=Config.STEP_API_BASE)
        c.chat.completions.create(model=Config.STEP_MODEL, messages=[{"role":"user","content":"ok"}], max_tokens=5, timeout=15)
        ep = Path(__file__).resolve().parents[1] / ".env"
        if ep.exists():
            ls = ep.read_text(encoding="utf-8").splitlines()
            for i,l in enumerate(ls):
                if l.strip().startswith("STEP_API_KEY"): ls[i] = f"STEP_API_KEY={k}"; break
            else: ls.append(f"STEP_API_KEY={k}")
            ep.write_text("\\n".join(ls)+"\\n", encoding="utf-8")
        os.environ["STEP_API_KEY"] = k
        return {"success":True,"message":"API key verified!"}
    except Exception as e:
        return {"success":False,"error":str(e)[:200]}

@router.post("/api/scrape")
async def scrape(r: ScrapeReq):
    import urllib.request
    try:
        req = urllib.request.Request(r.url, headers={"User-Agent":"Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read().decode("utf-8", errors="replace")
        c = _re.sub(r"<script[^>]*>[\\s\\S]*?</script>","",html,flags=_re.IGNORECASE)
        c = _re.sub(r"<style[^>]*>[\\s\\S]*?</style>","",c,flags=_re.IGNORECASE)
        c = _re.sub(r"</div>|</p>|<br\\s*/?>","\\n",c)
        c = _re.sub(r"<[^>]+>"," ",c)
        c = c.replace("&nbsp;"," ").replace("&lt;","<").replace("&gt;",">").replace("&amp;","&")
        ls = [l.strip() for l in c.split("\\n") if len(l.strip())>5]
        t = "\\n".join(ls[:300])
        tm = _re.search(r"<title>([\\s\\S]*?)</title>",html,_re.IGNORECASE)
        title = tm.group(1).strip() if tm else "Imported"
        return {"title":title,"content":t[:15000],"originalUrl":r.url}
    except Exception as e:
        raise HTTPException(500,str(e)[:200])

@router.post("/api/gemini/chat")
async def chat(r: ChatReq):
    from app.rag_app.shared_engine import get_engine
    q = ""
    for m in reversed(r.messages):
        if isinstance(m,dict) and m.get("role")=="user": q=m.get("content",""); break
    if not q: raise HTTPException(400,"Empty message")
    try:
        eng = get_engine()
        res = eng.query(q, top_k=5)
        ans = res.get("answer") or res.get("content") or "No results found"
        srcs = [{"title":s.get("source",s.get("file_name","KB")),"uri":"#"} for s in res.get("sources",[])[:5]]
        return {"text":ans,"groundingSources":srcs}
    except Exception as e:
        return {"text":f"Error: {e}","groundingSources":[]}

@router.post("/api/gemini/summarize")
async def summarize(r: SummaryReq):
    from app.rag_app.shared_engine import get_engine
    eng = get_engine()
    p = f"Summarize in Chinese.\\n1.Executive summary(2-3 sentences)\\n2.Key points(3-5 items)\\n\\nTitle: {r.title or 'Untitled'}\\n{r.content[:6000]}"
    try:
        resp = eng.llm_client.chat.completions.create(model=eng.model_name,messages=[{"role":"user","content":p}],temperature=0.3,max_tokens=600,timeout=60)
        return {"summary":resp.choices[0].message.content or ""}
    except Exception as e:
        return {"summary":f"LLM error: {e}"}

@router.post("/api/gemini/extract-entities")
async def extract_entities(r: ExtractReq):
    from app.rag_app.shared_engine import get_engine
    eng = get_engine()
    p = 'Extract entities as JSON. Output: {"nodes":[{"id":"..","label":"..","category":"concept|tool|system|process|person|organization"}],"edges":[{"source":"..","target":"..","label":"..","id":"e_1"}]}. Max 12 nodes. Use Chinese labels.\\n\\nText:\\n' + r.content[:5000]
    try:
        resp = eng.llm_client.chat.completions.create(model=eng.model_name,messages=[{"role":"user","content":p}],temperature=0.2,max_tokens=1200,timeout=60)
        raw = resp.choices[0].message.content or "{}"
        m = _re.search(r"\\{[\\s\\S]*\\}",raw)
        data = json.loads(m.group(0) if m else raw)
        return {"nodes":data.get("nodes",[]),"edges":data.get("edges",[])}
    except: return {"nodes":[],"edges":[]}

@router.post("/api/gemini/compare")
async def compare(r: CompareReq):
    from app.rag_app.shared_engine import get_engine
    if len(r.documents)<2: raise HTTPException(400,"Need 2+ docs")
    eng = get_engine()
    p = "Compare these documents in Chinese:\\n\\n"
    for i,d in enumerate(r.documents[:5]):
        p += f"Doc{i+1}: {d.get('title','')}\\n{d.get('content','')[:3000]}\\n\\n---\\n"
    p += "Sections: 1.Core 2.Common 3.Diff 4.Matrix(table) 5.Recommendation"
    try:
        resp = eng.llm_client.chat.completions.create(model=eng.model_name,messages=[{"role":"user","content":p}],temperature=0.3,max_tokens=1500,timeout=90)
        return {"comparison":resp.choices[0].message.content or ""}
    except Exception as e: return {"comparison":f"Error: {e}"}

@router.post("/api/gemini/define-term")
async def define_term(r: DefineReq):
    from app.rag_app.shared_engine import get_engine
    eng = get_engine()
    p = f"Explain: {r.term}\\nContext: {r.context or 'N/A'}\\nOutput: 1.Definition 2.Application. Chinese."
    try:
        resp = eng.llm_client.chat.completions.create(model=eng.model_name,messages=[{"role":"user","content":p}],temperature=0.3,max_tokens=300,timeout=15)
        return {"definition":resp.choices[0].message.content or ""}
    except Exception as e: return {"definition":f"Error: {e}"}

@router.post("/api/gemini/generate-cards")
async def generate_cards(r: CardReq):
    from app.rag_app.shared_engine import get_engine
    eng = get_engine()
    p = f'Generate 3 flashcards from: {r.title}. Output JSON: {{"cards":[{{"front":"..","back":"..","tags":[".."]}}]}}. Chinese.\\n{r.content[:5000]}'
    try:
        resp = eng.llm_client.chat.completions.create(model=eng.model_name,messages=[{"role":"user","content":p}],temperature=0.3,max_tokens=800,timeout=60)
        raw = resp.choices[0].message.content or "{}"
        m = _re.search(r"\\{[\\s\\S]*\\}",raw)
        data = json.loads(m.group(0) if m else raw)
        ts = int(time.time())
        cards = []
        for i,c in enumerate(data.get("cards",[])):
            cards.append({"id":f"card-{ts}-{i}","docId":r.docId,"front":c.get("front",""),"back":c.get("back",""),"tags":c.get("tags",["AI"]),"createdAt":datetime.now().isoformat()})
        return {"cards":cards}
    except:
        ts=int(time.time())
        return {"cards":[{"id":f"fb-{ts}","docId":r.docId,"front":"Topic?","back":"Failed","tags":["fallback"],"createdAt":datetime.now().isoformat()}]}

@router.post("/api/scan-workspace")
async def scan_ws():
    from app.rag_app.shared_engine import get_engine
    try:
        kb = get_engine().kb
        files = kb.list_files() if hasattr(kb,"list_files") else []
    except: files = []
    result = []
    for f in files:
        fid = f.get("id",f.get("file_id",""))
        ct = ""
        try:
            chunks = kb.get_chunks_by_file(fid, max_chunks=20)
            ct = "\\n".join(c.get("text","") for c in chunks)
        except: pass
        result.append({"title":f.get("name",""),"folder":f.get("domain",""),"content":ct[:5000],"path":f.get("file_path",""),"ext":"md"})
    return {"files":result}
'''

adapter_path = base / "app" / "rag_app" / "routes" / "gemini_adapter.py"
adapter_path.write_text(adapter_code, encoding="utf-8")
print(f"OK adapter: {adapter_path.stat().st_size} bytes")

# ===== Modify api_server.py =====
api_path = base / "app" / "rag_app" / "api_server.py"
content = api_path.read_text(encoding="utf-8")
content = content.replace(
    "from routes.recommend import router as recommend_router",
    "from routes.recommend import router as recommend_router\nfrom routes.gemini_adapter import router as gemini_adapter_router"
)
content = content.replace(
    "app.include_router(recommend_router)",
    "app.include_router(recommend_router)\napp.include_router(gemini_adapter_router)"
)
api_path.write_text(content, encoding="utf-8")
print(f"OK api_server: adapter present = {'gemini_adapter' in content}")
print("ALL DONE")
