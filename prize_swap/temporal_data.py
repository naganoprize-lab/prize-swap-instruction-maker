"""Pure history projections and denominator-aware aggregation; never mutate masters."""

from collections import defaultdict
from copy import deepcopy
from dataclasses import dataclass, asdict
from datetime import date, datetime, timedelta, timezone
from .floor_data import Machine
from .sales_analysis import METRICS, empty_snapshot, apply_snapshot
from .machine_matching import machine_key, normalize_reference, prepare_references, MANUAL_FIELDS
from .floor_status import effective_stat, ratio, reference_average
from .daily_reference import digest

NUMBERS = list(METRICS) + ["ratio"]
JST = timezone(timedelta(hours=9))


def manual_on(master, key, day):
    raw = master.manual_updates.get(key)
    if not raw:
        return False
    try:
        value = datetime.fromisoformat(raw)
        return (value.astimezone(JST).date() if value.tzinfo else value.date()).isoformat() == day
    except (ValueError, TypeError):
        return False


def layout_lookup(layouts, masters):
    by_key = defaultdict(list)
    by_number = defaultdict(list)
    master_keys = defaultdict(int)
    for m in masters:
        if m.source_key:
            master_keys[normalize_reference(m.source_key) or m.source_key] += 1
    for r in layouts:
        m = Machine(**r)
        key = normalize_reference(m.source_key) or m.source_key
        if key:
            by_key[key].append(m)
        number = machine_key(m) or m.reference_number
        if number:
            by_number[number].append(m)
    result = {}
    for m in masters:
        key = normalize_reference(m.source_key) or m.source_key
        candidates = by_key.get(key, []) if key and master_keys[key] == 1 else []
        if len(candidates) != 1:
            candidates = by_number.get(machine_key(m) or m.reference_number, [])
        result[m.uid] = candidates[0] if len(candidates) == 1 else None
    return result


def normalized_snapshot(doc):
    s = deepcopy(doc["snapshot"])
    meta = doc["metadata"]
    s.update(
        period_start=meta["start"],
        period_end=meta["end"],
        period_source=(
            "manual"
            if meta["source"] == "manual"
            else "excel" if meta["source"] == "excel" else "unknown"
        ),
    )
    # A validated filename is a distinct, visible source, not an Excel-extracted date.
    if meta["source"] == "filename":
        s["period_source"] = "manual"
    return s


def day_projection(masters, doc, overrides=None):
    lookup = layout_lookup(doc["layouts"], masters)
    out = []
    day = doc["metadata"]["end"]
    snapshot = normalized_snapshot(doc)
    for master in masters:
        m = deepcopy(master)
        historical = lookup[master.uid]
        m.analysis = None
        m.resolved_number = ""
        m.reference_number = ""
        m.reference_warning = ""
        m.match_method = "layout"
        m.match_message = ""
        for k in NUMBERS:
            setattr(m, k, None)
        m.ratio_from_excel = True
        m.installed_on = None
        m.product = ""
        m.operation_method = ""
        # Stable classification metadata may fall back; operational values never fall back to another day.
        m.manual_updates = {}
        m.prize_cost = None
        m.prize_cost_product = ""
        m.prize_cost_origin = "manual"
        m.play_pricing = None
        m.reference_average = master.reference_average
        if historical:
            for k in [
                *NUMBERS,
                "product",
                "kind",
                "installed_on",
                "operation_method",
                "maker",
                "reference_average",
                "ratio_from_excel",
            ]:
                setattr(m, k, deepcopy(getattr(historical, k)))
            if historical.number_origin == "reference" and master.number_origin != "direct":
                m.number = historical.number
        for k in MANUAL_FIELDS:
            if manual_on(master, k, day):
                setattr(m, k, deepcopy(getattr(master, k)))
                m.manual_updates[k] = master.manual_updates[k]
                if k == "prize_cost":
                    m.prize_cost_product = master.prize_cost_product
                    m.prize_cost_origin = master.prize_cost_origin
                    m.play_pricing = deepcopy(master.play_pricing)
        if m.reference_average is None:
            m.reference_average = master.reference_average
        m.layout_loaded_at = doc["imported_at"]
        out.append(m)
    prepare_references(out, doc["references"])
    apply_snapshot(out, snapshot)
    for master, m in zip(masters, out):
        # Explicit day edits are separate from raw Excel and from the live master.
        values = {k: getattr(master, k) for k in MANUAL_FIELDS if manual_on(master, k, day)}
        values.update((overrides or {}).get(m.uid, {}))
        updated = values.pop("_updated_at", doc["imported_at"])
        for k, v in values.items():
            if k in MANUAL_FIELDS or k in (
                "prize_cost_product",
                "prize_cost_origin",
                "play_pricing",
                "po_definition",
                "po_definition_fingerprint",
            ):
                setattr(m, k, deepcopy(v))
                m.manual_updates[k if k in MANUAL_FIELDS else "prize_cost"] = updated
                if m.analysis and k in METRICS:
                    m.analysis["values"][k] = v
                if m.analysis and k == "ratio":
                    m.analysis["imported_ratio"] = v
        if m.analysis:
            # Link imported performance to that day's product episode, not today's product.
            m.analysis["product_context"] = dict(product=m.product, installed_on=m.installed_on)
            m.analysis["operation_context"] = m.operation_method
    return out, snapshot


