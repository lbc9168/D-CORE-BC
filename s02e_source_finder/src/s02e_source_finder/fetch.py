"""Bounded public-page fetching and Chinese article extraction for the prototype."""

import hashlib
import ipaddress
import logging
import re
import socket
import time
from datetime import datetime, timezone
from io import BytesIO
from urllib.parse import urljoin, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup
from pypdf import PdfReader
from pypdf.errors import PdfReadError

from .contracts import Page, SourceStatement

logger = logging.getLogger(__name__)
MAX_BYTES = 8 * 1024 * 1024
MAX_TEXT = 45000
_LOCAL_SUFFIXES = (".localhost", ".local", ".internal", ".test", ".invalid", ".example")


def public_url(url: str, *, resolve: bool = True, dns_mode: str = "system") -> str:
    """Reject credentials, non-web schemes, Goalfore and non-public network destinations."""
    if any(ord(char) < 33 for char in url) or "\\" in url:
        raise ValueError("invalid_url")
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme not in {"http", "https"} or not host or parsed.username is not None or parsed.password is not None:
        raise ValueError("unsafe_url")
    if host == "goalfore.cn" or host.endswith(".goalfore.cn"):
        raise ValueError("goalfore_not_external")
    if host == "localhost" or host.endswith(_LOCAL_SUFFIXES) or "." not in host:
        raise ValueError("non_public_host")
    if parsed.port not in {None, 80, 443}:
        raise ValueError("unsupported_port")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        raise ValueError("non_public_address")
    if resolve:
        if dns_mode == "https":
            addresses = public_dns_addresses(host)
        else:
            addresses = [item[4][0] for item in socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)]
        if not addresses or any(not ipaddress.ip_address(item).is_global for item in addresses):
            raise ValueError("non_public_dns")
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parsed.query, ""))


def public_dns_addresses(host: str) -> list[str]:
    """Verify public DNS over authenticated HTTPS for trusted local Fake-IP proxies."""
    # This is explicit, never an automatic bypass after a private-address result.
    # Literal/private URLs remain rejected before this function is reached.
    with requests.get("https://dns.google/resolve", params={"name": host, "type": "A"}, timeout=(10, 20)) as response:
        if response.status_code != 200:
            raise ValueError("public_dns_unavailable")
        payload = response.json()
    if not isinstance(payload, dict) or payload.get("Status") != 0 or not isinstance(payload.get("Answer"), list):
        raise ValueError("public_dns_unavailable")
    addresses = []
    for answer in payload["Answer"]:
        if not isinstance(answer, dict):
            raise ValueError("public_dns_invalid_response")
        if answer.get("type") == 1:
            addresses.append(str(ipaddress.ip_address(answer.get("data", ""))))
    if not addresses:
        raise ValueError("public_dns_no_address")
    return addresses


def parse_content(body: bytes, content_type: str, encoding: str | None) -> tuple[str, str, str | None, str]:
    """Extract title, publisher hint, date and article text without executing page scripts."""
    if "pdf" in content_type or body.startswith(b"%PDF"):
        reader = PdfReader(BytesIO(body))
        if reader.is_encrypted or len(reader.pages) > 60:
            raise ValueError("pdf_not_readable_or_too_long")
        text = "\n".join(page.extract_text() or "" for page in reader.pages).strip()
        title = str((reader.metadata or {}).get("/Title") or "").strip()
        return title or next((line for line in text.splitlines() if line.strip()), ""), "", None, text
    if "html" not in content_type and "text/" not in content_type:
        raise ValueError("unsupported_content_type")
    soup = BeautifulSoup(body, "html.parser", from_encoding=encoding)
    title_tag = soup.find("meta", attrs={"property": "og:title"})
    title = str(title_tag.get("content", "")) if title_tag else ""
    if not title:
        # Some publisher templates contain an empty h1; continue to the real title.
        for title_node in (soup.find("h1"), soup.title):
            title = title_node.get_text(" ", strip=True) if title_node else ""
            if title:
                break
    site_tag = soup.find("meta", attrs={"property": "og:site_name"})
    site_name = str(site_tag.get("content", "")) if site_tag else (soup.title.get_text(" ", strip=True) if soup.title else "")
    date_tag = soup.find("meta", attrs={"property": "article:published_time"}) or soup.find("meta", attrs={"name": "PubDate"})
    publish_date = str(date_tag.get("content", "")) if date_tag else None
    for node in soup.select("script, style, nav, header, footer, noscript, iframe, form"):
        node.decompose()
    candidates = []
    for selector in (".TRS_Editor", "#artibody", ".post_body", ".article-content", "#article-content", ".article_content", ".content-detail", "article", "main"):
        candidates = [node for node in soup.select(selector) if len(node.get_text(strip=True)) >= 50]
        if candidates:
            break
    if candidates:
        node = max(candidates, key=lambda el: len(el.get_text(strip=True)))
    else:
        node = soup.body or soup
    text = node.get_text("\n", strip=True)
    return title.strip(), site_name.strip(), publish_date, text


def publisher_identity_text(body: bytes, content_type: str, encoding: str | None) -> str:
    """Keep bounded visible site ownership labels without mixing them into project facts."""
    if "html" not in content_type:
        return ""
    soup = BeautifulSoup(body, "html.parser", from_encoding=encoding)
    values = []
    for node in soup.select("header, footer, .copyright, #copyright, .footer, #footer"):
        value = node.get_text(" ", strip=True)
        if value and value not in values:
            values.append(value[:1000])
    return "\n".join(values)[:4000]


