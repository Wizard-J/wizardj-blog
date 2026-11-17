"""Import published entries and their available assets from the Halo backup."""

import argparse
import json
import re
import shutil
from pathlib import Path
from urllib.parse import quote, unquote, urljoin, urlsplit

from bs4 import BeautifulSoup


SITE = Path(__file__).resolve().parent.parent
SITE_HOSTS = {"wizardj.cn", "www.wizardj.cn", "localhost"}
MEDIA_ATTRIBUTES = ("src", "poster", "data-src", "data-original")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("backup", type=Path)
    backup = parser.parse_args().backup.resolve()
    assets = {
        item["url"]: item["file"]
        for item in json.loads((backup / "assets.json").read_text())
        if item["file"]
    }
    entries = [
        item
        for item in json.loads((backup / "articles.json").read_text())
        if item["status"] == "published" and item["version"] == "index"
    ]
    routes = set()
    copied = set()
    unresolved = set()

    def move_reference(value, original_url, is_media=False):
        if not value or value.startswith(("#", "data:", "mailto:", "tel:")):
            return value
        absolute = urljoin(original_url, value)
        parsed = urlsplit(absolute)
        if parsed.scheme not in {"http", "https"}:
            return ""
        path = unquote(parsed.path)
        if value.startswith("../../../assets/"):
            candidate = unquote(value.removeprefix("../../../"))
        elif absolute in assets:
            candidate = assets[absolute]
        elif parsed.hostname in SITE_HOSTS and path.startswith("/upload/"):
            candidate = "assets/attachments" + path
        else:
            candidate = None
        if candidate:
            source = (backup / candidate).resolve()
            if source.is_relative_to(backup / "assets") and source.is_file():
                if candidate.startswith("assets/attachments/upload/"):
                    target = Path("upload") / source.relative_to(backup / "assets/attachments/upload")
                else:
                    target = Path("halo-assets") / source.relative_to(backup / "assets")
                if target not in copied:
                    destination = SITE / "static" / target
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source, destination)
                    copied.add(target)
                return "/" + quote(target.as_posix())
        if is_media:
            unresolved.add(absolute)
        if parsed.hostname in SITE_HOSTS and not is_media:
            return (parsed.path or "/") + (("?" + parsed.query) if parsed.query else "") + (("#" + parsed.fragment) if parsed.fragment else "")
        return absolute

    for item in entries:
        original_url = item["original_url"]
        route = unquote(urlsplit(original_url).path).rstrip("/")
        if not route.startswith("/") or ".." in route.split("/") or route.casefold() in routes:
            raise ValueError(f"Invalid or duplicate article path {route!r}")
        routes.add(route.casefold())
        soup = BeautifulSoup((backup / item["html"]).read_text(), "html.parser")
        article = soup.find("article")
        if article is None or article.h1 is None:
            raise ValueError(f"No article wrapper in {item['html']}")
        article.h1.decompose()
        for element in article.find_all(["script", "base", "form", "object", "embed", "iframe"]):
            element.decompose()
        for element in article.find_all(True):
            for attr in list(element.attrs):
                if attr.startswith("on") or attr == "srcdoc":
                    del element[attr]
            for attr in MEDIA_ATTRIBUTES:
                if element.has_attr(attr):
                    element[attr] = move_reference(element[attr], original_url, is_media=True)
            for attr in ("srcset", "data-srcset"):
                if element.has_attr(attr) and not element[attr].startswith("data:"):
                    rewritten = []
                    for candidate in element[attr].split(","):
                        parts = candidate.split(maxsplit=1)
                        if parts:
                            parts[0] = move_reference(parts[0], original_url, is_media=True)
                            rewritten.append(" ".join(parts))
                    element[attr] = ", ".join(rewritten)
            if element.has_attr("href"):
                element["href"] = move_reference(element["href"], original_url)
        metadata = {
            "title": item["title"],
            "date": item["published_at"] or item["created_at"],
            "url": route + "/",
            "tags": item["tags"],
            "categories": item["categories"],
        }
        if item["cover"]:
            cover = move_reference(item["cover"], original_url, is_media=True)
            if cover.startswith("/"):
                metadata["params"] = {"cover": cover}
        section = "posts" if item["type"] == "posts" else "pages"
        stem = Path(item["html"]).parent.name
        if not re.fullmatch(r"[\w.-]+", stem):
            raise ValueError(f"Invalid source file stem {stem!r}")
        output = SITE / "content" / section / f"{stem}.html"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n" + article.decode_contents(), encoding="utf-8")

    print(f"Imported {len(entries)} published entries, copied {len(copied)} referenced files.")
    for url in sorted(unresolved):
        print("External or unavailable asset:", url)


if __name__ == "__main__":
    main()
