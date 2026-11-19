"""Build and publish a single explicitly selected Hugo draft."""

from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tempfile
from uuid import UUID
from xml.etree import ElementTree

from catalog import ORIGIN
from remote_publish import StaleRevisionError
from state import require_private


MAX_DRAFT_BYTES = 128_000
SITE_DIRECTORIES = ("layouts", "static", "assets", "data", "i18n", "archetypes", "themes")


class PublishError(RuntimeError):
    """The selected draft could not be published."""


def publication_id(value):
    try:
        valid = isinstance(value, str) and str(UUID(value, version=4)) == value
    except ValueError:
        valid = False
    if not valid:
        raise ValueError("发布编号无效")
    return value


def preview_draft(data):
    if not isinstance(data, bytes) or not 0 < len(data) <= MAX_DRAFT_BYTES:
        raise ValueError("草稿为空或超过发布长度限制")
    try:
        text = data.decode("utf-8").lstrip()
        metadata, end = json.JSONDecoder().raw_decode(text)
    except (UnicodeDecodeError, ValueError):
        raise ValueError("草稿的 Markdown 文件头无效") from None
    if not isinstance(metadata, dict) or metadata.get("draft") is not True:
        raise ValueError("请选择已确认保存的私有草稿")
    title = metadata.get("title")
    tags = metadata.get("tags", [])
    body = text[end:].strip()
    params = metadata.get("params")
    if isinstance(params, dict) and params.get("author") == "巫师前沿站":
        body = re.sub(r"^> (?:自动来源核验通过，待站长审阅。|自动核验需要人工复核：[^\n]*)\n\s*\n", "", body, count=1)
    if (not isinstance(title, str) or not title.strip() or len(title) > 160
            or any(ord(char) < 32 for char in title) or not body
            or any(ord(char) < 32 and char not in "\t\n\r" for char in body)
            or not isinstance(tags, list) or len(tags) > 20
            or any(not isinstance(tag, str) or not tag.strip() or len(tag) > 80
                   or any(ord(char) < 32 for char in tag) for tag in tags)):
        raise ValueError("草稿标题、正文或标签无效")
    if re.search(r"\{\{\s*[<%]", body):
        raise ValueError("草稿含 Hugo 短代码，发布前请改为标准 Markdown")
    return {"title": title.strip(), "body": body, "tags": list(dict.fromkeys(tags))}


def draft_view(data):
    view = preview_draft(data)
    return view["title"], view["body"]


def safe_tree(path):
    if path.is_symlink() or not path.is_dir():
        raise PublishError("发布源目录配置无效")
    for node in path.rglob("*"):
        if node.is_symlink() or not (node.is_dir() or node.is_file()):
            raise PublishError("发布源目录含不支持的文件")


def snapshot_digest(path):
    safe_tree(path)
    digest = sha256()
    for entry in sorted(p for p in path.rglob("*") if p.is_file()):
        digest.update(entry.relative_to(path).as_posix().encode("utf-8") + b"\0")
        digest.update(sha256(entry.read_bytes()).digest())
    return digest.hexdigest()


def sync_directory(path):
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def write_json(path, value):
    descriptor, filename = tempfile.mkstemp(prefix=".publish-state-", dir=path.parent)
    temporary = Path(filename)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        sync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


