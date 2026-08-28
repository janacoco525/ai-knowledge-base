"""
前端适配路由 — 网页抓取、工作区扫描
职责拆分：Provider 管理 → routes/providers.py，LLM 操作 → routes/llm_ops.py
"""
from __future__ import annotations
import ipaddress
import re as _re
import socket
import urllib.request
from urllib.parse import urlparse
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(tags=["FrontendAdapter"])


class ScrapeReq(BaseModel):
    url: str


MAX_SCRAPE_BYTES = 2 * 1024 * 1024


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise HTTPException(400, "网页重定向被拒绝，请直接提供最终地址")


def _validate_scrape_url(raw_url: str) -> str:
    parsed = urlparse(raw_url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise HTTPException(400, "只支持 http/https 网页地址")

    hostname = parsed.hostname
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(hostname, None)}
        for address in addresses:
            ip = ipaddress.ip_address(address)
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                raise HTTPException(400, "不允许访问本机或内网地址")
    except socket.gaierror:
        raise HTTPException(400, "无法解析网页地址")
    return parsed.geturl()


@router.post("/api/scrape")
async def scrape(r: ScrapeReq):
    try:
        url = _validate_scrape_url(r.url)
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        opener = urllib.request.build_opener(_NoRedirect)
        with opener.open(req, timeout=15) as resp:
            body = resp.read(MAX_SCRAPE_BYTES + 1)
            if len(body) > MAX_SCRAPE_BYTES:
                raise HTTPException(413, "网页内容超过 2MB 限制")
            html = body.decode("utf-8", errors="replace")
        c = _re.sub(r"<script[^>]*>[\s\S]*?</script>", "", html, flags=_re.IGNORECASE)
        c = _re.sub(r"<style[^>]*>[\s\S]*?</style>", "", c, flags=_re.IGNORECASE)
        c = _re.sub(r"</div>|</p>|<br\s*/?>", "\n", c)
        c = _re.sub(r"<[^>]+>", " ", c)
        c = c.replace("&nbsp;", " ").replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")
        ls = [l.strip() for l in c.split("\n") if len(l.strip()) > 5]
        t = "\n".join(ls[:300])
        tm = _re.search(r"<title>([\s\S]*?)</title>", html, _re.IGNORECASE)
        title = tm.group(1).strip() if tm else "Imported"
        return {"title": title, "content": t[:15000], "originalUrl": url}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e)[:200])


@router.post("/api/scan-workspace")
async def scan_ws():
    from app.rag_app.shared_engine import get_engine
    try:
        kb = get_engine().kb
        files = kb.list_files() if hasattr(kb, "list_files") else []
    except Exception:
        files = []
    result = []
    for f in files:
        fid = f.get("id", f.get("file_id", ""))
        ct = ""
        try:
            chunks = kb.get_chunks_by_file(fid, max_chunks=500)
            ct = "\n".join(c.get("text", "") for c in chunks)
        except Exception:
            pass
        result.append({"title": f.get("name", ""), "folder": f.get("domain", ""), "content": ct, "path": f.get("file_path", ""), "ext": "md"})
    return {"files": result}
