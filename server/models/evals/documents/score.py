"""Scores the reader's text against the test set's ground truth, with the scorer of the original measurement.

    python evals/documents/score.py [results.jsonl ...] [--require-keys N] [--require-rows N]

- keys: the exact value appears in the text (spaces collapsed, case ignored, accents kept); `recall` is
  the same with every space removed.
- order: the share of consecutive snippets in the expected order, plus the sentences that must come out
  whole.
- table: the share of ground-truth rows whose cells all appear, in order, on one line of the text.

Prints a table per engine and the totals; with `--require-*` it exits 1 when the widened reader finds
fewer keys (strict) or table rows than asked. Documents without ground truth (the stress set) are listed
for their time and memory only.
"""

import html
import itertools
import json
import re
import sys
from pathlib import Path

W = Path(__file__).parent
TRUTH = json.loads((W / "testset/ground_truth.json").read_text())


def norm(s: str) -> str:
    s = html.unescape(s)
    s = re.sub(r"\\([\\`*_{}\[\]()#+\-.!$|])", r"\1", s)  # markdown escapes
    s = s.replace("**", "")
    return re.sub(r"\s+", "", s).lower()


def strict(s: str) -> str:
    s = html.unescape(s)
    s = re.sub(r"\\([\\`*_{}\[\]()#+\-.!$|])", r"\1", s)
    s = s.replace("**", "")
    return re.sub(r"\s+", " ", s).lower()


def strip_images(s: str) -> str:
    return re.sub(r"!\[[^\]]*\]\([^)]*\)", "", s)


def rows_of(text: str) -> list[str]:
    text = re.sub(r"</tr>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " | ", text)
    return text.split("\n")


def score(text: str | None, truth: dict) -> dict:
    if not text:
        return {
            "recall": 0.0,
            "strict": 0.0,
            "order": 0.0,
            "table": None if not truth["table_rows"] else 0.0,
            "chars": 0,
        }
    text = strip_images(text)
    n = norm(text)
    st = strict(text)
    hits = [k for k in truth["keys"] if norm(k) in n]
    strict_hits = [k for k in truth["keys"] if strict(k) in st]
    pos = [n.find(norm(k)) for k in truth["order"]]
    pairs = list(itertools.pairwise(pos))
    checks = [a >= 0 and b > a for a, b in pairs]
    checks += [norm(c) in n for c in truth.get("contiguous", [])]
    order = sum(checks) / len(checks)
    table = None
    if truth["table_rows"]:
        rows = [norm(r) for r in rows_of(text)]
        ok = 0
        for cells in truth["table_rows"]:
            cn = [norm(c) for c in cells]
            for r in rows:
                i, good = 0, True
                for c in cn:
                    j = r.find(c, i)
                    if j < 0:
                        good = False
                        break
                    i = j + len(c)
                if good:
                    ok += 1
                    break
        table = ok / len(truth["table_rows"])
    return {
        "recall": len(hits) / len(truth["keys"]),
        "strict": len(strict_hits) / len(truth["keys"]),
        "order": order,
        "table": table,
        "chars": len(text),
        "missed": [k for k in truth["keys"] if k not in hits],
    }


def main(argv: list[str]) -> int:
    args, require = [], {}
    it = iter(argv)
    for a in it:
        if a in ("--require-keys", "--require-rows"):
            require[a] = int(next(it))
        else:
            args.append(Path(a))
    files = args or sorted((W / "results").glob("*.jsonl"))
    out: dict[str, dict[str, dict]] = {}
    for f in files:
        for line in f.read_text().splitlines():
            r = json.loads(line)
            truth = TRUTH.get(r["file"])
            s = score(r["text"], truth) if truth else {}
            peak = max(r.get("child_peak_rss") or 0, r.get("peak_tree_rss") or 0, r.get("maxrss_self") or 0)
            out.setdefault(r["engine"], {})[r["file"]] = {
                **s,
                "t_cold": r["t_cold"],
                "peak": peak,
                "cgroup_peak": r.get("cgroup_peak"),
                "error": r.get("error"),
            }
    (W / "results" / "scores.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))
    mb = 1024 * 1024
    failed = False
    header = ("file", "strict", "recall", "order", "table", "cold s", "peakMB", "cgMB")
    for engine, docs in out.items():
        print(f"\n== {engine}")
        print("{:32} {:>6} {:>6} {:>5} {:>5} {:>6} {:>7} {:>6}  missed/error".format(*header))
        keys = strict_keys = rows = total_keys = total_rows = 0
        for name, s in docs.items():
            truth = TRUTH.get(name)
            cost = f"{s['t_cold']:6.1f} {s['peak'] / mb:7.0f} {(s['cgroup_peak'] or 0) / mb:6.0f}"
            if truth is None:
                print(f"{name:32} {'':27} {cost}  {s['error'] or ''}")
                continue
            tb = "-" if s["table"] is None else f"{s['table']:.2f}"
            quality = f"{s['strict']:6.2f} {s['recall']:6.2f} {s['order']:5.2f} {tb:>5}"
            print(f"{name:32} {quality} {cost}  {s.get('missed', '')} {s['error'] or ''}")
            n = len(truth["keys"])
            total_keys += n
            strict_keys += round(s["strict"] * n)
            keys += round(s["recall"] * n)
            total_rows += len(truth["table_rows"])
            rows += round((s["table"] or 0) * len(truth["table_rows"]))
        print(f"keys {strict_keys}/{total_keys} (without spaces {keys}/{total_keys}), ", end="")
        print(f"table rows {rows}/{total_rows}")
        short = strict_keys < require.get("--require-keys", 0) or rows < require.get("--require-rows", 0)
        if engine == "widened" and short:
            failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
