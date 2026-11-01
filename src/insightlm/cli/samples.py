"""`insightlm-samples`: write sample CVs for trying the product by hand.

    insightlm-samples            # into ./samples

All people and companies are invented. The attacked CVs are the same documents the
red-team corpus measures, so what you see in the UI is what the numbers describe.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pymupdf

from insightlm.evaluation.adapters.cohort import build_cohort
from insightlm.evaluation.adapters.redteam import build_redteam_v1

_CVS: dict[str, list[str]] = {
    "01-asha-rao-strong.pdf": [
        "Asha Rao",
        "Senior Backend Engineer",
        "",
        "EXPERIENCE",
        "Payments platform at a fintech, 2019 to 2025.",
        "Built settlement and reconciliation services in Python and PostgreSQL,",
        "processing card payments for twelve thousand merchants.",
        "Designed idempotent retries and a double-entry ledger.",
        "Operated Kubernetes clusters in production across three regions.",
        "Led a team of four engineers through the ledger migration.",
        "",
        "EDUCATION",
        "B.Tech Computer Science, 2019.",
    ],
    "02-ben-okafor-weak.pdf": [
        "Ben Okafor",
        "Frontend Developer",
        "",
        "EXPERIENCE",
        "Agency frontend developer, 2021 to 2025.",
        "Built marketing sites and dashboards in React and TypeScript.",
        "Worked with designers in Figma on component libraries.",
        "Some scripting in Python for build tooling.",
        "Has never used Kubernetes; deployments were handled by the platform team.",
        "",
        "EDUCATION",
        "BSc Media Studies, 2021.",
    ],
    "03-meera-iyer-paraphrase.pdf": [
        "Meera Iyer",
        "Platform Engineer",
        "",
        "EXPERIENCE",
        "Ran our EKS clusters and wrote the Helm charts every service shipped with.",
        "Wrote Django and FastAPI services behind the public API.",
        "Built the card-acquiring integration with Visa and Mastercard, including chargebacks.",
        "",
        "EDUCATION",
        "MCA, 2020.",
    ],
}

_ATTACKS = {
    "a-white-claim": "10-jordan-ellis-hidden-white-claim.pdf",
    "a-micro_1pt-claim": "11-jordan-ellis-hidden-1pt-claim.pdf",
    "a-visible_tag_injection": "12-jordan-ellis-tag-injection.pdf",
    "a-visible_instruction": "13-jordan-ellis-visible-instruction.pdf",
    "c-plain": "14-jordan-ellis-clean.pdf",
    "c-banner_white_on_dark": "15-honest-white-on-dark-banner.pdf",
}


def _cv(lines: list[str]) -> bytes:
    doc = pymupdf.open()
    page = doc.new_page()
    for index, line in enumerate(lines):
        if line:
            size = 18 if index == 0 else (12 if line.isupper() else 10.5)
            page.insert_text((72, 80 + index * 18), line, fontsize=size)
    payload: bytes = doc.tobytes(garbage=4, deflate=True)
    doc.close()
    return payload


def _scan(pdf: bytes) -> bytes:
    """The same CV as a flatbed scan: one greyscale image per page, no text layer."""
    source = pymupdf.open(stream=pdf, filetype="pdf")
    doc = pymupdf.open()
    for number in range(source.page_count):
        original = source.load_page(number)
        page = doc.new_page(width=original.rect.width, height=original.rect.height)
        page.insert_image(page.rect, pixmap=original.get_pixmap(dpi=150, colorspace="GRAY"))
    payload: bytes = doc.tobytes(garbage=4, deflate=True)
    doc.close()
    source.close()
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="insightlm-samples", description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("samples"))
    parser.add_argument(
        "--cohort", type=int, default=0, help="Also write N synthetic CVs to <out>/cohort."
    )
    args = parser.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)

    for name, lines in _CVS.items():
        (args.out / name).write_bytes(_cv(lines))
    (args.out / "04-asha-rao-scanned.pdf").write_bytes(_scan(_cv(_CVS["01-asha-rao-strong.pdf"])))
    for doc in build_redteam_v1():
        if doc.id in _ATTACKS:
            (args.out / _ATTACKS[doc.id]).write_bytes(doc.pdf)

    if args.cohort:
        cohort = args.out / "cohort"
        cohort.mkdir(exist_ok=True)
        for applicant in build_cohort(args.cohort):
            (cohort / applicant.filename).write_bytes(applicant.pdf)
        print(f"{args.cohort} cohort CVs in {cohort}")

    for path in sorted(args.out.glob("*.pdf")):
        print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
