from __future__ import annotations

import re
from typing import Literal

import pymupdf
from pydantic import Field

from ekb.domain.models import Model

CSF_PARSER_VERSION = "csf-core-pdf-v1"
_MARKER = re.compile(
    r"(?P<function_label>[A-Z][A-Z ]+) \((?P<function>[A-Z]{2})\):"
    r"|[\u2022\u00b7]\s+(?P<category_label>.*?) \((?P<category>[A-Z]{2}\.[A-Z]{2})\):"
    r"|\bo\s+(?P<subcategory>[A-Z]{2}\.[A-Z]{2}-\d{2}):"
)


class CsfPassage(Model):
    pdf_page: int = Field(gt=0)
    printed_page: int = Field(ge=0)
    excerpt: str = Field(min_length=1)


class CsfNode(Model):
    id: str
    level: Literal["Function", "Category", "Subcategory"]
    parent_id: str | None = None
    label: str | None = None
    passages: tuple[CsfPassage, ...] = Field(min_length=1)

    @property
    def text(self) -> str:
        return " ".join(passage.excerpt for passage in self.passages)


def parse_csf_pdf(content: bytes) -> tuple[CsfNode, ...]:
    """Preserve native Appendix A identities without assigning EKB ontology classes."""
    nodes: list[CsfNode] = []
    in_core = False
    with pymupdf.open(stream=content, filetype="pdf") as document:
        for page_index in range(len(document)):
            page = document[page_index]
            raw = str(page.get_text("text", sort=False))
            if re.search(r"Appendix\s+B\.", raw):
                if in_core:
                    break
            if re.search(r"(?m)^Appendix\s+A\.\s+CSF Core\s*$", raw):
                in_core = True
            if not in_core:
                continue
            lines = [line.strip() for line in raw.splitlines() if line.strip()]
            page_numbers = [int(line) for line in lines if line.isdigit()]
            if len(page_numbers) != 1:
                raise ValueError("CSF Core page must have an unambiguous printed page")
            text = " ".join(
                line
                for line in lines
                if not line.isdigit()
                and not line.startswith(("NIST CSWP ", "The NIST Cybersecurity Framework"))
                and not re.fullmatch(r"[A-Z][a-z]+ \d{1,2}, \d{4}", line)
            )
            markers = list(_MARKER.finditer(text))
            leading = text[: markers[0].start()] if markers else text
            if nodes and leading.strip():
                continuation = CsfPassage(
                    pdf_page=page_index + 1,
                    printed_page=page_numbers[0],
                    excerpt=leading.strip(),
                )
                nodes[-1] = nodes[-1].model_copy(
                    update={"passages": (*nodes[-1].passages, continuation)}
                )
            for index, marker in enumerate(markers):
                end = markers[index + 1].start() if index + 1 < len(markers) else len(text)
                level: Literal["Function", "Category", "Subcategory"]
                if marker["function"]:
                    ident = marker["function"]
                    level = "Function"
                    parent_id = None
                    label = marker["function_label"].title()
                    start = marker.start()
                elif marker["category"]:
                    ident = marker["category"]
                    level = "Category"
                    parent_id = ident.split(".")[0]
                    label = marker["category_label"]
                    start = marker.start("category_label")
                else:
                    ident = marker["subcategory"]
                    level = "Subcategory"
                    parent_id = ident.split("-")[0]
                    label = None
                    start = marker.start("subcategory")
                nodes.append(
                    CsfNode(
                        id=ident,
                        level=level,
                        parent_id=parent_id,
                        label=label,
                        passages=(
                            CsfPassage(
                                pdf_page=page_index + 1,
                                printed_page=page_numbers[0],
                                excerpt=text[start:end].strip(),
                            ),
                        ),
                    )
                )
    ids = {node.id for node in nodes}
    if not nodes or len(ids) != len(nodes):
        raise ValueError("CSF Core identities are missing or duplicated")
    if any(node.parent_id and node.parent_id not in ids for node in nodes):
        raise ValueError("CSF Core hierarchy has an unresolved parent")
    return tuple(nodes)
