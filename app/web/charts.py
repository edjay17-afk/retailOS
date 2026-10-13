"""Server-side SVG charts. Every point, bar and slice carries a data-tip attribute; a few lines of JavaScript in base.html
show it as a tooltip on hover or touch, so each value can be read exactly. Without JavaScript the browser's own <title> tooltip is used."""
from __future__ import annotations

import itertools
import math
from typing import Callable, Sequence

from markupsafe import Markup, escape

PESO = "₱"
DASH = ' stroke-dasharray="3 4"'
_gid = itertools.count(1)


def _english(text: str, **values) -> str:
    return text.format_map(values) if values else text


def _money(v: float) -> str:
    return f"{PESO}{v:,.2f}"


def _compact(v: float) -> str:
    a = abs(v)
    if a >= 1_000_000:
        return f"{v / 1_000_000:.1f}M".replace(".0M", "M")
    if a >= 10_000:
        return f"{v / 1000:.0f}k"
    if a >= 1000:
        return f"{v / 1000:.1f}k".replace(".0k", "k")
    return f"{v:,.0f}"


def _nice_max(v: float) -> float:
    """Round the top of the scale up to a tidy number (1, 2, 2.5, 5 or 10 times a power of ten)."""
    if v <= 0:
        return 1.0
    p = 10 ** math.floor(math.log10(v))
    for m in (1, 2, 2.5, 5, 10):
        if v <= m * p:
            return m * p
    return 10 * p


def _tip(text: str) -> str:
    return f'data-tip="{escape(text)}"'


def _smooth(pts: list[tuple[float, float]]) -> str:
    """Catmull-Rom to cubic bezier: a smooth path through every point (charts read as trends, not staircases)."""
    if len(pts) < 3:
        return " ".join(f"{'M' if i == 0 else 'L'}{x:.1f},{y:.1f}" for i, (x, y) in enumerate(pts))
    d = [f"M{pts[0][0]:.1f},{pts[0][1]:.1f}"]
    for i in range(len(pts) - 1):
        p0 = pts[i - 1] if i else pts[i]
        p1, p2 = pts[i], pts[i + 1]
        p3 = pts[i + 2] if i + 2 < len(pts) else p2
        c1 = (p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6)
        c2 = (p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6)
        d.append(f"C{c1[0]:.1f},{c1[1]:.1f} {c2[0]:.1f},{c2[1]:.1f} {p2[0]:.1f},{p2[1]:.1f}")
    return " ".join(d)


def _grid(out: list, left: float, right: float, top: float, tpad: float, ih: float, axis_fmt) -> None:
    for g in range(5):
        v = top * g / 4
        y = tpad + ih - (v / top) * ih
        out.append(f'<line x1="{left}" x2="{right}" y1="{y:.1f}" y2="{y:.1f}" stroke="var(--line)" stroke-width="1"{"" if g == 0 else DASH}/>'
                   f'<text x="{left - 8}" y="{y + 3.5:.1f}" text-anchor="end" font-size="10.5" fill="var(--mute)">{escape(axis_fmt(v))}</text>')


def sparkline(values: Sequence[float | None], w: int = 110, h: int = 36, color: str = "var(--chart)",
              labels: Sequence[str] | None = None, fmt: Callable[[float], str] = _money, t: Callable = _english) -> Markup:
    """Small trend line. With labels, hovering along it shows that point's label and value."""
    kept = [(i, v) for i, v in enumerate(values) if v is not None]  # gaps keep their own label
    if len(kept) < 2:
        return Markup("")
    vals = [v for _, v in kept]
    lo, hi = min(vals), max(vals)
    span = (hi - lo) or 1.0
    pad = 3
    step = (w - 2 * pad) / (len(vals) - 1)
    pts = [(pad + k * step, h - pad - (v - lo) / span * (h - 2 * pad)) for k, (_, v) in enumerate(kept)]
    gid = f"sg{next(_gid)}"
    lo_i = vals.index(lo)
    curve = _smooth(pts)
    out = [f'<svg class="spark" viewBox="0 0 {w} {h}" width="{w}" height="{h}" role="img" aria-label="{escape(t("trend"))}">'
           f'<defs><linearGradient id="{gid}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{color}" stop-opacity=".22"/><stop offset="1" stop-color="{color}" stop-opacity="0"/></linearGradient></defs>'
           f'<path d="{curve} L{pts[-1][0]:.1f},{h} L{pts[0][0]:.1f},{h} Z" fill="url(#{gid})" stroke="none"/>'
           f'<path d="{curve}" fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>'
           f'<circle cx="{pts[-1][0]:.1f}" cy="{pts[-1][1]:.1f}" r="6.5" fill="{color}" opacity=".14"/>'
           f'<circle cx="{pts[-1][0]:.1f}" cy="{pts[-1][1]:.1f}" r="2.8" fill="{color}"/>'
           f'<circle cx="{pts[lo_i][0]:.1f}" cy="{pts[lo_i][1]:.1f}" r="3" fill="var(--surface)" stroke="{color}" stroke-width="1.6"/>']
    for k, (x, y) in enumerate(pts):
        i = kept[k][0]
        lab = labels[i] if labels and i < len(labels) else t("Point {count}", count=i + 1)
        tip = f"{lab}: {fmt(vals[k])}"
        out.append(f'<g class="pt"><rect class="hit" x="{x - step / 2:.1f}" y="0" width="{step:.1f}" height="{h}" {_tip(tip)}><title>{escape(tip)}</title></rect>'
                   f'<circle class="dot" cx="{x:.1f}" cy="{y:.1f}" r="3.2" fill="{color}" stroke="#fff" stroke-width="1.5"/></g>')
    out.append("</svg>")
    return Markup("".join(out))


