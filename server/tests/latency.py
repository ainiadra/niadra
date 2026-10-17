"""Wall-clock bounds for tests that share a machine with other gates.

A test holds what it measured to ten times its target: that catches a regression of an order of magnitude,
never the noise of a loaded machine. `NIADRA_BENCH=1` makes the same tests a benchmark: each prints what it
measured and is held to the target itself. Statement counts and correctness never depend on this.
"""

import os

BENCH = os.environ.get("NIADRA_BENCH") == "1"
SLACK = 10


def within(name: str, measured_s: float, target_s: float) -> None:
    if BENCH:
        print(f"\nbench {name}: {measured_s * 1000:.2f} ms (target {target_s * 1000:.2f} ms)")
        assert measured_s < target_s, f"{name}: {measured_s * 1000:.1f} ms over its {target_s * 1000:.1f} ms"
        return
    limit = target_s * SLACK
    assert measured_s < limit, (
        f"{name}: {measured_s * 1000:.1f} ms, over ten times its {target_s * 1000:.1f} ms target"
    )
