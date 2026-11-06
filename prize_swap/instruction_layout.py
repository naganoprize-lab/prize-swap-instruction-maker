"""Group-atomic two-block packing, shared by measured PDF and Excel layouts."""

from dataclasses import dataclass, field

from .core import AppError, ordered
from .methods import registered_machines


@dataclass
class ProductGroup:
    product: object
    machines: list
    heights: list
    cells: list = field(default_factory=list)
    machine_cells: list = field(default_factory=list)
    continuation: bool = False

    @property
    def height(self):
        return sum(self.heights)


@dataclass
class GroupPair:
    groups: list
    heights: list

    @property
    def height(self):
        return sum(self.heights)

    @property
    def left(self):
        return self.groups[0].product if self.groups[0] else None

    @property
    def right(self):
        return self.groups[1].product if self.groups[1] else None

    @property
    def cells(self):
        return [g.cells if g else [] for g in self.groups]


def pair_groups(left, right):
    count = max(len(left.heights) if left else 0, len(right.heights) if right else 0)
    heights = [
        max(g.heights[i] if g and i < len(g.heights) else 0 for g in (left, right))
        for i in range(count)
    ]
    return GroupPair([left, right], heights)


def measured_groups(products, body_height, measure):
    segments, warnings = [], []
    for product in ordered(products):
        ids = registered_machines(product) or [""]
        group = measure(product, ids, False)
        if group.height <= body_height:
            segments.append(group)
            continue
        # Only genuinely oversized products are split. Each fragment is still
        # a complete set of machine rows, with merged product-level fields.
        first = True
        while ids:
            low, high, best = 1, len(ids), None
            while low <= high:
                count = (low + high) // 2
                candidate = measure(product, ids[:count], not first)
                if candidate.height <= body_height:
                    best = candidate
                    low = count + 1
                else:
                    high = count - 1
            if best is None:
                raise AppError(
                    f"「{product.name[:40]}」の文字が1ページに収まりません。商品名・運営方法を短くするか参考列幅を広げてください。"
                )
            segments.append(best)
            ids = ids[len(best.machines) :]
            first = False
        warnings.append(
            f"「{product.name[:40]}」は1ブロックの高さを超えるため、継続ブロックに分割しました。"
        )
    return segments, warnings


def pack_products(products, rows, layout, body_height, measure):
    if not 1 <= rows <= 50 or layout not in ("column", "row"):
        raise AppError("ページ配置の設定が不正です。")
    segments, warnings = measured_groups(products, body_height, measure)
    pages = []
    while segments:
        result = None
        for capacity in range(min(rows, len(segments)), 0, -1):
            chunk = segments[: capacity * 2]
            left = chunk[::2] if layout == "row" else chunk[:capacity]
            right = chunk[1::2] if layout == "row" else chunk[capacity:]
            pairs = [
                pair_groups(g, right[i] if i < len(right) else None) for i, g in enumerate(left)
            ]
            if (sum(len(p.heights) for p in pairs) <= rows or len(pairs) == 1) and sum(
                p.height for p in pairs
            ) <= body_height:
                result = pairs, len(chunk)
                break
        if result is None:
            result = [pair_groups(segments[0], None)], 1
        pages.append(result[0])
        segments = segments[result[1] :]
    return pages or [[]], warnings
