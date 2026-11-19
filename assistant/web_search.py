"""Bounded public search for the Wizard assistant."""
import ipaddress
import json
import threading
from collections import deque
from html.parser import HTMLParser
from time import monotonic
from urllib.error import URLError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

ANYSEARCH_URL = "https://api.anysearch.com/v1/search"
DUCKDUCKGO_URL = "https://lite.duckduckgo.com/lite/"
MAX_RESULTS = 5
MAX_RESPONSE_BYTES = 256 * 1024
MAX_RESULTS_CHARS = 2200
_REQUEST_TIMES = deque()
_BUDGET_LOCK = threading.Lock()


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _reserve_request():
    now = monotonic()
    with _BUDGET_LOCK:
        while _REQUEST_TIMES and now - _REQUEST_TIMES[0] >= 86400:
            _REQUEST_TIMES.popleft()
        if len(_REQUEST_TIMES) >= 80:
            return "已达到联网搜索的 24 小时限额（80 次）。"
        if sum(now - timestamp < 60 for timestamp in _REQUEST_TIMES) >= 12:
            return "联网搜索过于频繁，请稍后再试。"
        _REQUEST_TIMES.append(now)
    return None


def _public_link(value):
    if not isinstance(value, str) or len(value) > 1600:
        return ""
    if value.startswith("//"):
        value = "https:" + value
    if any(char.isspace() or ord(char) < 32 for char in value) or "\\" in value:
        return ""
    try:
        parsed = urlsplit(value)
        if parsed.hostname in ("duckduckgo.com", "lite.duckduckgo.com") and parsed.path == "/l/":
            value = parse_qs(parsed.query).get("uddg", [""])[0]
            parsed = urlsplit(value)
        if any(char.isspace() or ord(char) < 32 for char in value) or "\\" in value:
            return ""
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            return ""
        if parsed.username is not None or parsed.password is not None or parsed.port not in (None, 80, 443):
            return ""
        host = parsed.hostname.rstrip(".")
        try:
            if not ipaddress.ip_address(host).is_global:
                return ""
        except ValueError:
            labels = host.split(".")
            if len(labels) < 2 or labels[-1].isdigit() or labels[-1] in ("local", "localhost", "internal", "test"):
                return ""
    except ValueError:
        return ""
    return value


def _compact_results(rows, limit):
    results = []
    seen = set()
    budget = MAX_RESULTS_CHARS
    for row in rows:
        if not isinstance(row, dict):
            continue
        url = _public_link(row.get("url"))
        raw_title = row.get("title")
        if not url or url in seen or not isinstance(raw_title, str):
            continue
        title = " ".join(raw_title.split())[:120]
        if not title or len(title) + len(url) > budget:
            continue
        raw_snippet = row.get("snippet")
        snippet = " ".join(raw_snippet.split()) if isinstance(raw_snippet, str) else ""
        snippet = snippet[:min(220, budget - len(title) - len(url))]
        results.append({"title": title, "url": url, "snippet": snippet})
        seen.add(url)
        budget -= len(title) + len(url) + len(snippet)
        if len(results) == limit:
            break
    return results


class DuckDuckGoResults(HTMLParser):
    def __init__(self):
        super().__init__()
        self.results = []
        self.challenge = False
        self.parts = []
        self.capture = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        classes = (attrs.get("class") or "").split()
        if tag == "form" and attrs.get("id") == "challenge-form":
            self.challenge = True
        if tag == "a" and "result-link" in classes:
            self.results.append({"url": attrs.get("href")})
            self.capture, self.parts = ("a", "title"), []
        if tag == "td" and self.results and "result-snippet" in classes:
            self.capture, self.parts = ("td", "snippet"), []

    def handle_data(self, data):
        if self.capture:
            self.parts.append(data)

    def handle_endtag(self, tag):
        if self.capture and self.capture[0] == tag:
            self.results[-1][self.capture[1]] = "".join(self.parts)
            self.capture = None


def _read(request):
    with build_opener(NoRedirects()).open(request, timeout=7) as response:
        if response.status != 200:
            raise ValueError("unexpected status")
        body = response.read(MAX_RESPONSE_BYTES + 1)
    if len(body) > MAX_RESPONSE_BYTES:
        raise ValueError("response too large")
    return body


def _search_anysearch(query, limit):
    request = Request(
        ANYSEARCH_URL,
        data=json.dumps({"query": query, "max_results": limit}).encode("utf-8"),
        headers={"Accept": "application/json", "Content-Type": "application/json"},
    )
    payload = json.loads(_read(request))
    if not isinstance(payload, dict) or payload.get("code") != 0:
        raise ValueError("invalid search response")
    data = payload.get("data")
    results = data.get("results") if isinstance(data, dict) else None
    if not isinstance(results, list):
        raise ValueError("invalid search results")
    return results


def _search_duckduckgo(query, _limit):
    request = Request(
        DUCKDUCKGO_URL + "?" + urlencode({"q": query}),
        headers={"Accept": "text/html", "User-Agent": "Mozilla/5.0"},
    )
    parser = DuckDuckGoResults()
    parser.feed(_read(request).decode("utf-8", errors="replace"))
    parser.close()
    if parser.challenge:
        raise ValueError("search challenge")
    return parser.results


def web_search(query, limit=5):
    """Return source metadata. Search result URLs are never fetched by this tool."""
    if not isinstance(query, str) or not query.strip():
        return {"ok": False, "error": "请输入联网搜索关键词。"}
    query = " ".join(query.split())[:120]
    limit = max(1, min(MAX_RESULTS, limit)) if type(limit) is int else MAX_RESULTS
    error = _reserve_request()
    if error:
        return {"ok": False, "query": query, "error": error}
    available = False
    for provider, search in (("AnySearch", _search_anysearch), ("DuckDuckGo", _search_duckduckgo)):
        try:
            results = _compact_results(search(query, limit), limit)
        except (URLError, OSError, ValueError):
            continue
        available = True
        if results:
            return {"ok": True, "query": query, "provider": provider, "results": results}
    if available:
        return {"ok": True, "query": query, "results": [], "message": "未找到可用搜索结果。"}
    return {"ok": False, "query": query, "error": "联网搜索服务暂时不可用，请稍后重试。"}