def source_statements(body: bytes, content_type: str, encoding: str | None) -> list[SourceStatement]:
    """Retain known visible publisher labels that article-only extraction would discard."""
    if "html" not in content_type:
        return []
    soup = BeautifulSoup(body, "html.parser", from_encoding=encoding)
    statements = []
    for selector in (".date-source .source", ".post_info .source"):
        for node in soup.select(selector):
            name = node.get_text(" ", strip=True)
            if name and len(name) <= 160 and name not in {row.name for row in statements}:
                statements.append(SourceStatement(name=name, evidence=name, selector=selector, link=node.get("href")))
    return statements


class PublicFetcher:
    """Apply finite retry, redirect and response-size limits for one sample run."""

    def __init__(self, *, session=None, sleeper=time.sleep, dns_mode: str = "system", allow_english: bool = False):
        if dns_mode not in {"system", "https"}:
            raise ValueError("unsupported_dns_mode")
        self.dns_mode = dns_mode
        self.allow_english = allow_english
        self.session = session or requests.Session()
        if session is None:
            self.session.trust_env = False
            self.session.headers.update({"User-Agent": "BRI-Reference-Check/0.1 (+public article verification)"})
        self.sleeper = sleeper
        self.blocked_hosts: set[str] = set()

    def fetch(self, url: str) -> Page:
        """Return an explicit readable, rejected or deferred page result; never infer from snippets."""
        page = Page(url=url, final_url=url, checked_at=datetime.now(timezone.utc).isoformat())
        current = url
        try:
            for redirect in range(6):
                current = public_url(current, dns_mode=self.dns_mode)
                page.final_url = current
                host = urlsplit(current).hostname
                if host in self.blocked_hosts:
                    page.problem = "site_blocked_this_run"
                    return page
                for attempt in range(3):
                    try:
                        response = self.session.get(current, timeout=(10, 30), allow_redirects=False, stream=True)
                    except requests.RequestException as exc:
                        logger.warning("fetch_failed host=%s attempt=%s type=%s", host, attempt, type(exc).__name__)
                        if attempt == 2:
                            page.problem = "network_temporarily_unavailable"
                            return page
                        self.sleeper(2 ** attempt)
                        continue
                    with response:
                        page.http_status = response.status_code
                        if response.status_code == 403:
                            self.blocked_hosts.add(host)
                            page.problem = "http_403"
                            return page
                        if response.status_code == 429 or 500 <= response.status_code < 600:
                            if attempt == 2:
                                page.problem = f"http_{response.status_code}"
                                return page
                            retry_after = response.headers.get("Retry-After", "30")
                            # A longer or date-based server delay is deferred to a later run.
                            if response.status_code == 429 and (not retry_after.isdigit() or int(retry_after) > 60):
                                page.problem = "rate_limited_deferred"
                                return page
                            self.sleeper(max(30, int(retry_after)) if response.status_code == 429 else 2 ** attempt)
                            continue
                        if response.status_code in {301, 302, 303, 307, 308}:
                            location = response.headers.get("Location")
                            if not location:
                                page.problem = "redirect_without_location"
                                return page
                            target = public_url(urljoin(current, location), dns_mode=self.dns_mode)
                            if urlsplit(current).path not in {"", "/"} and urlsplit(target).path in {"", "/"}:
                                page.problem = "redirect_to_homepage"
                                return page
                            current = target
                            break
                        if response.status_code != 200:
                            page.problem = f"http_{response.status_code}"
                            return page
                        chunks = []
                        size = 0
                        for chunk in response.iter_content(65536):
                            size += len(chunk)
                            if size > MAX_BYTES:
                                page.problem = "response_too_large"
                                return page
                            chunks.append(chunk)
                        content_type = response.headers.get("Content-Type", "").lower()
                        encoding = response.encoding if "charset=" in content_type else None
                        payload = b"".join(chunks)
                        page.title, page.site_name, page.publish_date, page.text = parse_content(payload, content_type, encoding)
                        page.source_statements = source_statements(payload, content_type, encoding)
                        page.identity_text = publisher_identity_text(payload, content_type, encoding)
                        page.text_hash = hashlib.sha256(page.text.encode("utf-8")).hexdigest()
                        if re.search(r"404|页面不存在|网页不存在|访问验证|人机验证|验证码|access denied|page not found|sign in|登录", page.title, re.I):
                            page.problem = "error_or_access_page"
                        elif len(page.text.strip()) < 50 or not page.title or (len(page.text.splitlines()) > 1 and len(set(page.text.splitlines())) == 1):
                            page.problem = "insufficient_content"
                        elif len(page.text) > MAX_TEXT:
                            page.problem = "article_too_long_for_sample"
                        elif not self.allow_english and len(re.findall(r"[\u3400-\u9fff]", page.text)) < 20:
                            page.problem = "not_chinese"
                        return page
                else:
                    page.problem = "request_attempts_exhausted"
                    return page
            page.problem = "too_many_redirects"
        except (ValueError, socket.gaierror, PdfReadError, OSError) as exc:
            logger.warning("page_unusable url=%s type=%s", page.final_url, type(exc).__name__)
            page.problem = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
        return page

    def close(self) -> None:
        """Close the local HTTP session after the sample run."""
        self.session.close()
