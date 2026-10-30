"""Plot an OpenTuner run's trials.jsonl per evaluation: score of each evaluation, best so far,
and failed / timed-out / constraint-rejected evaluations (drawn at score 0).

    python plot_opentune.py runs/<…_opentune-base_throughput>/trials.jsonl [out.png]   # default: progress.png next to it
"""
import json
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

src = Path(sys.argv[1])
out = sys.argv[2] if len(sys.argv) > 2 else src.with_name("progress.png")
recs = [json.loads(l) for l in src.read_text().splitlines() if l.strip("\0 ")]

ok = [r for r in recs if (r.get("measurement") or {}).get("ok")]
bad = [r for r in recs if r not in ok]
best, line = 0.0, []
for r in recs:
    best = max(best, r["score"])
    line.append(best)

fig, ax = plt.subplots(figsize=(12, 4))
ax.axhline(1.0, color="gray", linestyle="--", linewidth=1, label="baseline (1.0)")
ax.plot([r["eval"] for r in ok], [r["score"] for r in ok], ".", label="evaluation")
ax.plot([r["eval"] for r in bad], [0] * len(bad), "x", color="tab:red", label="failed / timeout / rejected")
ax.step([r["eval"] for r in recs], line, where="post", color="tab:orange", label="best so far")
ax.set_xlabel("evaluation")
ax.set_ylabel("score")
ax.grid(alpha=0.3)
ax.legend(loc="center left", bbox_to_anchor=(1.01, 0.5), fontsize=8)
ax.set_title(src.parent.name, fontsize=9)
fig.tight_layout()
fig.savefig(out, dpi=120)
print(out)
