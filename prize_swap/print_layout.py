"""Order-preserving, height-balanced columns and balanced page boundaries."""

from itertools import zip_longest

from .instruction_layout import measured_groups, pair_groups


def balanced_pages(groups, height, rows=20, layout="column"):
    """Evaluate every contiguous split; minimize pages, then column load squares.

    Sum-of-squares minimizes both left/right differences and uneven density
    between pages, without reordering any products in column order.
    """
    count = len(groups)
    if not count:
        return [[]]
    prefix = [0.0]
    for group in groups:
        prefix.append(prefix[-1] + group.height)
    best = [None] * (count + 1)
    best[count] = (0, 0.0, None)
    for start in range(count - 1, -1, -1):
        for stop in range(start + 1, min(count, start + 2 * rows) + 1):
            if best[stop] is None:
                continue
            choices = []
            if layout == "row":
                left, right = groups[start:stop:2], groups[start + 1 : stop : 2]
                lh, rh = sum(g.height for g in left), sum(g.height for g in right)
                if max(lh, rh) <= height + 1e-6:
                    choices.append((abs(lh - rh), -len(left), left, right, lh, rh))
            else:
                for split in range(max(start, stop - rows), min(stop, start + rows) + 1):
                    lh, rh = prefix[split] - prefix[start], prefix[stop] - prefix[split]
                    if max(lh, rh) <= height + 1e-6:
                        choices.append(
                            (
                                abs(lh - rh),
                                -(split - start),
                                groups[start:split],
                                groups[split:stop],
                                lh,
                                rh,
                            )
                        )
            if not choices:
                continue
            _, _, left, right, lh, rh = min(choices, key=lambda c: (round(c[0], 6), c[1]))
            score = (
                1 + best[stop][0],
                round(lh * lh + rh * rh + 0.25 * (lh + rh) ** 2 + best[stop][1], 6),
            )
            if (
                best[start] is None
                or score < best[start][:2]
                or (score == best[start][:2] and stop > best[start][2][0])
            ):
                best[start] = (score[0], score[1], (stop, left, right))
    pages = []
    start = 0
    while start < count:
        stop, left, right = best[start][2]
        pages.append([pair_groups(l, r) for l, r in zip_longest(left, right)])
        start = stop
    return pages


def pack_balanced(products, rows, layout, body_height, measure):
    groups, warnings = measured_groups(products, body_height, measure)
    return balanced_pages(groups, body_height, rows, layout), warnings