class Publisher:
    def __init__(self, site_source, article_link, remote, private_dir, *,
                 public_article_link=None, hugo_bin="/opt/homebrew/bin/hugo"):
        self.site_source = Path(site_source)
        self.article_link = Path(article_link)
        self.public_link = Path(public_article_link) if public_article_link is not None else self.article_link
        self.remote = remote
        self.private_dir = Path(private_dir)
        self.hugo_bin = hugo_bin
        self.private_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        require_private(self.private_dir.lstat(), stat.S_ISDIR, "Publisher state")
        self.pending = self.private_dir / "pending.json"
        self.public_revision = self.private_dir / "public-revision.json"

    def _receipt(self, key):
        return self.private_dir / (publication_id(key) + ".json")

    def publication(self, key):
        receipt = self._receipt(key)
        if not receipt.exists():
            return None
        record = json.loads(receipt.read_text(encoding="utf-8"))
        return {name: record[name] for name in ("title", "url")}

    def _pending(self):
        if not self.pending.exists():
            return None
        return json.loads(self.pending.read_text(encoding="utf-8"))

    def pending_id(self):
        pending = self._pending()
        if pending is None or self._receipt(pending["id"]).exists():
            return None
        return pending["id"]

    @staticmethod
    def _active(link):
        if not link.is_symlink():
            raise PublishError("文章目录尚未配置为可切换版本")
        try:
            generation = link.resolve(strict=True)
        except OSError:
            raise PublishError("无法读取当前文章目录") from None
        if generation.parent != link.parent.resolve() or not generation.is_dir():
            raise PublishError("文章目录超出发布范围")
        return generation

    def _stage_articles(self, link, identifier, article, expected_digest):
        base = self._active(link)
        candidate = Path(tempfile.mkdtemp(prefix=".post-release-" + identifier + "-", dir=base.parent))
        try:
            safe_tree(base)
            shutil.copytree(base, candidate, dirs_exist_ok=True)
            if snapshot_digest(candidate) != expected_digest or self._active(link) != base:
                raise PublishError("文章源文件已变化，请重新发布")
            with (candidate / (identifier + ".md")).open("xb") as handle:
                handle.write(article)
                handle.flush()
                os.fsync(handle.fileno())
            sync_directory(candidate)
            sync_directory(candidate.parent)
            return {"base": base.name, "candidate": candidate.name, "digest": expected_digest}
        except Exception:
            shutil.rmtree(candidate)
            raise

    def _prepare_hugo(self, source, public_articles):
        config = self.site_source / "hugo.toml"
        if config.is_symlink() or not config.is_file():
            raise PublishError("缺少 Hugo 发布配置")
        shutil.copy2(config, source / "hugo.toml")
        (source / "publish.toml").write_text("[markup.goldmark.renderer]\nunsafe = false\n", encoding="utf-8")
        for name in SITE_DIRECTORIES:
            original = self.site_source / name
            if original.exists():
                safe_tree(original)
                shutil.copytree(original, source / name)
        (source / "content").mkdir()
        pages = self.site_source / "content" / "pages"
        if pages.exists():
            safe_tree(pages)
            shutil.copytree(pages, source / "content" / "pages")
        shutil.copytree(public_articles, source / "content" / "posts")

    def _build(self, job, identifier, public_articles):
        source = job / "source"
        release = job / "release"
        source.mkdir()
        self._prepare_hugo(source, public_articles)
        env = {key: value for key, value in os.environ.items() if not key.startswith("HUGO_")}
        try:
            result = subprocess.run(
                [self.hugo_bin, "--source", str(source), "--config", "hugo.toml,publish.toml",
                 "--destination", str(release), "--environment", "production"],
                env=env, capture_output=True, timeout=60, check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            raise PublishError("Hugo 构建未完成，请检查发布运行环境") from None
        if result.returncode:
            raise PublishError("Hugo 构建失败，当前网站未切换")
        html = release / "archives" / identifier / "index.html"
        if not html.is_file() or not (release / "index.html").is_file():
            raise PublishError("构建结果缺少新文章或首页")
        page = html.read_bytes()
        url = f"{ORIGIN}/archives/{identifier}/"
        if b'<!-- raw HTML omitted -->' in page:
            raise PublishError("草稿含无法安全呈现的 HTML，请改为 Markdown")
        if not page or url.encode("utf-8") not in page:
            raise PublishError("新文章的公开地址未通过检查")
        try:
            sitemap = ElementTree.parse(release / "sitemap.xml")
            entries = [node.text for node in sitemap.findall(".//{*}loc")]
            feed = ElementTree.parse(release / "index.xml")
            feed_links = [node.text for node in feed.findall("./channel/item/link")]
        except (ElementTree.ParseError, OSError):
            raise PublishError("构建结果缺少有效站点地图或订阅源") from None
        if url not in entries or url not in feed_links:
            raise PublishError("新文章未加入站点地图或订阅源")
        safe_tree(release)
        shutil.rmtree(source)

    def _stage(self, identifier, data, draft):
        base_revision = self.remote.revision()
        if self.public_revision.exists():
            saved_revision = json.loads(self.public_revision.read_text(encoding="utf-8"))["revision"]
            if base_revision != saved_revision:
                raise PublishError("线上博客版本已变更，请先同步发布底稿与线上版本")
        else:
            write_json(self.public_revision, {"revision": base_revision})
        if self.public_link != self.article_link and self._active(self.public_link) == self._active(self.article_link):
            raise PublishError("发布底稿须与工作中的文章目录隔离")
        job = Path(tempfile.mkdtemp(prefix=".publish-job-", dir=self.private_dir))
        candidates = []
        try:
            now = datetime.now(timezone.utc).isoformat(timespec="seconds")
            public_path = f"/archives/{identifier}/"
            metadata = {"title": draft["title"], "date": now, "publishDate": now,
                        "slug": identifier, "url": public_path, "draft": False}
            if draft["tags"]:
                metadata["tags"] = draft["tags"]
            article = (json.dumps(metadata, ensure_ascii=False, indent=2) + "\n\n" + draft["body"] + "\n").encode("utf-8")
            links = (self.article_link, self.public_link) if self.public_link != self.article_link else (self.article_link,)
            for link in links:
                baseline = snapshot_digest(self._active(link))
                candidates.append(self._stage_articles(link, identifier, article, baseline))
            public_generation = self.public_link.parent / candidates[-1]["candidate"]
            self._build(job, identifier, public_generation)
            record = {"id": identifier, "source": sha256(data).hexdigest(), "candidates": candidates,
                      "base_revision": base_revision, "job": job.name, "path": public_path,
                      "title": draft["title"], "url": ORIGIN + public_path}
            write_json(self.pending, record)
            return record
        except Exception:
            if not self.pending.exists():
                for link, record in zip((self.article_link, self.public_link), candidates):
                    shutil.rmtree(link.parent / record["candidate"])
                shutil.rmtree(job)
            raise

    def _check_baselines(self, record):
        links = (self.article_link, self.public_link)
        for link, generation in zip(links, record["candidates"]):
            active = self._active(link)
            if active.name == generation["candidate"]:
                continue
            if active.name != generation["base"] or snapshot_digest(active) != generation["digest"]:
                raise PublishError("有未解决的发布且文章源已经变化，暂时不能覆盖源文件")

    @staticmethod
    def _replace_link(link, candidate):
        with tempfile.TemporaryDirectory(prefix=".publish-link-", dir=link.parent) as work:
            temporary = Path(work) / "current"
            temporary.symlink_to(candidate, target_is_directory=True)
            os.replace(temporary, link)
            sync_directory(link.parent)

    def _discard_stale(self, record, job):
        links = (self.article_link, self.public_link)
        if any(self._active(link).name == generation["candidate"]
               for link, generation in zip(links, record["candidates"])):
            raise PublishError("发布状态需要核对，已保留待处理记录")
        self.pending.unlink()
        sync_directory(self.private_dir)
        for link, generation in zip(links, record["candidates"]):
            shutil.rmtree(link.parent / generation["candidate"], ignore_errors=True)
        shutil.rmtree(job, ignore_errors=True)

    def publish(self, identifier, data):
        """Called under the owner's publication and article-mutation lock."""
        identifier = publication_id(identifier)
        draft = preview_draft(data)
        receipt = self._receipt(identifier)
        completed = self.publication(identifier)
        if completed is not None:
            old = json.loads(receipt.read_text(encoding="utf-8"))
            if old["source"] != sha256(data).hexdigest():
                raise PublishError("该发布编号已对应另一版草稿")
            return completed
        record = self._pending()
        if record and self._receipt(record["id"]).exists():
            self.pending.unlink()
            record = None
        if record:
            if record["id"] != identifier or record["source"] != sha256(data).hexdigest():
                raise PublishError("另一篇文章的发布结果待核对，请先重试原发布")
        else:
            record = self._stage(identifier, data, draft)
        job = self.private_dir / record["job"]
        self._check_baselines(record)
        try:
            result = self.remote.deploy(job / "release", identifier, record["path"], record["base_revision"])
        except StaleRevisionError:
            self._discard_stale(record, job)
            raise PublishError("线上博客版本已变更，请先同步发布底稿与线上版本") from None
        if not isinstance(result, dict) or result.get("url") != record["url"] or result.get("revision") != "publish-" + identifier:
            raise PublishError("ECS 返回的发布结果需要核对，请保留草稿后重试")
        self._check_baselines(record)
        for link, generation in zip((self.article_link, self.public_link), record["candidates"]):
            if self._active(link).name != generation["candidate"]:
                self._replace_link(link, generation["candidate"])
        write_json(self.public_revision, {"revision": result["revision"]})
        write_json(receipt, record)
        self.pending.unlink()
        sync_directory(self.private_dir)
        shutil.rmtree(job, ignore_errors=True)
        return {name: record[name] for name in ("title", "url")}
