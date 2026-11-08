"""Label-only overrides; source products are shared but never renamed here."""

from dataclasses import asdict, dataclass
import math

from .core import AppError, machine_numbers


@dataclass
class LabelOptions:
    per_page: int = 7
    mode: str = "auto"
    font_size: int = 28
    weight: str = "bold"
    alignment: str = "center"
    border: float = 1.2

    def validate(self):
        if type(self.per_page) is not int or not 4 <= self.per_page <= 10:
            raise AppError("1ページのラベル数は4〜10枚です。")
        if (
            self.mode not in ("auto", "manual")
            or self.weight not in ("bold", "black")
            or self.alignment not in ("center", "left")
        ):
            raise AppError("ラベルの印刷設定が不正です。")
        if type(self.font_size) is not int or not 8 <= self.font_size <= 256:
            raise AppError("文字サイズは8〜256ptで指定してください。")
        if (
            isinstance(self.border, bool)
            or not isinstance(self.border, (float, int))
            or not math.isfinite(self.border)
            or not 0.5 <= self.border <= 4
        ):
            raise AppError("枠線の太さは0.5〜4ptです。")
        return self


def label_machines(value):
    text = value if isinstance(value, str) else "\n".join(value or [])
    return machine_numbers(text)


@dataclass
class LabelEntry:
    product: object
    name_override: str | None = None
    included_override: bool | None = None
    manual_copies: int | None = None

    @property
    def name(self):
        return self.product.name if self.name_override is None else self.name_override

    @property
    def machines(self):
        return label_machines(self.product.machines)

    @property
    def automatic_copies(self):
        return max(1, len(self.machines))

    @property
    def copies(self):
        return self.automatic_copies if self.manual_copies is None else self.manual_copies

    @property
    def included(self):
        return (
            self.product.included
            and bool(self.name.strip())
            and self.included_override is not False
        )


class LabelStore:
    def __init__(self):
        self.entries = {}
        self.options = LabelOptions()

    def sync(self, products):
        self.entries = {id(p): self.entries.get(id(p), LabelEntry(p)) for p in products}

    def for_day(self, products, day):
        self.sync(products)
        return sorted(
            (self.entries[id(p)] for p in products if p.day == day), key=lambda e: e.product.order
        )

    def state(self, products):
        self.sync(products)
        entries = []
        for i, p in enumerate(products):
            e = self.entries[id(p)]
            if (
                e.name_override is not None
                or e.included_override is not None
                or e.manual_copies is not None
            ):
                entries.append(
                    dict(
                        index=i,
                        name=e.name_override,
                        included=e.included_override,
                        copies=e.manual_copies,
                    )
                )
        return dict(version=1, options=asdict(self.options), entries=entries)

    @classmethod
    def restore(cls, state, products):
        result = cls()
        result.sync(products)
        if state is None:  # Old sessions remain readable.
            return result
        try:
            if not isinstance(state, dict) or state.get("version") != 1:
                raise ValueError("バージョンが不正")
            result.options = LabelOptions(**state["options"]).validate()
            seen = set()
            for record in state["entries"]:
                index = record["index"]
                if type(index) is not int or not 0 <= index < len(products) or index in seen:
                    raise ValueError("商品参照が不正")
                seen.add(index)
                name, included, copies = (
                    record.get("name"),
                    record.get("included"),
                    record.get("copies"),
                )
                if name is not None and (not isinstance(name, str) or not name.strip()):
                    raise ValueError("商品名が不正")
                if included is not None and type(included) is not bool:
                    raise ValueError("対象チェックが不正")
                if copies is not None and (type(copies) is not int or not 1 <= copies <= 9999):
                    raise ValueError("手動枚数が不正")
                result.entries[id(products[index])] = LabelEntry(
                    products[index], name, included, copies
                )
        except (KeyError, TypeError, ValueError) as exc:
            raise AppError(f"ラベル設定を復元できません：{exc}") from exc
        return result


def expanded_labels(entries):
    # Stable sort keeps identical-order source rows in their source order.
    return [
        entry.name
        for entry in sorted(entries, key=lambda e: e.product.order)
        if entry.included
        for _ in range(entry.copies)
    ]
