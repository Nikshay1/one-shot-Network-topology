# VERDICT — benchmark results

## heldout

| mode | n | AC@1 | AC@3 | Avg@5 |
| --- | --- | --- | --- | --- |
| agentic | 1 | 0.000 | 0.000 | 0.400 |
| fixed | 1 | 0.000 | 0.000 | 0.400 |
| fixed-no-counterfactual | 1 | 1.000 | 1.000 | 1.000 |
| fixed-no-topology | 1 | 0.000 | 1.000 | 0.600 |
| fixed-no-twin | 1 | 0.000 | 0.000 | 0.400 |

## synthetic

| mode | n | precision@1 | precision@3 | red-herring false-blame | median time-to-RCA (s) |
| --- | --- | --- | --- | --- | --- |
| agentic | 23 | 0.522 | 0.739 | 0.000 | 7.866 |
| fixed | 23 | 0.522 | 0.739 | 0.000 | 13.860 |

## Agent efficiency — agentic vs fixed

The fixed pipeline spends the same budget on every case (5 counterfactuals + 1 twin) whether the case needs it or not. The agent chooses its targets. The claim is equal-or-better AC@k for fewer expensive ops — so this table, not AC@k alone, is the result.

| suite | mode | n | mean tool calls | mean cost points | mean expensive ops | mean wall-clock (s) |
| --- | --- | --- | --- | --- | --- | --- |
| heldout | agentic | 1 | 0.000 | 0.000 | 6.000 | 2.660 |
| heldout | fixed | 1 | 0.000 | 0.000 | 6.000 | 2.119 |
| heldout | fixed-no-counterfactual | 1 | 0.000 | 0.000 | 1.000 | 12.121 |
| heldout | fixed-no-topology | 1 | 0.000 | 0.000 | 6.000 | 3.971 |
| heldout | fixed-no-twin | 1 | 0.000 | 0.000 | 5.000 | 2.449 |
| synthetic | agentic | 25 | 0.000 | 0.000 | 5.040 | 7.984 |
| synthetic | fixed | 25 | 0.000 | 0.000 | 5.040 | 13.734 |

## LLM cost per case

- `OPENAI_API_KEY` present: **False**
- measured USD/case: **0.000**
- runs that degraded to the autopilot: **1**

> No OPENAI_API_KEY: every agent resolved to no-LLM, so rule 11 ran the deterministic autopilot and cost per case is $0.00. The agentic rows below are therefore NOT a measurement of the agent — they are the autopilot under an agentic label. Re-run with a key for the real table.

## External baselines (RCAEval)

> Skipped: nsigma: RCAEval not importable (No module named 'RCAEval'); baro: RCAEval not importable (No module named 'RCAEval')

![benchmark](results.png)

---

Reproduce: `make bench` (see README §Reproduce the numbers).