"""Evidence-backed publisher identities, separate from article and project decisions."""

import re
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, model_validator

from .contracts import Page, StrictModel


class PublisherEvidence(StrictModel):
    """Record the primary page and exact wording supporting an institutional identity."""
    url: str = Field(min_length=8)
    quote: str = Field(min_length=2)


class PublisherRecord(StrictModel):
    """A reviewed publisher or platform, whose identity does not validate its articles."""
    publisher_id: str
    name: str
    aliases: list[str] = Field(default_factory=list)
    hosts: list[str] = Field(default_factory=list)
    source_type: Literal["official", "media", "platform"]
    evidence: list[PublisherEvidence] = Field(min_length=1)


class Affiliation(StrictModel):
    """Record why a publisher's site can carry an internal or same-group signature."""
    owner_id: str
    signatory_id: str
    relationship: Literal["internal_department", "subsidiary", "same_group"]
    evidence: list[PublisherEvidence] = Field(min_length=1)


class PublisherRegistry(StrictModel):
    """Freeze reviewed organizations and affiliations without event-specific allowlists."""
    version: Literal["publisher-registry-v1"]
    reviewed_at: str
    reviewer: str
    human_confirmed: bool = False
    records: list[PublisherRecord]
    affiliations: list[Affiliation] = Field(default_factory=list)

    @model_validator(mode="after")
    def unambiguous(self):
        """Reject overlapping aliases/hosts and affiliations to missing institutions."""
        ids = [record.publisher_id for record in self.records]
        aliases = [name for record in self.records for name in set([record.name] + record.aliases)]
        hosts = [host for record in self.records for host in record.hosts]
        if len(set(ids)) != len(ids) or len(set(aliases)) != len(aliases) or len(set(hosts)) != len(hosts):
            raise ValueError("publisher_registry_ambiguous")
        if any(host != host.lower() or "/" in host or ":" in host or not host for host in hosts):
            raise ValueError("publisher_host_invalid")
        if any(item.owner_id not in ids or item.signatory_id not in ids for item in self.affiliations):
            raise ValueError("publisher_affiliation_unknown")
        return self


def stated_origins(page: Page) -> list[dict[str, str]]:
    """Collect source labels from the body and separately preserved visible source controls."""
    origins = [{"name": row.name.strip(), "evidence": row.evidence, "locator": row.selector} for row in page.source_statements]
    for match in re.finditer(r"(?:^\s*(?:信息|文章)?来源\s*[:：]\s*|[（(|｜]\s*(?:信息|文章)?来源\s*[:：]\s*)([^\n）)]+)", page.text, flags=re.M):
        name = re.split(r"\s*(?:发布时间|发布日期|作者|编辑)\s*[:：]", match[1])[0].strip()
        if 1 < len(name) <= 100 and name not in {row["name"] for row in origins}:
            origins.append({"name": name, "evidence": match[0].strip(), "locator": "article_source_label"})
    expanded = []
    for origin in origins:
        fragments = re.split(r"[，,；;]", origin["name"])
        parsed = []
        for fragment in fragments:
            match = re.fullmatch(r"(.+?)(?:整理|编译)|(?:素材)?来源于(.+?)(?:官网|网站)?", fragment.strip())
            if match:
                parsed.append((match[1] or match[2]).strip())
        if len(fragments) > 1 and len(parsed) == len(fragments):
            expanded.extend(origin | {"name": name} for name in parsed)
        else:
            expanded.append(origin)
    return expanded


def verify_publisher(page: Page, registry: PublisherRegistry) -> dict:
    """Resolve publisher ownership and explicit attribution, keeping unresolved origins pending."""
    host = (urlsplit(page.final_url).hostname or "").lower()
    names = {name: record for record in registry.records for name in [record.name] + record.aliases}
    owner = next((record for record in registry.records if host in record.hosts), None)
    claims = stated_origins(page)
    result = {"host": host, "publisher": owner.name if owner else None, "source_type": owner.source_type if owner and owner.source_type != "platform" else "unknown", "is_reprint": False,
              "original_publisher": None, "evidence": [], "origin_chain": claims, "institution_evidence": [], "problem": None, "registry_version": registry.version}
    if not owner:
        result["problem"] = "publisher_not_registered"
        return result
    result["institution_evidence"] = [item.model_dump() for item in owner.evidence]
    result["evidence"] = [f"registered_host:{host}"]
    members = {item.signatory_id for item in registry.affiliations if item.owner_id == owner.publisher_id}
    originals = []
    unknown = []
    for claim in claims:
        institution = names.get(claim["name"])
        result["evidence"].append(claim["evidence"])
        if not institution:
            unknown.append(claim["name"])
            continue
        if institution.publisher_id in members:
            result["institution_evidence"] += [item.model_dump() for affiliation in registry.affiliations if affiliation.owner_id == owner.publisher_id and affiliation.signatory_id == institution.publisher_id for item in affiliation.evidence]
        elif institution.publisher_id != owner.publisher_id:
            originals.append((institution, claim["name"]))
    if unknown:
        result.update(is_reprint=True, original_publisher=" → ".join(claim["name"] for claim in claims), problem="original_publisher_unverified", unresolved_publishers=unknown)
        return result
    if owner.source_type == "platform":
        # A trusted hosting operator does not authenticate a user-uploaded source claim.
        result.update(is_reprint=True, original_publisher=" → ".join(claim["name"] for claim in claims) or None, problem="platform_original_article_needed")
        return result
    if originals:
        if any(item.source_type == "platform" for item, _ in originals):
            result["problem"] = "original_publisher_is_platform"
            return result
        if len({item.publisher_id for item, _ in originals}) > 1:
            result.update(is_reprint=True, problem="multiple_original_publishers")
            return result
        result.update(is_reprint=True, original_publisher=originals[0][1], original_publisher_canonical=originals[0][0].name)
        result["institution_evidence"] += [evidence.model_dump() for item, _ in originals for evidence in item.evidence]
    return result
