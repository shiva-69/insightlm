"""Labelled cases for the criterion judge: `judge-v1`.

Written by hand, one failure mode per group. Every passage is invented, and so is every
person in them. Each case sets the criterion against one passage that bears on it and,
usually, one that doesn't, so citing the wrong passage shows up as an error.
"""

from __future__ import annotations

from insightlm.evaluation.domain.judging import CaseKind, JudgeCase
from insightlm.recruiting.domain.assessment import Verdict

S, A, W, X = Verdict.STRONG, Verdict.ADEQUATE, Verdict.WEAK, Verdict.ABSENT
SUPPORTED = frozenset({S, A})
PARTIAL = frozenset({W, A})
NONE = frozenset({X})

FILLER = "Enjoys long-distance running and volunteers at a local animal shelter."

JUDGE_V1: tuple[JudgeCase, ...] = (
    # -- direct: the words and the meaning line up --------------------------------------
    JudgeCase(
        "d1",
        CaseKind.DIRECT,
        "Kubernetes in production",
        (FILLER, "Operated Kubernetes clusters in production across three regions for four years."),
        SUPPORTED,
        frozenset({"C2"}),
    ),
    JudgeCase(
        "d2",
        CaseKind.DIRECT,
        "Python backend services",
        ("Built and ran backend services in Python serving 40k requests per second.", FILLER),
        SUPPORTED,
        frozenset({"C1"}),
    ),
    JudgeCase(
        "d3",
        CaseKind.DIRECT,
        "Payment reconciliation",
        (
            FILLER,
            "Owned daily payment reconciliation between our ledger and three acquiring banks.",
        ),
        SUPPORTED,
        frozenset({"C2"}),
    ),
    JudgeCase(
        "d4",
        CaseKind.DIRECT,
        "Team leadership",
        ("Led a team of six engineers through two product launches.",),
        SUPPORTED,
        frozenset({"C1"}),
    ),
    JudgeCase(
        "d5",
        CaseKind.DIRECT,
        "PostgreSQL performance tuning",
        ("Tuned PostgreSQL performance: rewrote slow queries and added partial indexes.", FILLER),
        SUPPORTED,
        frozenset({"C1"}),
    ),
    # -- paraphrase: supported, in other words ------------------------------------------
    JudgeCase(
        "p1",
        CaseKind.PARAPHRASE,
        "Kubernetes in production",
        (FILLER, "Ran our EKS clusters and wrote the Helm charts every service shipped with."),
        SUPPORTED,
        frozenset({"C2"}),
    ),
    JudgeCase(
        "p2",
        CaseKind.PARAPHRASE,
        "Payment processing",
        ("Built the card-acquiring integration with Visa and Mastercard, including chargebacks.",),
        SUPPORTED,
        frozenset({"C1"}),
    ),
    JudgeCase(
        "p3",
        CaseKind.PARAPHRASE,
        "Python backend services",
        ("Wrote Django and FastAPI services behind our public API.", FILLER),
        SUPPORTED,
        frozenset({"C1"}),
    ),
    JudgeCase(
        "p4",
        CaseKind.PARAPHRASE,
        "Team leadership",
        (FILLER, "Managed four engineers, ran their one-to-ones and hired two more."),
        SUPPORTED,
        frozenset({"C2"}),
    ),
    JudgeCase(
        "p5",
        CaseKind.PARAPHRASE,
        "Mobile app development",
        ("Shipped the iOS and Android apps in Swift and Kotlin to 200k users.",),
        SUPPORTED,
        frozenset({"C1"}),
    ),
    # -- mention: the topic, but only in passing ----------------------------------------
    JudgeCase(
        "m1",
        CaseKind.MENTION,
        "Kubernetes in production",
        ("Completed an online course introducing Kubernetes concepts.", FILLER),
        frozenset({W}),
        frozenset({"C1"}),
    ),
    JudgeCase(
        "m2",
        CaseKind.MENTION,
        "Payment processing",
        (FILLER, "Sat in on design reviews for the payments team as an observer."),
        PARTIAL | NONE,
        frozenset({"C2"}),
    ),
    JudgeCase(
        "m3",
        CaseKind.MENTION,
        "Python backend services",
        ("Some scripting in Python for build tooling.",),
        frozenset({W}),
        frozenset({"C1"}),
    ),
    JudgeCase(
        "m4",
        CaseKind.MENTION,
        "Team leadership",
        ("Occasionally onboarded new joiners to the codebase.",),
        PARTIAL,
        frozenset({"C1"}),
    ),
    # -- negation: the words are there, the claim is denied -----------------------------
    JudgeCase(
        "n1",
        CaseKind.NEGATION,
        "Kubernetes in production",
        ("Has never used Kubernetes; deployments were on plain virtual machines.", FILLER),
        NONE,
    ),
    JudgeCase(
        "n2",
        CaseKind.NEGATION,
        "Python backend services",
        ("No professional Python experience. Backend work was all in Java.",),
        NONE,
    ),
    JudgeCase(
        "n3",
        CaseKind.NEGATION,
        "Payment processing",
        (FILLER, "Worked next to the payments team but did not work on payment processing."),
        NONE,
    ),
    JudgeCase(
        "n4",
        CaseKind.NEGATION,
        "Built a settlement engine",
        ("Built the reporting layer, though not the settlement engine itself.",),
        frozenset({X, W}),
        frozenset({"C1"}),
    ),
    JudgeCase(
        "n5",
        CaseKind.NEGATION,
        "Team leadership",
        ("Individual contributor throughout; has not managed or led a team.",),
        NONE,
    ),
    # -- unrelated: nothing to find --------------------------------------------------------
    JudgeCase(
        "u1",
        CaseKind.UNRELATED,
        "Kubernetes in production",
        ("Produced brand identities and posters for small clients.", FILLER),
        NONE,
    ),
    JudgeCase(
        "u2",
        CaseKind.UNRELATED,
        "Payment processing",
        ("Taught secondary-school chemistry for five years.",),
        NONE,
    ),
    JudgeCase(
        "u3",
        CaseKind.UNRELATED,
        "Python backend services",
        ("Designed marketing sites in Figma and Webflow.", FILLER),
        NONE,
    ),
    JudgeCase(
        "u4",
        CaseKind.UNRELATED,
        "Mobile app development",
        ("Managed the office move and vendor contracts.",),
        NONE,
    ),
)