def sum_known(values):
    known = [v for v in values if v is not None]
    return sum(known) if known else None


def complete_sum(values):
    return sum(values) if values and all(v is not None for v in values) else None


def rollup_samples(samples, spans):
    """No ratio averaging: sum numerators and denominators over aligned samples."""
    result = {k: None for k in NUMBERS}
    count = len(samples)
    if not count:
        return result, dict(
            sales_days=0,
            valid_counts={},
            context_changed=False,
            po_reason="データなし",
            actual_plays=None,
            acquired_count=None,
            inserted_amount=None,
            ratio_denominator=None,
            product_names=[],
        )
    values = [{k: effective_stat(m, k) for k in METRICS} for m in samples]
    valid_counts = {
        k: sum(span for v, span in zip(values, spans) if v[k] is not None) for k in METRICS
    }
    for k in ("sales", "cost", "gross_profit", "dispensed", "operating_days"):
        result[k] = sum_known([v[k] for v in values])
    sales_days = valid_counts["sales"]
    if result["sales"] is not None and sales_days:
        result["average"] = result["sales"] / sales_days
    # Cost and sales must have exactly the same coverage. Missing costs are not zero.
    cost = complete_sum([v["cost"] if v["sales"] is not None else None for v in values])
    all_sales = complete_sum([v["sales"] for v in values])
    if cost is not None and all_sales is not None and all_sales > 0:
        result["cost_rate"] = cost / all_sales
    if (
        result["sales"] is not None
        and result["dispensed"] is not None
        and result["dispensed"] > 0
        and all(v["sales"] is not None and v["dispensed"] is not None for v in values)
    ):
        result["per_item"] = result["sales"] / result["dispensed"]
    prices = {v["unit_price"] for v in values if v["unit_price"] is not None}
    if len(prices) == 1 and all(v["unit_price"] is not None for v in values):
        result["unit_price"] = next(iter(prices))
    plays = []
    counts = []
    inserted = []
    for m in samples:
        p = (m.analysis or {}).get("po_inputs", {})
        plays.append(p.get("actual_plays"))
        counts.append(p.get("acquired_count", effective_stat(m, "dispensed")))
        inserted.append(p.get("inserted_amount"))
    total_plays = complete_sum(plays)
    total_count = complete_sum(counts)
    reason = "プレイ回数の分母が不足しているため期間PO率は算出不可"
    if total_plays is not None and total_plays > 0 and total_count is not None:
        result["po_rate"] = total_count / total_plays
        reason = "期間獲得個数合計÷期間プレイ回数合計"
    elif count == 1:
        from .po_evaluation import evaluate_po

        result["po_rate"] = evaluate_po(samples[0])["actual"]
        reason = "単一の確定日・期間の確認済みP/O率"
    baseline = []
    for m, span, v in zip(samples, spans, values):
        ref = reference_average(m)
        baseline.append(
            ref * span if ref is not None and ref > 0 and v["sales"] is not None else None
        )
    denominator = complete_sum(baseline)
    if all_sales is not None and denominator is not None and denominator > 0:
        result["ratio"] = all_sales / denominator * 100
    elif count == 1:
        result["ratio"] = ratio(samples[0])
    contexts = {(m.product, m.installed_on, m.operation_method) for m in samples}
    details = dict(
        sales_days=sales_days,
        valid_counts=valid_counts,
        context_changed=len(contexts) > 1,
        po_reason=reason,
        actual_plays=total_plays,
        acquired_count=total_count,
        inserted_amount=complete_sum(inserted),
        ratio_denominator=denominator,
        product_names=sorted({m.product for m in samples if m.product}),
    )
    return result, details