def area(items: Sequence[tuple[str, float]], w: int = 760, h: int = 250, color: str = "var(--chart)", fmt: Callable[[float], str] = _money,
         axis_fmt: Callable[[float], str] = _compact, name: str = "", compare: Sequence[float] | None = None, t: Callable = _english) -> Markup:
    """Line-and-area chart with a value axis, gridlines, an average line and a hover marker on every point.
    With compare (same window one period back), a dashed grey line is overlaid and each tooltip names the previous value."""
    if len(items) < 2:
        return Markup("")
    vals = [v for _, v in items]
    cmp_vals = list(compare)[: len(items)] if compare else []
    top = _nice_max(max(vals + cmp_vals))
    left, right, tpad, bpad = 46, 14, 14, 30
    ih = h - tpad - bpad
    n = len(items)
    step = (w - left - right) / (n - 1)
    pts = [(left + i * step, tpad + ih - (v / top) * ih) for i, (_, v) in enumerate(items)]
    curve = _smooth(pts)
    gid = f"ag{next(_gid)}"
    out = [f'<svg class="chart" viewBox="0 0 {w} {h}" width="100%" role="img" aria-label="{escape(name or t("line chart"))}">'
           f'<defs><linearGradient id="{gid}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{color}" stop-opacity=".24"/><stop offset="1" stop-color="{color}" stop-opacity=".01"/></linearGradient></defs>']
    _grid(out, left, w - right, top, tpad, ih, axis_fmt)
    avg = sum(vals) / n
    if avg > 0:
        ya = tpad + ih - (avg / top) * ih
        out.append(f'<line x1="{left}" x2="{w - right}" y1="{ya:.1f}" y2="{ya:.1f}" stroke="var(--mute2)" stroke-width="1.2" stroke-dasharray="6 4"/>'
                   f'<text x="{w - right}" y="{ya - 5:.1f}" text-anchor="end" font-size="10.5" fill="var(--mute)">{escape(t("average"))} {escape(fmt(avg))}</text>')
    if cmp_vals:
        cline = " ".join(f"{pts[i][0]:.1f},{tpad + ih - (cv / top) * ih:.1f}" for i, cv in enumerate(cmp_vals))
        out.append(f'<polyline points="{cline}" fill="none" stroke="var(--mute2)" stroke-width="2" stroke-dasharray="5 4" stroke-linecap="round" stroke-linejoin="round"/>')
    out.append(f'<path d="{curve} L{pts[-1][0]:.1f},{tpad + ih:.1f} L{left:.1f},{tpad + ih:.1f} Z" fill="url(#{gid})" stroke="none"/>'
               f'<path d="{curve}" fill="none" stroke="{color}" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"/>')
    every = max(1, math.ceil(n / 7))
    for i, (label, v) in enumerate(items):
        x, y = pts[i]
        if i % every == 0 and (i == n - 1 or n - 1 - i > every // 2 + 1):
            out.append(f'<text x="{x:.1f}" y="{h - 9}" text-anchor="middle" font-size="10.5" fill="var(--mute)">{escape(label)}</text>')
        prev = items[i - 1][1] if i else None
        extra = "" if not prev else f" ({(v - prev) / prev * 100:+.0f}% {t('vs previous')})"
        if i < len(cmp_vals):
            extra += f", {t('prev period')} {fmt(cmp_vals[i])}"
        tip = f"{label}: {fmt(v)}{extra}"
        out.append(f'<g class="pt"><rect class="hit" x="{x - step / 2:.1f}" y="{tpad}" width="{step:.1f}" height="{ih}" {_tip(tip)}><title>{escape(tip)}</title></rect>'
                   f'<line class="guide" x1="{x:.1f}" x2="{x:.1f}" y1="{tpad}" y2="{tpad + ih}"/>'
                   f'<circle class="dot" cx="{x:.1f}" cy="{y:.1f}" r="4.6" fill="{color}" stroke="#fff" stroke-width="2"/></g>')
    out.append(f'<circle cx="{pts[-1][0]:.1f}" cy="{pts[-1][1]:.1f}" r="9" fill="{color}" opacity=".14" pointer-events="none"/>'
               f'<circle cx="{pts[-1][0]:.1f}" cy="{pts[-1][1]:.1f}" r="4" fill="{color}" stroke="#fff" stroke-width="1.8" pointer-events="none"/></svg>')
    return Markup("".join(out))


def bars(items: Sequence[tuple[str, float]], w: int = 760, h: int = 250, color: str = "var(--chart)", fmt: Callable[[float], str] = lambda v: f"{v:,.0f}",
         axis_fmt: Callable[[float], str] = _compact, name: str = "", highlight_max: bool = True, compare: Sequence[float] | None = None, t: Callable = _english) -> Markup:
    """Vertical bars with a value axis, gridlines and an average line. The tallest bar is darker. Hover any bar for its exact value.
    With compare (same window one period back), a dashed grey line is overlaid and each tooltip names the previous value."""
    if not items:
        return Markup("")
    vals = [v for _, v in items]
    cmp_vals = list(compare)[: len(items)] if compare else []
    top = _nice_max(max(vals + cmp_vals + [0]))
    left, right, tpad, bpad = 46, 12, 14, 30
    ih = h - tpad - bpad
    n = len(items)
    bw = (w - left - right) / n
    best = max(range(n), key=lambda i: vals[i]) if highlight_max else -1
    out = [f'<svg class="chart" viewBox="0 0 {w} {h}" width="100%" role="img" aria-label="{escape(name or t("bar chart"))}">']
    _grid(out, left, w - right, top, tpad, ih, axis_fmt)
    every = max(1, math.ceil(n / 12))
    last_labeled = -every  # the last label only prints if it will not crowd the previous one
    for i, (label, v) in enumerate(items):
        bh = max((v / top) * ih, 0)
        x = left + i * bw + bw * 0.16
        wbar = bw * 0.68
        tip = f"{label}: {fmt(v)}" + (f", {t('prev period')} {fmt(cmp_vals[i])}" if i < len(cmp_vals) else "")
        fill = color if i != best else "var(--primary-h)"
        out.append(f'<g class="bar"><rect class="hit" x="{left + i * bw:.1f}" y="{tpad}" width="{bw:.1f}" height="{ih}" {_tip(tip)}><title>{escape(tip)}</title></rect>'
                   f'<rect class="b" x="{x:.1f}" y="{tpad + ih - bh:.1f}" width="{wbar:.1f}" height="{max(bh, 1.2):.1f}" rx="{min(6, wbar / 3):.1f}" fill="{fill}" opacity=".88"/></g>')
        if i % every == 0 or (i == n - 1 and i - last_labeled > max(1, every // 2)):
            out.append(f'<text x="{x + wbar / 2:.1f}" y="{h - 9}" text-anchor="middle" font-size="10.5" fill="var(--mute)">{escape(label)}</text>')
            last_labeled = i
    if cmp_vals:
        cline = " ".join(f"{left + i * bw + bw / 2:.1f},{tpad + ih - (cv / top) * ih:.1f}" for i, cv in enumerate(cmp_vals))
        out.append(f'<polyline points="{cline}" fill="none" stroke="var(--mute2)" stroke-width="2" stroke-dasharray="5 4" stroke-linecap="round" stroke-linejoin="round" pointer-events="none"/>')
    avg = sum(vals) / n
    if avg > 0:
        ya = tpad + ih - (avg / top) * ih
        out.append(f'<line x1="{left}" x2="{w - right}" y1="{ya:.1f}" y2="{ya:.1f}" stroke="var(--ink)" stroke-opacity=".45" stroke-width="1.2" stroke-dasharray="6 4" pointer-events="none"/>'
                   f'<text x="{w - right}" y="{ya - 5:.1f}" text-anchor="end" font-size="10.5" fill="var(--mute)" pointer-events="none">{escape(t("average"))} {escape(fmt(avg))}</text>')
    out.append("</svg>")
    return Markup("".join(out))


def hbars(items: Sequence[tuple[str, float, str]], color: str = "var(--chart)", fmt: Callable[[float], str] = _money, t: Callable = _english) -> Markup:
    """Horizontal ranking bars: (label, value, extra text for the tooltip). Plain HTML so long names wrap."""
    if not items:
        return Markup("")
    mx = max(v for _, v, _ in items) or 1.0
    rows = []
    for label, v, extra in items:
        tip = f"{label}: {fmt(v)}" + (f" ({extra})" if extra else "")
        rows.append(f'<div class="hb" {_tip(tip)}><div class="hb-l" title="{escape(label)}">{escape(label)}</div>'
                    f'<div class="hb-t"><i style="width:{max(v / mx * 100, 1.5):.1f}%;background:{color}"></i></div><div class="hb-v">{escape(fmt(v))}</div></div>')
    return Markup('<div class="hbars">' + "".join(rows) + "</div>")


def ring(done: float, total: float, size: int = 74, color: str = "var(--chart)", label: str = "done", t: Callable = _english) -> Markup:
    pct = (done / total) if total else 0.0
    r = size / 2 - 7
    c = 2 * math.pi * r
    tip = t("{done} of {total} {label} ({percent}%)", done=f"{done:g}", total=f"{total:g}", label=t(label), percent=f"{pct * 100:.0f}")
    return Markup(
        f'<svg class="ring" viewBox="0 0 {size} {size}" width="{size}" height="{size}" role="img" aria-label="{escape(tip)}" {_tip(tip)}>'
        f'<title>{escape(tip)}</title>'
        f'<circle cx="{size / 2}" cy="{size / 2}" r="{r:.1f}" fill="none" stroke="var(--line)" stroke-width="8"/>'
        f'<circle cx="{size / 2}" cy="{size / 2}" r="{r:.1f}" fill="none" stroke="{color}" stroke-width="8" stroke-linecap="round" stroke-dasharray="{c * pct:.1f} {c:.1f}" transform="rotate(-90 {size / 2} {size / 2})"/>'
        f'<text x="50%" y="52%" text-anchor="middle" dominant-baseline="middle" font-size="{size * 0.24:.0f}" font-weight="700" fill="var(--ink)">{pct * 100:.0f}%</text></svg>')


def donut(parts: Sequence[tuple[str, float, str]], size: int = 150, fmt: Callable[[float], str] = _money, center: str = "", t: Callable = _english) -> Markup:
    """Share chart. parts are (label, value, colour); hover a slice for its value and percentage."""
    total = sum(v for _, v, _ in parts if v > 0)
    if total <= 0:
        return Markup("")
    r = size / 2 - 14
    c = 2 * math.pi * r
    out = [f'<svg class="donut" viewBox="0 0 {size} {size}" width="{size}" height="{size}" role="img" aria-label="{escape(t("share chart"))}">'
           f'<circle cx="{size / 2}" cy="{size / 2}" r="{r:.1f}" fill="none" stroke="var(--line)" stroke-width="18"/>']
    acc = 0.0
    for label, v, col in parts:
        if v <= 0:
            continue
        frac = v / total
        tip = f"{label}: {fmt(v)} ({frac * 100:.0f}%)"
        out.append(f'<circle class="slice" cx="{size / 2}" cy="{size / 2}" r="{r:.1f}" fill="none" stroke="{col}" stroke-width="18" stroke-dasharray="{c * frac:.2f} {c:.2f}" '
                   f'stroke-dashoffset="{-c * acc:.2f}" transform="rotate(-90 {size / 2} {size / 2})" {_tip(tip)}><title>{escape(tip)}</title></circle>')
        acc += frac
    if center:
        out.append(f'<text x="50%" y="50%" text-anchor="middle" dominant-baseline="middle" font-size="15" font-weight="700" fill="var(--ink)">{escape(center)}</text>')
    out.append("</svg>")
    legend = "".join(f'<div class="dl"><i style="background:{col}"></i>{escape(label)} <b>{v / total * 100:.0f}%</b></div>' for label, v, col in parts if v > 0)
    return Markup(f'<div class="donutw">{"".join(out)}<div class="dlegend">{legend}</div></div>')