# -- judge-v2: harder, because v1 saturated at 100% and stopped telling prompts apart ----

LONG_FILLER = (
    "Joined a mid-size retail company as its third engineer. Wrote onboarding guides, "
    "ran the weekly demo, and organised the internal hack day. Maintained the office "
    "wiki and helped the design team pick a component library."
)

JUDGE_V2: tuple[JudgeCase, ...] = (
    JudgeCase(
        "h1",
        CaseKind.DIRECT,
        "Payment reconciliation",
        (
            LONG_FILLER + " Separately, ran the payments reconciliation service in "
            "production for two years, owning its on-call.",
            FILLER,
        ),
        SUPPORTED,
        frozenset({"C1"}),
    ),
    JudgeCase(
        "h2",
        CaseKind.MENTION,
        "Kubernetes in production",
        (
            "Led planning for the Kubernetes migration, then handed delivery to the "
            "platform team before it went live.",
        ),
        PARTIAL,
        frozenset({"C1"}),
    ),
    JudgeCase(
        "h3",
        CaseKind.NEGATION,
        "Payment processing",
        ("My team built the payment gateway; I worked on the admin dashboard.",),
        frozenset({X, W}),
        frozenset({"C1"}),
    ),
    JudgeCase(
        "h4",
        CaseKind.NEGATION,
        "Kubernetes in production",
        ("Planning to learn Kubernetes next quarter.", FILLER),
        NONE,
    ),
    JudgeCase(
        "h5",
        CaseKind.NEGATION,
        "Python backend services",
        ("Used Python only for small scripts; every backend service was written in Go.",),
        frozenset({X, W}),
        frozenset({"C1"}),
    ),
    JudgeCase(
        "h6",
        CaseKind.DIRECT,
        "Payment processing",
        ("Not only built the payment APIs but also ran their on-call rotation.",),
        SUPPORTED,
        frozenset({"C1"}),
    ),
    JudgeCase(
        "h7",
        CaseKind.PARAPHRASE,
        "Kubernetes in production",
        (
            "No container experience was needed in the previous role, but this one "
            "involved running our EKS clusters day to day.",
        ),
        SUPPORTED,
        frozenset({"C1"}),
    ),
    JudgeCase(
        "h8",
        CaseKind.MENTION,
        "Five or more years of Python",
        ("Python developer since 2023.",),
        frozenset({W, X}),
        frozenset({"C1"}),
        hint="count the years; the current year is 2026",
    ),
    JudgeCase(
        "h9",
        CaseKind.PARAPHRASE,
        "Distributed systems",
        (
            "Designed a sharded, replicated ledger with leader election and "
            "idempotent writes across regions.",
        ),
        SUPPORTED,
        frozenset({"C1"}),
    ),
    JudgeCase(
        "h10",
        CaseKind.MENTION,
        "Python backend services",
        (
            "Five years building Python services.",
            "Note: the Python work was a two-week hackathon; production code was Java.",
        ),
        frozenset({W, A, X}),
        frozenset({"C1", "C2"}),
    ),
    JudgeCase(
        "h11",
        CaseKind.MENTION,
        "Kubernetes in production",
        ("Last used Kubernetes in production in 2014; since then, serverless only.",),
        frozenset({W, A}),
        frozenset({"C1"}),
    ),
    JudgeCase(
        "h12",
        CaseKind.NEGATION,
        "Computer science degree",
        ("BSc Physics; self-taught programmer.", FILLER),
        frozenset({X, W}),
        frozenset({"C1"}),
    ),
    JudgeCase(
        "h13",
        CaseKind.PARAPHRASE,
        "Team leadership",
        (
            FILLER,
            "Tech lead for three engineers on the checkout squad; ran planning and their reviews.",
        ),
        SUPPORTED,
        frozenset({"C2"}),
    ),
    JudgeCase(
        "h14",
        CaseKind.UNRELATED,
        "Payment processing",
        (LONG_FILLER, "Paid the team's lunch invoices through the expenses tool."),
        frozenset({X, W}),
        frozenset({"C2"}),
    ),
)
