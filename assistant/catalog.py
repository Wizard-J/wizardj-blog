"""Read-only catalog of the published Hugo article sources copied to the assistant."""

from datetime import datetime, timezone
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import unicodedata
from urllib.parse import quote, unquote, urlsplit


ORIGIN = "https://wizardj.cn"
MAX_RESULTS = 20
MAX_SOURCE_BYTES = 512_000
MAX_BODY_CHARS = 18_000
HIDDEN_TAGS = {"script", "style", "template", "noscript", "svg"}
BLOCK_TAGS = {
    "article", "aside", "blockquote", "div", "figcaption", "figure", "footer",
    "header", "h1", "h2", "h3", "h4", "h5", "h6", "hr", "ol", "p", "pre",
    "section", "table", "tr", "ul",
}


class ArticleText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.chunks = []
        self.hidden = []
        self.preformatted = False

    def handle_starttag(self, tag, attrs):
        if tag in HIDDEN_TAGS:
            self.hidden.append(tag)
        if self.hidden:
            return
        if tag in BLOCK_TAGS:
            self.chunks.append("\n\n")
        elif tag == "li":
            self.chunks.append("\n- ")
        elif tag == "br":
            self.chunks.append("\n")
        elif tag in {"td", "th"}:
            self.chunks.append(" | ")
        elif tag == "img":
            alt = dict(attrs).get("alt")
            if alt:
                self.chunks.append(f"[图片：{alt}]")
        if tag == "pre":
            self.preformatted = True

    def handle_endtag(self, tag):
        if self.hidden:
            if self.hidden[-1] == tag:
                self.hidden.pop()
            return
        if tag in BLOCK_TAGS:
            self.chunks.append("\n\n")
        if tag == "pre":
            self.preformatted = False

    def handle_data(self, data):
        if not self.hidden:
            self.chunks.append(data if self.preformatted else re.sub(r"\s+", " ", data))

    def text(self):
        return re.sub(r"\n{3,}", "\n\n", "".join(self.chunks)).strip()


def normalize(text):
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def timestamp(value):
    if not isinstance(value, str) or not value:
        raise ValueError("Missing article timestamp")
    # Halo may have serialized nanoseconds while Python 3.9 only accepts microseconds.
    value = re.sub(r"(\.\d{6})\d+(?=Z$|[+-]\d{2}:?\d{2}$)", r"\1", value)
    value = re.sub(r"\.(\d{1,5})(?=Z$|[+-]\d{2}:?\d{2}$)", lambda match: "." + match[1].ljust(6, "0"), value)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def archive_address(value):
    if not isinstance(value, str) or any(ord(char) < 32 for char in value):
        raise ValueError("Invalid article address")
    parsed = urlsplit(value)
    if (parsed.scheme, parsed.netloc) not in {("", ""), ("https", "wizardj.cn")}:
        raise ValueError("Article address must be on wizardj.cn")
    match = re.fullmatch(r"/archives/([^/]+)/?", parsed.path)
    if not match or parsed.query or parsed.fragment:
        raise ValueError("Article address must be an archive page")
    slug = unquote(match[1], errors="strict")
    if (slug in {".", ".."} or len(slug) > 256 or any(char in slug for char in "/\\%?#")
            or any(unicodedata.category(char).startswith("C") or char.isspace() for char in slug)):
        raise ValueError("Invalid archive slug")
    return slug, f"{ORIGIN}/archives/{quote(slug, safe='-._~')}/"


def read_article(path, now):
    with path.open("rb") as source:
        raw = source.read(MAX_SOURCE_BYTES + 1)
    if len(raw) > MAX_SOURCE_BYTES:
        return None
    text = raw.decode("utf-8-sig").lstrip()
    metadata, end = json.JSONDecoder().raw_decode(text)
    if not isinstance(metadata, dict) or metadata.get("draft", False) is not False:
        return None
    # Treat pages with a Hugo render override conservatively. All imported public
    # sources are regular archive pages and have an explicit date and URL.
    for build in ("_build", "build"):
        options = metadata.get(build, {})
        if not isinstance(options, dict) or options.get("render", "always") != "always":
            return None
    date = timestamp(metadata.get("date"))
    published_value = metadata.get("publishDate", metadata.get("publishdate")) or metadata["date"]
    expiry_value = metadata.get("expiryDate", metadata.get("expirydate"))
    if date > now or timestamp(published_value) > now or (expiry_value and timestamp(expiry_value) <= now):
        return None
    title = metadata.get("title")
    tags = metadata.get("tags", [])
    if not isinstance(title, str) or not title.strip() or not isinstance(tags, list):
        return None
    if not all(isinstance(tag, str) for tag in tags):
        return None
    key, url = archive_address(metadata.get("url"))
    body = text[end:].strip()
    if path.suffix == ".html":
        parser = ArticleText()
        parser.feed(body)
        parser.close()
        body = parser.text()
    if len(body) > MAX_BODY_CHARS:
        body = body[:MAX_BODY_CHARS].rstrip() + "\n\n[正文超过工具长度上限，余下内容可打开原文查看。]"
    return key, date, {"id": key, "title": title.strip(), "date": date.isoformat(), "url": url, "tags": tags, "body": body}


class Catalog:
    def __init__(self, path):
        self._articles = {}
        self._search = {}
        now = datetime.now(timezone.utc)
        dated_articles = []
        for article in sorted(Path(path).iterdir()):
            if article.name.startswith((".", "_")) or article.name.casefold() == "agents.md":
                continue
            if article.suffix not in {".html", ".md"} or article.is_symlink() or not article.is_file():
                continue
            try:
                parsed = read_article(article, now)
            except (OSError, ValueError):
                continue
            if parsed:
                dated_articles.append(parsed)
        dated_articles.sort(key=lambda article: (article[1], article[0]), reverse=True)
        for key, _, article in dated_articles:
            if key not in self._articles:
                self._articles[key] = article
                self._search[key] = (
                    normalize(article["title"]), normalize(" ".join(article["tags"])), normalize(article["body"]),
                )

    @staticmethod
    def _summary(article):
        return {key: value[:] if key == "tags" else value for key, value in article.items() if key != "body"}

    def list(self, query=""):
        if not isinstance(query, str) or len(query) > 160:
            raise ValueError("搜索关键词无效或超过长度限制")
        normalized = normalize(query)
        keys = list(self._articles)
        if normalized:
            parts = tuple(dict.fromkeys(re.findall(r"[^\W_]+", normalized)))
            ranked = []
            for key in keys:
                title, tags, body = self._search[key]
                weights = [(title, 16), (tags, 8), (body, 2)]
                score = sum(weight * 2 for haystack, weight in weights if normalized in haystack)
                matched = sum(any(part in haystack for haystack, _ in weights) for part in parts)
                if parts and matched == len(parts):
                    score += sum(weight for haystack, weight in weights for part in parts if part in haystack)
                if score:
                    ranked.append((score, key))
            ranked.sort(key=lambda item: item[0], reverse=True)
            keys = [key for _, key in ranked]
        return [self._summary(self._articles[key]) for key in keys[:MAX_RESULTS]]

    def get(self, key):
        if not isinstance(key, str) or len(key) > 500:
            return None
        if key.startswith(("/archives/", ORIGIN + "/")):
            try:
                key, _ = archive_address(key)
            except ValueError:
                return None
        article = self._articles.get(key)
        if article is None:
            return None
        return {**self._summary(article), "body": article["body"]}
