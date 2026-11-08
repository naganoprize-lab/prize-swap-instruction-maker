"""Shared meanings of the two instruction blocks (A:G / H:N)."""

from .methods import active_assignments, format_plans, registered_machines

LABELS = [
    "マシン番号",
    "運営方法",
    "予定ブース数",
    "商品名",
    "入数",
    "CS数",
    "獲得個数制限",
]


def product_values(product):
    machines = registered_machines(product)
    assignments = active_assignments(product)
    name = ("【優先入替】\n" if product.priority and product.name else "") + (product.name or "")
    return [
        "\n".join(machines),
        "\n".join(assignments.get(n) or "" for n in machines) or "",
        format_plans(product),
        name,
        product.units if product.units is not None else "",
        product.cases if product.cases is not None else "",
        product.limit,
    ]
