import asyncio
import hashlib
import ipaddress
import json
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlsplit, urlunsplit

import httpx

MAX_RESPONSE_BYTES = 2000000


def public_url(value):
    if (
        not isinstance(value, str)
        or len(value) > 2048
        or any(ord(char) < 32 for char in value)
    ):
        raise ValueError("Invalid source URL.")
    parsed = urlsplit(value)
    host = (parsed.hostname or "").lower().rstrip(".")
    if not re.fullmatch(r"[a-z0-9.-]+", host) or ".." in host or "[" in parsed.netloc:
        raise ValueError("Invalid public source hostname.")
    if (
        parsed.scheme not in ("http", "https")
        or not host
        or parsed.username
        or parsed.password
    ):
        raise ValueError("Only public web sources are allowed.")
    if (
        parsed.port not in (None, 80, 443)
        or "." not in host
        or host.endswith(
            (
                ".localhost",
                ".local",
                ".internal",
                ".lan",
                ".home",
                ".test",
                ".invalid",
                ".example",
            )
        )
    ):
        raise ValueError("Private or nonstandard source URL.")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address or re.fullmatch(
        r"(?:0x[0-9a-f]+|[0-9]+)(?:\.(?:0x[0-9a-f]+|[0-9]+)){0,3}", host
    ):
        raise ValueError("IP-literal source URLs are not accepted.")
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parsed.query, ""))


def readable_text(markdown):
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", markdown)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"(?m)^#{1,6}\s+", "", text)
    return text.replace("**", "").replace("`", "").strip()


def page_rejection_reason(item, text):
    metadata = item.get("metadata")
    metadata = metadata if isinstance(metadata, dict) else {}
    status = metadata.get("statusCode")
    if (
        isinstance(status, int)
        and not isinstance(status, bool)
        and 400 <= status <= 599
    ):
        return "http_error"
    if len(text.strip()) < 100:
        return "insufficient_text"
    if len(text) > 1600:
        return None
    heading = text.splitlines()[0].strip().lower()
    title = str(metadata.get("title") or item.get("title") or "").lower()
    body = text.lower()
    error_heading = re.fullmatch(
        r"(?:(?:error\s*)?(?:403|404|429|500|502|503|504)\s*[:\-–]?\s*)?"
        r"(?:access denied|forbidden|page not found|not found|service unavailable|"
        r"bad gateway|gateway timeout|internal server error|too many requests)[.!]?",
        heading,
    )
    error_detail = re.search(
        r"try again later|temporarily unavailable|requested (?:page|url)|"
        r"permission to access|request (?:id|blocked)|reference (?:id|#)|"
        r"server (?:error|load)|nginx|cloudflare",
        body,
    )
    if error_heading and error_detail:
        return "error_template"
    if heading.startswith("the server load is currently too high") and (
        "load average" in body or "server overload" in title
    ):
        return "overload_template"
    challenge_heading = re.fullmatch(
        r"(?:just a moment(?:\.{1,3}|…)?|verify (?:you are|you're) human[.!]?|"
        r"checking your browser(?:\.{1,3}|…)?|attention required!?\s*[|—-]?\s*cloudflare)",
        heading,
    )
    if challenge_heading and re.search(
        r"captcha|enable javascript|enable cookies|security (?:check|verification)|"
        r"verify (?:you are|you're) human|checking your browser",
        body,
    ):
        return "access_challenge"
    return None


