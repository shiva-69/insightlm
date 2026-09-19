"""Build the report's figures from committed results in eval/results/.

    uv run python scripts/figures.py        # -> docs/figures/*.svg and *.png

Every figure reads a results file produced by a harness (see eval/results/README.md), so
each number in the dissertation traces back to a run anyone can repeat.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

RESULTS = Path("eval/results")
OUT = Path("docs/figures")
INK, INK3, EVIDENCE, CAUTION = "#1c1a18", "#6f685f", "#b45309", "#9a3412"

plt.rcParams.update(
    {
        "font.family": "sans-serif",
        "font.size": 10,
        "axes.edgecolor": "#d6d1cb",
        "axes.labelcolor": INK,
        "xtick.color": INK3,
        "ytick.color": INK3,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "figure.dpi": 150,
        "savefig.bbox": "tight",
    }
)


def load(name: str) -> object:
    return json.loads((RESULTS / name).read_text())


def save(fig: plt.Figure, name: str) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for ext in ("svg", "png"):
        fig.savefig(OUT / f"{name}.{ext}")
    plt.close(fig)


def retrieval() -> None:
    runs = load("retrieval.json")["runs"]  # type: ignore[index]
    names = [r["config"]["name"].split(" - ", 1)[1] for r in runs]
    metrics = [("recall_at_k", "Recall@8"), ("ndcg_at_k", "nDCG@8"), ("mrr", "MRR")]
    fig, ax = plt.subplots(figsize=(7, 3.2))
    width = 0.26
    for i, (key, label) in enumerate(metrics):
        values = [r["summary"][key] for r in runs]
        ax.bar(
            [x + (i - 1) * width for x in range(len(runs))],
            values,
            width,
            label=label,
            color=[INK, INK3, EVIDENCE][i],
        )
    ax.set_xticks(range(len(runs)), names)
    ax.set_ylim(0, 1.05)
    ax.set_title("Retrieval ablation, Gemini embeddings (generated-40)", loc="left")
    ax.legend(frameon=False, ncols=3, loc="lower right")
    save(fig, "retrieval-ablation")


def judge() -> None:
    reports = load("judge.json")
    kinds = ["direct", "paraphrase", "mention", "negation", "unrelated"]
    fig, ax = plt.subplots(figsize=(7, 3.2))
    width = 0.38
    for i, report in enumerate(reports):  # type: ignore[arg-type]
        values = [report["by_kind"][k] or 0 for k in kinds]
        ax.bar(
            [x + (i - 0.5) * width for x in range(len(kinds))],
            values,
            width,
            label=f"{report['judge']} ({report['accuracy']:.0%} overall)",
            color=[INK3, EVIDENCE][i],
        )
    ax.set_xticks(range(len(kinds)), kinds)
    ax.set_ylim(0, 1.05)
    ax.set_ylabel("accuracy")
    ax.set_title("Criterion judge by failure mode (judge-v1 + v2, 37 cases)", loc="left")
    ax.legend(frameon=False, loc="lower left")
    save(fig, "judge-by-kind")


def injection() -> None:
    report = load("injection.json")["assessment"]  # type: ignore[index]
    fig, axes = plt.subplots(1, len(report), figsize=(7, 3.2), sharey=True)
    for ax, (judge_name, results) in zip(axes, report.items(), strict=True):
        clean = results["clean CV"]["defended"]
        attacks = [k for k in results if k != "clean CV"]
        lift_u = sum(results[k]["undefended"] - clean for k in attacks) / len(attacks)
        lift_d = sum(results[k]["defended"] - clean for k in attacks) / len(attacks)
        ax.bar(["undefended", "defended"], [lift_u, lift_d], color=[CAUTION, INK])
        ax.set_title(judge_name, loc="left", fontsize=10)
        for x, v in enumerate([lift_u, lift_d]):
            ax.text(x, v + 0.8, f"{v:+.1f}", ha="center", color=INK)
    axes[0].set_ylabel("mean score lift from an attack")
    fig.suptitle("Prompt injection: effect on assessment scores (redteam-v1)", x=0.05, ha="left")
    save(fig, "injection-lift")


def fairness() -> None:
    report = load("fairness.json")
    runs = next(v for k, v in report.items() if k != "offline")  # type: ignore[union-attr]
    dims = ["name", "institution", "city"]
    fig, ax = plt.subplots(figsize=(7, 3.2))
    width = 0.38
    for i, run in enumerate(runs):
        base = run["bases"]["ambiguous"]
        values = [base[d]["spread_of_means"] for d in dims]
        bars = ax.bar(
            [x + (i - 0.5) * width for x in range(len(dims))],
            values,
            width,
            color=[INK3, EVIDENCE][i],
            label=f"redaction: {run['profile']}",
        )
        for bar, d in zip(bars, dims, strict=True):
            p = base[d]["p"]
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                bar.get_height() + 0.3,
                "p n/a" if p is None else f"p={p:.2f}",
                ha="center",
                fontsize=8,
                color=INK3,
            )
        noise = base["noise_sd"]
        ax.axhline(noise, color=[INK3, EVIDENCE][i], linestyle=":", linewidth=1)
    ax.set_xticks(range(len(dims)), dims)
    ax.set_ylabel("spread of variant means (0-100)")
    ax.set_title("Counterfactual fairness, ambiguous CV (dotted: judge noise sd)", loc="left")
    ax.legend(frameon=False)
    save(fig, "fairness-spread")


def talent() -> None:
    fig, ax = plt.subplots(figsize=(7, 3.2))
    for name, color in (("talent-offline.json", INK3), ("talent-gemini.json", EVIDENCE)):
        data = load(name)
        points = sorted(
            (float(k.split(":")[1]), v)
            for k, v in data["mean_f1"].items()  # type: ignore[index]
            if k.startswith("relative")
        )
        ax.plot(
            [p[0] for p in points],
            [p[1] for p in points],
            marker="o",
            color=color,
            label=data["embedder"],
        )  # type: ignore[index]
    ax.set_xlabel("cutoff: fraction of the best match's score")
    ax.set_ylabel("mean F1 (6 queries)")
    ax.set_ylim(0, 1.0)
    ax.set_title("Talent search: relative cutoff, per embedder (cohort-v1)", loc="left")
    ax.legend(frameon=False)
    save(fig, "talent-cutoff")


def capacity() -> None:
    # From the k6 runs recorded in docs/09 (the summaries are not kept per run).
    runs = [
        ("50 users\n1 process", 58),
        ("200 users\n1 process", 1730),
        ("200 users\n4 processes", 115),
    ]
    fig, ax = plt.subplots(figsize=(7, 3.2))
    ax.bar([r[0] for r in runs], [r[1] for r in runs], color=[INK, CAUTION, INK])
    ax.axhline(300, color=INK3, linestyle=":", linewidth=1)
    ax.text(2.45, 320, "read SLO 300 ms", ha="right", color=INK3, fontsize=8)
    for x, (_, v) in enumerate(runs):
        ax.text(x, v + 30, f"{v} ms", ha="center", color=INK)
    ax.set_ylabel("p95 latency, all requests")
    ax.set_title("Load test: the single-process knee (docs/09)", loc="left")
    save(fig, "capacity")


if __name__ == "__main__":
    for build in (retrieval, judge, injection, talent, capacity):
        build()
    if (RESULTS / "fairness.json").exists():
        fairness()
    sys.stdout.write(f"figures written to {OUT}\n")
