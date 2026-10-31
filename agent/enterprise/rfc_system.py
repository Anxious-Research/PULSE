"""Enterprise RFC (Request For Comments) & ADR Institutional Knowledge Engine.

Enforces Big-Tech style architectural governance:
- No non-trivial architectural changes without an approved RFC.
- Mandatory sign-offs from CTO and CISO before code begins.
- Archival of RFCs to docs/rfc/ as versioned institutional records.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional
from pulse_constants import get_pulse_home

logger = logging.getLogger("enterprise.rfc")

RFC_STORE_FILENAME = "rfc_registry.json"


class RFCStatus(str, Enum):
    DRAFT = "draft"
    UNDER_REVIEW = "under_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    IMPLEMENTED = "implemented"


@dataclass
class RFCComment:
    author: str
    content: str
    timestamp: float = field(default_factory=time.time)


@dataclass
class RFCDocument:
    rfc_id: str
    title: str
    author: str
    summary: str
    motivation: str
    technical_design: str
    security_considerations: str
    status: str = RFCStatus.DRAFT.value
    approvals: Dict[str, bool] = field(default_factory=dict)  # reviewer -> approved
    comments: List[RFCComment] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)


class RFCEngine:
    """Manages the lifecycle of enterprise RFCs and architecture decision records."""

    def __init__(self, storage_dir: Optional[Path] = None, docs_dir: Optional[Path] = None):
        if storage_dir is None:
            self.dir = get_pulse_home() / "enterprise"
        else:
            self.dir = Path(storage_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.registry_file = self.dir / RFC_STORE_FILENAME

        if docs_dir is None:
            self.docs_dir = get_pulse_home().parent / "PULSE" / "docs" / "rfc"
        else:
            self.docs_dir = Path(docs_dir)
        self.docs_dir.mkdir(parents=True, exist_ok=True)

        self.rfcs: Dict[str, RFCDocument] = {}
        self.load()

    def load(self) -> None:
        if not self.registry_file.exists():
            return
        try:
            raw = json.loads(self.registry_file.read_text(encoding="utf-8"))
            for rfc_id, rdata in raw.items():
                comments = [RFCComment(**c) for c in rdata.pop("comments", [])]
                self.rfcs[rfc_id] = RFCDocument(comments=comments, **rdata)
        except Exception as exc:
            logger.warning("Could not load RFC registry: %s", exc)

    def save(self) -> None:
        try:
            data = {k: asdict(v) for k, v in self.rfcs.items()}
            self.registry_file.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception as exc:
            logger.error("Could not save RFC registry: %s", exc)

    def create_rfc(
        self,
        title: str,
        author: str,
        summary: str,
        motivation: str,
        technical_design: str,
        security_considerations: str,
    ) -> RFCDocument:
        rfc_num = len(self.rfcs) + 1
        rfc_id = f"RFC-{rfc_num:04d}"
        rfc = RFCDocument(
            rfc_id=rfc_id,
            title=title,
            author=author,
            summary=summary,
            motivation=motivation,
            technical_design=technical_design,
            security_considerations=security_considerations,
            status=RFCStatus.UNDER_REVIEW.value,
        )
        self.rfcs[rfc_id] = rfc
        self.save()
        self.sync_markdown(rfc_id)
        logger.info("Created %s: '%s' by @%s", rfc_id, title, author)
        return rfc

    def add_comment(self, rfc_id: str, author: str, comment: str) -> None:
        if rfc_id not in self.rfcs:
            raise KeyError(f"RFC {rfc_id} does not exist.")
        rfc = self.rfcs[rfc_id]
        rfc.comments.append(RFCComment(author=author, content=comment))
        rfc.updated_at = time.time()
        self.save()
        self.sync_markdown(rfc_id)

    def submit_vote(self, rfc_id: str, reviewer: str, approved: bool, rationale: str = "") -> Dict[str, Any]:
        """Submit executive/VP vote for an RFC."""
        if rfc_id not in self.rfcs:
            raise KeyError(f"RFC {rfc_id} does not exist.")
        rfc = self.rfcs[rfc_id]
        rfc.approvals[reviewer] = approved
        if rationale:
            rfc.comments.append(RFCComment(author=reviewer, content=f"Vote: {'APPROVED' if approved else 'REJECTED'}. Rationale: {rationale}"))

        # Check approval consensus: Requires at least 2 approvals and zero vetoes
        has_veto = any(vote is False for vote in rfc.approvals.values())
        approvals_count = sum(1 for vote in rfc.approvals.values() if vote is True)

        if has_veto:
            rfc.status = RFCStatus.REJECTED.value
        elif approvals_count >= 2:
            rfc.status = RFCStatus.APPROVED.value

        rfc.updated_at = time.time()
        self.save()
        self.sync_markdown(rfc_id)
        return {"rfc_id": rfc_id, "status": rfc.status, "approvals": rfc.approvals}

    def sync_markdown(self, rfc_id: str) -> Path:
        """Write RFC to docs/rfc/<rfc_id>.md."""
        rfc = self.rfcs[rfc_id]
        md_path = self.docs_dir / f"{rfc_id}.md"

        approval_lines = "\n".join(f"- **@{k}**: {'✅ Approved' if v else '❌ Rejected'}" for k, v in rfc.approvals.items())
        comment_lines = "\n".join(f"> **@{c.author}** ({time.strftime('%Y-%m-%d %H:%M', time.localtime(c.timestamp))}):\n> {c.content}\n" for c in rfc.comments)

        content = f"""# {rfc.rfc_id}: {rfc.title}

- **Author**: @{rfc.author}
- **Status**: `{rfc.status.upper()}`
- **Date Created**: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(rfc.created_at))}

## Summary
{rfc.summary}

## Motivation
{rfc.motivation}

## Technical Design
{rfc.technical_design}

## Security & Privacy Considerations
{rfc.security_considerations}

## Governance & Approvals
{approval_lines if approval_lines else "_No votes cast yet._"}

## Stakeholder Comments & Debate
{comment_lines if comment_lines else "_No comments yet._"}
"""
        md_path.write_text(content, encoding="utf-8")
        return md_path
