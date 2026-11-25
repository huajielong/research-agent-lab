from typing import List, Dict
import requests
import xml.etree.ElementTree as ET
from typing import List, Dict, Optional
import os, re, tempfile
import requests
from pdfminer.high_level import extract_text

session = requests.Session()
session.headers.update(
    {"User-Agent": "LF-ADP-Agent/1.0 (mailto:your.email@example.com)"}
)

## -----

from typing import List, Dict, Optional
import os, re, time, tempfile
import requests
import xml.etree.ElementTree as ET

# ----- 带重试与请求头的会话 -----
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from typing import List, Dict, Optional
import os, re, time
import requests
import xml.etree.ElementTree as ET
from io import BytesIO


from typing import List, Dict, Optional
import os, re, time
import requests
import xml.etree.ElementTree as ET
from io import BytesIO

# ----- 带重试与请求头的会话 -----
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


def _build_session(
    user_agent: str = "LF-ADP-Agent/1.0 (mailto:your.email@example.com)",
) -> requests.Session:
    s = requests.Session()
    s.headers.update(
        {
            "User-Agent": user_agent,
            "Accept": "*/*",
            "Accept-Encoding": "gzip, deflate",
            "Connection": "keep-alive",
        }
    )
    retry = Retry(
        total=5,
        connect=5,
        read=5,
        backoff_factor=0.6,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET", "HEAD"]),
        raise_on_redirect=False,
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=10, pool_maxsize=20)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    return s


session = _build_session()


# ----- 工具函数 -----
def ensure_pdf_url(abs_or_pdf_url: str) -> str:
    url = abs_or_pdf_url.strip().replace("http://", "https://")
    if "/pdf/" in url and url.endswith(".pdf"):
        return url
    url = url.replace("/abs/", "/pdf/")
    if not url.endswith(".pdf"):
        url += ".pdf"
    return url


def _safe_filename(name: str) -> str:
    import re

    name = re.sub(r"[^A-Za-z0-9._-]+", "_", name)
    if not name.lower().endswith(".pdf"):
        name += ".pdf"
    return name


def clean_text(s: str) -> str:
    s = re.sub(r"-\n", "", s)  # "transfor-\nmers" -> "transformers"
    s = re.sub(r"\r\n|\r", "\n", s)  # 统一换行符
    s = re.sub(r"[ \t]+", " ", s)  # 合并多余空格
    s = re.sub(r"\n{3,}", "\n\n", s)  # 连续空行最多保留一行
    return s.strip()


def fetch_pdf_bytes(pdf_url: str, timeout: int = 90) -> bytes:
    r = session.get(pdf_url, timeout=timeout, allow_redirects=True)
    r.raise_for_status()
    return r.content


def pdf_bytes_to_text(pdf_bytes: bytes, max_pages: Optional[int] = None) -> str:
    # 1) PyMuPDF
    try:
        import fitz  # PyMuPDF

        out = []
        with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
            n = len(doc)
            limit = n if max_pages is None else min(max_pages, n)
            for i in range(limit):
                out.append(doc.load_page(i).get_text("text"))
        return "\n".join(out)
    except Exception:
        pass

    # 2) pdfminer.six
    try:
        from pdfminer.high_level import extract_text_to_fp

        buf_in = BytesIO(pdf_bytes)
        buf_out = BytesIO()
        extract_text_to_fp(buf_in, buf_out)
        return buf_out.getvalue().decode("utf-8", errors="ignore")
    except Exception as e:
        raise RuntimeError(f"PDF 文本抽取失败：{e}")


def maybe_save_pdf(pdf_bytes: bytes, dest_dir: str, filename: str) -> str:
    os.makedirs(dest_dir, exist_ok=True)
    path = os.path.join(dest_dir, _safe_filename(filename))
    with open(path, "wb") as f:
        f.write(pdf_bytes)
    return path


# ----- arXiv 检索 -----
from typing import List, Dict
import time, requests, xml.etree.ElementTree as ET
from io import BytesIO

# session = _build_session()
# ensure_pdf_url(), clean_text(), fetch_pdf_bytes(), pdf_bytes_to_text(), maybe_save_pdf()


def arxiv_search_tool(
    query: str,
    max_results: int = 3,
) -> List[Dict]:
    """
    在 arXiv 上检索，并返回把 `summary` 覆盖为 PDF 抽取文本（尽可能为全文）后的结果。
    """
    # ===== 内部开关 =====
    _INCLUDE_PDF = True
    _EXTRACT_TEXT = True
    _MAX_PAGES = 6
    _TEXT_CHARS = 5000
    _SAVE_FULL_TEXT = False
    _SLEEP_SECONDS = 1.0
    # ==========================

    api_url = (
        "https://export.arxiv.org/api/query"
        f"?search_query=all:{requests.utils.quote(query)}&start=0&max_results={max_results}"
    )

    out: List[Dict] = []
    try:
        resp = session.get(api_url, timeout=60)
        resp.raise_for_status()
    except requests.exceptions.RequestException as e:
        return [{"error": f"arXiv API 请求失败：{e}"}]

    try:
        root = ET.fromstring(resp.content)
        ns = {"atom": "http://www.w3.org/2005/Atom"}

        for entry in root.findall("atom:entry", ns):
            title = (
                entry.findtext("atom:title", default="", namespaces=ns) or ""
            ).strip()
            published = (
                entry.findtext("atom:published", default="", namespaces=ns) or ""
            )[:10]
            url_abs = entry.findtext("atom:id", default="", namespaces=ns) or ""
            # 原始摘要
            abstract_summary = (
                entry.findtext("atom:summary", default="", namespaces=ns) or ""
            ).strip()

            authors = []
            for a in entry.findall("atom:author", ns):
                nm = a.findtext("atom:name", default="", namespaces=ns)
                if nm:
                    authors.append(nm)

            link_pdf = None
            for link in entry.findall("atom:link", ns):
                if link.attrib.get("title") == "pdf":
                    link_pdf = link.attrib.get("href")
                    break
            if not link_pdf and url_abs:
                link_pdf = ensure_pdf_url(url_abs)

            item = {
                "title": title,
                "authors": authors,
                "published": published,
                "url": url_abs,
                "summary": abstract_summary,
                "link_pdf": link_pdf,
            }

            pdf_bytes = None
            if (_INCLUDE_PDF or _EXTRACT_TEXT) and link_pdf:
                try:
                    pdf_bytes = fetch_pdf_bytes(link_pdf, timeout=90)
                    time.sleep(_SLEEP_SECONDS)
                except Exception as e:
                    item["pdf_error"] = f"PDF 下载失败：{e}"

            if _EXTRACT_TEXT and pdf_bytes:
                try:
                    text = pdf_bytes_to_text(pdf_bytes, max_pages=_MAX_PAGES)
                    text = clean_text(text) if text else ""
                    if text:
                        if _SAVE_FULL_TEXT:
                            item["summary"] = text
                        else:
                            item["summary"] = text[:_TEXT_CHARS]
                except Exception as e:
                    item["text_error"] = f"文本抽取失败：{e}"

            out.append(item)
        return out
    except ET.ParseError as e:
        return [{"error": f"arXiv API XML 解析失败：{e}"}]
    except Exception as e:
        return [{"error": f"未预期的错误：{e}"}]


