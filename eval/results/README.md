# Final results, frozen code (19 Sep 2026)

Produced by the harnesses in `src/insightlm/cli/` on the final code and prompts. The
figures in `docs/figures/` are built from these files by `make figures`, so every number
in the report traces back to one of them. Paid calls are cached (`eval/.cache/`, not
committed): rerunning costs nothing until a case, prompt, rendering or model changes.

| File | Command | Paid calls this run | Finding |
| --- | --- | ---: | --- |
| `retrieval.json` | `insightlm-eval --corpus generated --count 40 --out …` | 0 (embeddings cached) | 4–11, 22 |
| `judge.json` | `insightlm-eval-judge --judge gemini --set all --out …` | 0 | 12, 19 |
| `injection.json` | `insightlm-eval-injection --assess --judge gemini --out …` | 27 | 14–15 |
| `fairness.json` | `insightlm-eval-fairness --judge gemini --bases ambiguous --samples 5 --out …` | 300 | 17, 21 |
| `talent-gemini.json`, `talent-offline.json` | `insightlm-eval-talent [--embedder gemini] --out …` | 0 | 20 |

`*.txt` files are each harness's printed report from the same run.

The load-test figure (`capacity`) takes its three numbers from docs/09, because k6
summaries were not kept per run; `make load` reproduces the 50-user one.