@dataclass
class TemporalFrame:
    start: str
    end: str
    machines: list
    snapshot: dict
    coverage: dict
    source_ids: tuple


def build_frame(masters, docs, start, end, coverage, overrides=None, cancel=None):
    day_sets = []
    snapshots = []
    spans = []
    for doc in docs:
        if cancel and cancel.is_set():
            raise RuntimeError("集計をキャンセルしました。")
        machines, snapshot = day_projection(masters, doc, (overrides or {}).get(doc["id"]))
        span = (
            date.fromisoformat(doc["metadata"]["end"])
            - date.fromisoformat(doc["metadata"]["start"])
        ).days + 1
        day_sets.append(machines)
        snapshots.append(snapshot)
        spans.append(span)
    source_ids = tuple(d["id"] for d in docs)
    s = empty_snapshot()
    s.update(
        file_name=f"日別参照 {start}〜{end}",
        source=" / ".join(d["name"] for d in docs[:5]),
        loaded_at=max((d["imported_at"] for d in docs), default=""),
        period_start=start,
        period_end=end,
        period_source="manual",
        po_definition="prize_per_play",
        fingerprint=digest([source_ids, start, end, overrides]),
    )
    # The rank reference includes all Excel machines, independent of floor matching.
    numbers = sorted({n for snapshot in snapshots for n in snapshot["records"]})
    for i, n in enumerate(numbers):
        if cancel and cancel.is_set():
            raise RuntimeError("集計をキャンセルしました。")
        samples = []
        sample_spans = []
        for doc, snapshot, span in zip(docs, snapshots, spans):
            r = snapshot["records"].get(n)
            if not r:
                continue
            m = Machine(
                number=n,
                analysis=dict(
                    values=r["values"],
                    po_inputs=r.get("po_inputs", {}),
                    po_definition=snapshot["po_definition"],
                    period_start=snapshot["period_start"],
                    period_end=snapshot["period_end"],
                    period_source=snapshot["period_source"],
                    product_context=None,
                ),
                reference_average=None,
            )
            samples.append(m)
            sample_spans.append(span)
        vals, detail = rollup_samples(samples, sample_spans)
        if detail["sales_days"] != coverage["acquired"]:
            vals["sales"] = None
        raw = deepcopy(next(x["records"][n] for x in reversed(snapshots) if n in x["records"]))
        raw.update(
            values={k: vals[k] for k in METRICS},
            row=i + 2,
            raw_number=n,
            reference_name="期間集計",
            po_inputs={},
        )
        s["records"][n] = raw
    projected = []
    for i, master in enumerate(masters):
        if cancel and cancel.is_set():
            raise RuntimeError("集計をキャンセルしました。")
        samples = [ms[i] for ms in day_sets]
        latest = deepcopy(samples[-1] if samples else master)
        vals, detail = rollup_samples(samples, spans)
        detail.update(
            coverage,
            period_start=start,
            period_end=end,
            source_ids=source_ids,
            sales_comparable=bool(
                coverage["acquired"] and detail["sales_days"] == coverage["acquired"]
            ),
            read_only=not (
                len(docs) == 1 and start == end and docs[0]["metadata"]["data_type"] == "daily"
            ),
        )
        if len(docs) == 1 and start == end:
            # Keep the original day values and sources; do not manufacture a recomputed daily ratio.
            pass
        else:
            if not samples:
                latest.product = ""
                latest.installed_on = None
                latest.operation_method = ""
            for key, value in vals.items():
                setattr(latest, key, value)
            latest.temporal_metrics = vals
            latest.analysis = dict(
                values={k: vals[k] for k in METRICS},
                po_inputs={
                    k: detail[k]
                    for k in ("actual_plays", "acquired_count", "inserted_amount")
                    if detail[k] is not None
                },
                po_definition="prize_per_play",
                file_name=s["file_name"],
                loaded_at=s["loaded_at"],
                period_start=start,
                period_end=end,
                period_source="manual",
                fingerprint=s["fingerprint"],
                product_context=dict(product=latest.product, installed_on=latest.installed_on),
                operation_context=latest.operation_method,
                multiple_products=detail["context_changed"],
            )
        latest.temporal_summary = detail
        if not samples:
            latest.ratio_from_excel = True
            latest.temporal_metrics = vals
            latest.match_method = "layout"
            latest.match_message = "選択期間のデータなし"
        # Physical geometry and persistent identities always belong to the live floor.
        for k in ("uid", "x", "y", "width", "height", "font_size"):
            setattr(latest, k, getattr(master, k))
        projected.append(latest)
        number = machine_key(latest)
        if number in s["records"] and any(
            "sales" in (overrides or {}).get(doc["id"], {}).get(master.uid, {}) for doc in docs
        ):
            s["records"][number]["values"]["sales"] = (
                effective_stat(latest, "sales") if detail["sales_comparable"] else None
            )
    return TemporalFrame(start, end, projected, s, coverage, source_ids)


