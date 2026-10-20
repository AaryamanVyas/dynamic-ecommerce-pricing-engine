"""Dated, single-series statistics and a deliberately small forecast baseline."""
from datetime import date, timedelta
from decimal import Decimal
from statistics import mean, median


def _linear(points, target):
    origin = points[0][0]
    xs = [(day - origin).days for day, _ in points]
    ys = [price for _, price in points]
    xm, ym = mean(xs), mean(ys)
    denominator = sum((x - xm) ** 2 for x in xs)
    slope = sum((x - xm) * (y - ym) for x, y in zip(xs, ys)) / denominator if denominator else 0
    return ym + slope * ((target - origin).days - xm), slope


def analyze_history(rows, today=None):
    """Never pool stores/currencies; daily medians avoid refresh-count weighting.

    Require 5 distinct dates spanning 14 days. Promotions are shown in extrema
    but excluded from the forecast. One-step expanding-window validation selects
    a trend only when its absolute errors beat the last-price baseline.
    """
    today = date.fromisoformat(today) if today else date.today()
    if len({(row["currency"], row["series"]) for row in rows}) > 1:
        raise ValueError("History must contain one product, seller and currency series")
    by_day, values = {}, []
    for row in rows:
        day, amount = date.fromisoformat(row["date"][:10]), float(Decimal(str(row["price"])))
        if not 0 < amount < 1e9 or day > today:
            continue
        values.append(amount)
        if not row.get("discounted", False):
            by_day.setdefault(day, []).append(amount)
    points = sorted((day, median(prices)) for day, prices in by_day.items())
    if points:
        cutoff = points[-1][0] - timedelta(days=90)
        points = [point for point in points if point[0] >= cutoff]
    result = {"lowest": format(min(values), ".2f") if values else None,
              "highest": format(max(values), ".2f") if values else None,
              "distinctDates": len(points), "forecast": None, "stale": False,
              "reason": "Need 5 regular-price dates spanning at least 14 days; refreshes on one day do not establish a trend."}
    if points:
        result.update(lastDate=points[-1][0].isoformat(), ageDays=(today - points[-1][0]).days,
                      stale=(today - points[-1][0]).days > 14)
    if result.get("ageDays", 0) > 365:
        result["reason"] = "The latest regular-price record is over a year old; forecast withheld."
        return result
    if len(points) < 5 or (points[-1][0] - points[0][0]).days < 14:
        return result
    points = points[-30:]
    trend_errors, baseline_errors = [], []
    for index in range(3, len(points)):
        day, actual = points[index]
        prediction, _ = _linear(points[:index], day)
        trend_errors.append(abs(prediction - actual))
        baseline_errors.append(abs(points[index - 1][1] - actual))
    target = points[-1][0] + timedelta(days=7)
    prediction, slope = _linear(points, target)
    trend = mean(trend_errors) < mean(baseline_errors) and prediction > 0
    predicted = prediction if trend else points[-1][1]
    result.update(reason="Historical replay: target is seven days after the last record." if result["stale"]
                  else "Seven-day estimate based on recorded regular prices.",
                  forecast={"date": target.isoformat(), "price": format(predicted, ".2f"),
                            "model": "linear_trend" if trend else "last_price_baseline",
                            "trendPerWeek": round(slope * 7, 4),
                            "validationMAE": round(mean(trend_errors if trend else baseline_errors), 4),
                            "validationPoints": len(trend_errors),
                            "note": "Small-sample estimate, not a guarantee. Promotions excluded from fit; MAE describes historical errors, not a confidence interval."})
    return result