# ---- 工具定义 ----
arxiv_tool_def = {
    "type": "function",
    "function": {
        "name": "arxiv_search_tool",
        "description": "Searches arXiv and (internally) fetches PDFs to memory and extracts text.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search keywords."},
                "max_results": {"type": "integer", "default": 3},
            },
            "required": ["query"],
        },
    },
}


## -----


import os
from dotenv import load_dotenv
from tavily import TavilyClient

load_dotenv()  # 从 .env 文件加载环境变量


def tavily_search_tool(
    query: str, max_results: int = 5, include_images: bool = False
) -> list[dict]:
    """
    使用 Tavily API 执行一次搜索。

    参数：
        query (str)：搜索查询词。
        max_results (int)：返回结果数量（默认 5）。
        include_images (bool)：是否包含图片结果。

    返回：
        List[dict]：包含 'title'、'content'、'url' 等键的字典列表。
    """
    api_key = os.getenv("TAVILY_API_KEY")
    if not api_key:
        # 与其他工具保持一致：返回 error 字典而不是抛异常，
        # 否则 aisuite 的工具循环会中断整个 research_agent 回合。
        return [{"error": "环境变量中未找到 TAVILY_API_KEY。"}]

    client = TavilyClient(api_key, api_base_url=os.getenv("DLAI_TAVILY_BASE_URL"))

    try:
        response = client.search(
            query=query, max_results=max_results, include_images=include_images
        )

        results = []
        for r in response.get("results", []):
            results.append(
                {
                    "title": r.get("title", ""),
                    "content": r.get("content", ""),
                    "url": r.get("url", ""),
                }
            )

        if include_images:
            for img_url in response.get("images", []):
                results.append({"image_url": img_url})

        return results

    except Exception as e:
        return [{"error": str(e)}]  # 便于 LLM 智能体解析


tavily_tool_def = {
    "type": "function",
    "function": {
        "name": "tavily_search_tool",
        "description": "Performs a general-purpose web search using the Tavily API.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search keywords for retrieving information from the web.",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of results to return.",
                    "default": 5,
                },
                "include_images": {
                    "type": "boolean",
                    "description": "Whether to include image results.",
                    "default": False,
                },
            },
            "required": ["query"],
        },
    },
}

## Wikipedia 检索工具

from typing import List, Dict
import wikipedia
import wikipedia.wikipedia as _wikipedia

# wikipedia 包默认用 http:// 且通用 UA，Wikimedia 会返回 429 纯文本，
# 包内 r.json() 因此报 "Expecting value: line 1 column 1"。
# 注意：wikipedia.API_URL 只是 from .wikipedia import * 的副本，
# 必须改子模块 wikipedia.wikipedia 里的同名全局变量。
_wikipedia.API_URL = "https://en.wikipedia.org/w/api.php"
_wikipedia.USER_AGENT = "research-agent-lab/1.0 (local educational use)"


def wikipedia_search_tool(query: str, sentences: int = 5) -> List[Dict]:
    """
    在 Wikipedia 上检索给定查询的摘要。

    参数：
        query (str)：用于 Wikipedia 的搜索查询。
        sentences (int)：摘要包含的句子数量。

    返回：
        List[Dict]：仅含一个字典的列表，包含 title、summary 和 url。
    """
    try:
        page_title = wikipedia.search(query)[0]
        page = wikipedia.page(page_title)
        summary = wikipedia.summary(page_title, sentences=sentences)

        return [{"title": page.title, "summary": summary, "url": page.url}]
    except Exception as e:
        return [{"error": str(e)}]


# 工具定义
wikipedia_tool_def = {
    "type": "function",
    "function": {
        "name": "wikipedia_search_tool",
        "description": "Searches for a Wikipedia article summary by query string.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search keywords for the Wikipedia article.",
                },
                "sentences": {
                    "type": "integer",
                    "description": "Number of sentences in the summary.",
                    "default": 5,
                },
            },
            "required": ["query"],
        },
    },
}


# 工具映射
tool_mapping = {
    "tavily_search_tool": tavily_search_tool,
    "arxiv_search_tool": arxiv_search_tool,
    "wikipedia_search_tool": wikipedia_search_tool,
}