def attach_comparison(frame, masters, library, folder, settings=None, cancel=None):
    """Nearest acquired daily reports, independently of playback direction.

    Trend is supplementary and requires the same prize episode and method. It
    never rewrites the established single-day replacement rules.
    """
    usable, _ = library.usable(folder, full=False)
    daily = [
        d
        for d in usable
        if d["metadata"]["data_type"] == "daily" and d["metadata"]["end"] <= frame.end
    ]
    if len(daily) < 2:
        return frame
    current, previous = daily[-1], daily[-2]
    if current["metadata"]["end"] < frame.start:
        return frame
    projected = []
    for doc in (previous, current):
        if cancel and cancel.is_set():
            raise RuntimeError("集計をキャンセルしました。")
        full = library.document(doc["id"])
        day = full["metadata"]["end"]
        coverage = dict(expected=1, acquired=1, missing=0, partial=False, warnings=[], dates=[day])
        projected.append(
            build_frame(
                masters,
                [full],
                day,
                day,
                coverage,
                {doc["id"]: library.preference("overrides:" + doc["id"], {})},
                cancel,
            )
        )
    from .analysis_settings import sales_boundaries
    from .sales_evaluation import evaluation

    # Rank is a descriptive position among all source records on that date.
    settings = settings or {}
    bounds = [sales_boundaries(p.machines, settings, p.snapshot) for p in projected]
    for i, m in enumerate(frame.machines):
        old, new = (p.machines[i] for p in projected)
        context = (old.product, old.installed_on, old.operation_method)
        comparable = bool(
            all(context) and context == (new.product, new.installed_on, new.operation_method)
        )
        a, b = effective_stat(old, "sales"), effective_stat(new, "sales")
        ra, rb = ratio(old), ratio(new)
        comparison = dict(
            previous_date=previous["metadata"]["end"],
            current_date=current["metadata"]["end"],
            previous_sales=a,
            current_sales=b,
            sales_delta=b - a if comparable and a is not None and b is not None else None,
            sales_change=(
                b / a - 1 if comparable and a is not None and a > 0 and b is not None else None
            ),
            ratio_delta=rb - ra if comparable and ra is not None and rb is not None else None,
            previous_rank=evaluation(old, settings, bounds[0])["rank"],
            current_rank=evaluation(new, settings, bounds[1])["rank"],
            comparable=comparable,
            reason=(
                ""
                if comparable
                else "商品・設置日・運営方法が一致する履歴が不足しているため推移未評価"
            ),
        )
        m.temporal_summary["comparison"] = comparison
        if m.analysis:
            m.analysis["temporal_comparison"] = deepcopy(comparison)
    return frame