def select_passages(text, query, limit=6000):
    if len(text) <= limit:
        return text
    ignored = {
        "the",
        "and",
        "can",
        "does",
        "with",
        "for",
        "that",
        "this",
        "are",
        "from",
        "official",
        "documentation",
        "when",
        "which",
        "what",
        "have",
    }
    terms = set(re.findall(r"[a-z][a-z0-9_-]{2,}", query.lower())) - ignored
    lead = min(2200, limit // 2)
    windows = [
        (offset, text[offset : offset + 1100])
        for offset in range(lead, min(len(text), 200000), 900)
    ]
    ranked = sorted(
        windows,
        key=lambda item: sum(min(item[1].lower().count(term), 3) for term in terms),
        reverse=True,
    )
    chosen = [(0, text[:lead]), *sorted(ranked[:3], key=lambda item: item[0])]
    return "\n\n[Excerpt boundary]\n\n".join(part for _, part in chosen)[:limit]


def is_discussion(url):
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower()
    return (
        host in {"news.ycombinator.com", "stackoverflow.com", "reddit.com"}
        or host.endswith((".stackexchange.com", ".reddit.com"))
        or (host == "github.com" and "/issues/" in parsed.path)
    )


@dataclass(frozen=True)
class SourceDocument:
    identifier: str
    url: str
    title: str
    text: str
    retrieved_at: str
    content_hash: str

    def metadata(self):
        return {
            "id": self.identifier,
            "url": self.url,
            "title": self.title,
            "retrieved_at": self.retrieved_at,
            "content_hash": self.content_hash,
            "excerpt_chars": len(self.text),
            "kind": "discussion" if is_discussion(self.url) else "webpage",
        }


class FirecrawlSearch:
    def __init__(self, transport=None):
        self.key = os.environ.get("FIRECRAWL_API_KEY", "")
        self.transport = transport

    def ready(self):
        return bool(self.key)

    async def search(self, question):
        if not self.ready():
            raise RuntimeError("Search is not configured.")
        if not isinstance(question, str) or not 5 <= len(question) <= 240:
            raise ValueError("Invalid verification question.")
        if re.search(
            r"(?:fc-[a-f0-9]{20,}|sk-[A-Za-z0-9_-]{20,}|ghp_[A-Za-z0-9]+|rpa_[A-Za-z0-9]+)",
            question,
        ):
            raise ValueError(
                "Potential credential in search query; edit the question first."
            )
        query = question.rstrip("?") + " official documentation"
        async with asyncio.timeout(40):
            async with (
                httpx.AsyncClient(
                    timeout=35, follow_redirects=False, transport=self.transport
                ) as connection,
                connection.stream(
                    "POST",
                    "https://api.firecrawl.dev/v2/search",
                    headers={"Authorization": f"Bearer {self.key}"},
                    json={
                        "query": query,
                        "limit": 3,
                        "scrapeOptions": {
                            "formats": ["markdown"],
                            "onlyMainContent": True,
                            "maxAge": 3600000,
                            "timeout": 20000,
                            "skipTlsVerification": False,
                        },
                    },
                ) as response,
            ):
                if response.status_code != 200:
                    raise RuntimeError(f"Search service HTTP {response.status_code}.")
                chunks = []
                size = 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > MAX_RESPONSE_BYTES:
                        raise ValueError("Search response exceeds the size limit.")
                    chunks.append(chunk)
                body = json.loads(b"".join(chunks))
        if (
            not isinstance(body, dict)
            or body.get("success") is not True
            or not isinstance(body.get("data"), dict)
        ):
            raise RuntimeError("Search did not return a valid result.")
        web = body["data"].get("web", [])
        if not isinstance(web, list):
            raise TypeError("Invalid search result list.")
        documents = []
        seen = set()
        for item in web[:3]:
            if not isinstance(item, dict):
                continue
            try:
                url = public_url(item.get("url"))
            except ValueError:
                continue
            markdown = item.get("markdown")
            if (
                url in seen
                or not isinstance(markdown, str)
                or len(markdown.strip()) < 100
            ):
                continue
            readable = readable_text(markdown[:200000])
            if page_rejection_reason(item, readable):
                continue
            seen.add(url)
            text = select_passages(readable, question)
            documents.append(
                SourceDocument(
                    identifier=f"S{len(documents) + 1}",
                    url=url,
                    title=str(item.get("title") or urlsplit(url).hostname)[:240],
                    text=text,
                    retrieved_at=datetime.now(UTC).isoformat(),
                    content_hash=hashlib.sha256(text.encode()).hexdigest(),
                )
            )
        return query, documents
