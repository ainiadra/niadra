"""Token estimation (5.10).

Without a local tokenizer for the target, the estimate errs upward: one token per three
characters, where Portuguese and English prose average about four. Over-counting only costs a
little budget; under-counting would push a pack past a view's ceiling.
"""

import math

ESTIMATOR = "chars_div_3"


def estimate_tokens(text: str) -> int:
    return max(1, math.ceil(len(text) / 3)) if text else 0
