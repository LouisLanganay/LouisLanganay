#!/usr/bin/env python3
"""Génère les graphiques SVG du profil GitHub (versions claire et sombre)
et stats.json à la racine du dépôt.

Données lues en direct sur l'API GitHub. Python standard, sans dépendance.

    GITHUB_TOKEN=... python3 scripts/build_charts.py

Le GITHUB_TOKEN d'Actions ne voit que les dépôts publics : les langages
et les étoiles ne portent donc que sur les dépôts publics non forkés, en
local comme en CI.
"""

import datetime as dt
import json
import math
import os
import pathlib
import urllib.request
from html import escape

LOGIN = "LouisLanganay"
API = "https://api.github.com"
ROOT = pathlib.Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"

SANS = "-apple-system,'Segoe UI',Helvetica,Arial,sans-serif"
MONO = "ui-monospace,SFMono-Regular,Menlo,Consolas,monospace"

# Thèmes alignés sur la bannière. Les verts des marques sont validés par
# le validateur dataviz (bande de luminance, contraste >= 3:1 sur le fond).
THEMES = {
    "light": {
        "bg": "#f6f7f9", "text": "#14161a", "muted": "#5d6470",
        "grid": "#dfe2e7", "mark": "#1f9d6b", "other": "#9aa1ad",
    },
    "dark": {
        "bg": "#0f1115", "text": "#f2f3f5", "muted": "#9aa1ad",
        "grid": "#262a31", "mark": "#2fa877", "other": "#5d6470",
    },
}

MONTHS = ["janv.", "févr.", "mars", "avr.", "mai", "juin",
          "juil.", "août", "sept.", "oct.", "nov.", "déc."]

# Dépôts mis en avant dans le README (tous publics).
FEATURED = ["drop", "claude-gmail-channel", "commit-ai-generator", "AREA",
            "Zombie-Quarter-Rampage-my_rpg", "Raytracer"]

SCHEMA_VERSION = 1

NNBSP = " "


# ---------------------------------------------------------------- API ----

def _token():
    return os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")


def _request(url, body=None):
    headers = {"Accept": "application/vnd.github+json",
               "User-Agent": f"{LOGIN}-profile-charts"}
    if _token():
        headers["Authorization"] = f"Bearer {_token()}"
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp), resp.headers


def rest(path):
    return _request(API + path)[0]


def rest_all(path):
    items, page = [], 1
    while True:
        sep = "&" if "?" in path else "?"
        chunk = rest(f"{path}{sep}per_page=100&page={page}")
        items.extend(chunk)
        if len(chunk) < 100:
            return items
        page += 1


def graphql(query, variables):
    out = _request(API + "/graphql", {"query": query, "variables": variables})[0]
    if out.get("errors"):
        raise RuntimeError(out["errors"])
    return out["data"]


def month_starts(today, n=12):
    """Premiers jours des n derniers mois COMPLETS (le mois en cours exclu)."""
    first = dt.date(today.year, today.month, 1)
    out = []
    for _ in range(n):
        first = (first - dt.timedelta(days=1)).replace(day=1)
        out.append(first)
    return out[::-1]


def next_month(d):
    return (d.replace(day=28) + dt.timedelta(days=4)).replace(day=1)


def contributions(starts):
    """Total et part privée par mois : un contributionsCollection par mois.

    restrictedContributionsCount est le compte agrégé des contributions
    privées que GitHub affiche déjà sur le profil ; aucun nom de dépôt.
    """
    parts = []
    for i, s in enumerate(starts):
        end = next_month(s) - dt.timedelta(days=1)
        parts.append(
            f'm{i}: contributionsCollection(from: "{s.isoformat()}T00:00:00Z", '
            f'to: "{end.isoformat()}T23:59:59Z") '
            "{ restrictedContributionsCount contributionCalendar { totalContributions } }")
    query = "query($login: String!) { user(login: $login) { %s } }" % " ".join(parts)
    data = graphql(query, {"login": LOGIN})["user"]
    months = []
    for i, s in enumerate(starts):
        m = data[f"m{i}"]
        total = m["contributionCalendar"]["totalContributions"]
        private = m["restrictedContributionsCount"]
        months.append({"year": s.year, "month": s.month, "total": total,
                       "private": private, "public": total - private})
    return months


def collect(now):
    today = now.date()
    user = rest(f"/users/{LOGIN}")
    # /users/{login}/repos ne renvoie que les dépôts publics, quel que soit
    # le jeton : même périmètre en local et en CI.
    repos = [r for r in rest_all(f"/users/{LOGIN}/repos?type=owner")
             if not r["fork"] and not r["private"]]

    languages = {}
    for repo in repos:
        for lang, size in rest(f"/repos/{LOGIN}/{repo['name']}/languages").items():
            languages[lang] = languages.get(lang, 0) + size

    months = contributions(month_starts(today))

    created = dt.date.fromisoformat(user["created_at"][:10])
    years = today.year - created.year - (
        (today.month, today.day) < (created.month, created.day))

    by_name = {r["name"]: r for r in repos}
    featured = [by_name[n] for n in FEATURED if n in by_name]

    return {
        "now": now,
        "user": user,
        "public_repos": user["public_repos"],
        "stars": sum(r["stargazers_count"] for r in repos),
        "repo_count": len(repos),
        "created": created,
        "years": years,
        "languages": languages,
        "months": months,
        "total": sum(m["total"] for m in months),
        "private": sum(m["private"] for m in months),
        "featured": featured,
    }


# ------------------------------------------------------------- helpers ----

def fr_int(n):
    return f"{n:,}".replace(",", NNBSP)


def fr_pct(x):
    return f"{x:.1f}".replace(".", ",") + NNBSP + "%"


def nice_ceiling(v):
    """Plus petit plafond d'axe propre (3 à 5 graduations de 1, 2, 2,5 ou 5 x 10^n)."""
    if v <= 0:
        return 4, 1
    target = v * 1.08  # place pour l'étiquette du pic
    best = None
    mag = 10 ** math.floor(math.log10(target / 5))
    for m in (mag, mag * 10):
        for f in (1, 2, 2.5, 5):
            step = f * m
            for k in (3, 4, 5):
                top = step * k
                if top >= target and (best is None or top < best[0]):
                    best = (top, step)
    return int(best[0]), best[1]


def text(x, y, s, *, size=14, fill, family=SANS, weight=None, anchor=None,
         spacing=None, opacity=None):
    attrs = [f'x="{x:.1f}"', f'y="{y:.1f}"', f'font-family="{family}"',
             f'font-size="{size}"', f'fill="{fill}"']
    if weight:
        attrs.append(f'font-weight="{weight}"')
    if anchor:
        attrs.append(f'text-anchor="{anchor}"')
    if spacing:
        attrs.append(f'letter-spacing="{spacing}"')
    if opacity is not None:
        attrs.append(f'opacity="{opacity}"')
    return f"<text {' '.join(attrs)}>{escape(s)}</text>"


def bar_path(x, y, w, h, r=4, side="top"):
    """Barre arrondie seulement à l'extrémité libre, ancrée sur la base."""
    r = min(r, w / 2, h / 2) if w > 0 and h > 0 else 0
    if side == "top":  # colonne verticale
        return (f"M{x:.1f},{y + h:.1f} L{x:.1f},{y + r:.1f} "
                f"Q{x:.1f},{y:.1f} {x + r:.1f},{y:.1f} L{x + w - r:.1f},{y:.1f} "
                f"Q{x + w:.1f},{y:.1f} {x + w:.1f},{y + r:.1f} L{x + w:.1f},{y + h:.1f} Z")
    # barre horizontale, extrémité droite
    return (f"M{x:.1f},{y:.1f} L{x + w - r:.1f},{y:.1f} "
            f"Q{x + w:.1f},{y:.1f} {x + w:.1f},{y + r:.1f} L{x + w:.1f},{y + h - r:.1f} "
            f"Q{x + w:.1f},{y + h:.1f} {x + w - r:.1f},{y + h:.1f} L{x:.1f},{y + h:.1f} Z")


def svg(width, height, label, body, bg):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
            f'viewBox="0 0 {width} {height}" role="img" aria-label="{escape(label)}">\n'
            f"<title>{escape(label)}</title>\n"
            f'<rect width="{width}" height="{height}" rx="18" fill="{bg}"/>\n'
            + "\n".join(body) + "\n</svg>\n")


# -------------------------------------------------------------- charts ----

def chart_stats(d, t):
    W, H = 1200, 150
    tiles = [
        (fr_int(d["public_repos"]), "DÉPÔTS PUBLICS"),
        (fr_int(d["stars"]), "ÉTOILES CUMULÉES"),
        (fr_int(d["total"]), "CONTRIBUTIONS SUR 12 MOIS"),
        (str(d["years"]), "ANS SUR GITHUB"),
    ]
    body = []
    tw = W / len(tiles)
    for i, (value, label) in enumerate(tiles):
        x = i * tw + 48
        if i:
            body.append(f'<line x1="{i * tw:.1f}" x2="{i * tw:.1f}" y1="36" y2="114" '
                        f'stroke="{t["grid"]}" stroke-width="1"/>')
        body.append(text(x, 84, value, size=44, weight="700", fill=t["text"], spacing="-1"))
        body.append(text(x + 2, 112, label, size=12, family=MONO, fill=t["muted"], spacing="1"))
    label = ("Chiffres clés : " + ", ".join(f"{v} {l.lower()}" for v, l in tiles))
    return svg(W, H, label, body, t["bg"])


def chart_activity(d, t):
    W, H = 1200, 400
    left, right, top, base = 104, 1152, 132, 330
    months = d["months"]
    peak = max(m["total"] for m in months)
    ymax, step = nice_ceiling(peak)
    plot_h = base - top
    band = (right - left) / len(months)
    bw = 44
    generated = d["now"].date()

    body = [
        text(48, 62, "Contributions GitHub", size=24, weight="700", fill=t["text"]),
        text(48, 90, f"12 derniers mois complets, dont {fr_int(d['private'])} dans des dépôts privés",
             size=15, fill=t["muted"]),
        text(1152, 66, fr_int(d["total"]), size=40, weight="700", fill=t["text"],
             anchor="end", spacing="-1"),
        text(1152, 90, f"GÉNÉRÉ LE {generated.strftime('%d/%m/%Y')}", size=12, family=MONO,
             fill=t["muted"], anchor="end", spacing="1"),
    ]
    tick = 0
    while tick <= ymax:
        y = base - plot_h * tick / ymax
        dash = '' if tick == 0 else ' stroke-dasharray="2 6"'
        body.append(f'<line x1="{left - 8}" x2="{right}" y1="{y:.1f}" y2="{y:.1f}" '
                    f'stroke="{t["grid"]}" stroke-width="1"{dash}/>')
        body.append(text(left - 16, y + 4, fr_int(int(tick)), size=12, family=MONO,
                         fill=t["muted"], anchor="end"))
        tick += step

    peak_i = max(range(len(months)), key=lambda i: months[i]["total"])
    last = len(months) - 1
    for i, mo in enumerate(months):
        y_, m, count = mo["year"], mo["month"], mo["total"]
        cx = left + band * i + band / 2
        h = plot_h * count / ymax
        name = f"{MONTHS[m - 1]} {y_}"
        if h > 0:
            body.append(f'<path d="{bar_path(cx - bw / 2, base - h, bw, h)}" fill="{t["mark"]}">'
                        f"<title>{escape(name)} : {fr_int(count)} contributions "
                        f"({fr_int(mo['public'])} publiques, {fr_int(mo['private'])} privées)"
                        "</title></path>")
        if i in (peak_i, last):
            body.append(text(cx, base - h - 10, fr_int(count), size=13, family=MONO,
                             fill=t["text"], anchor="middle"))
        label = MONTHS[m - 1] + (f" {str(y_)[2:]}" if m == 1 or i == 0 else "")
        body.append(text(cx, base + 24, label, size=13, family=MONO,
                         fill=t["muted"], anchor="middle"))

    first, end_ = months[0], months[-1]
    label = (f"Contributions GitHub par mois, de {MONTHS[first['month'] - 1]} {first['year']} "
             f"à {MONTHS[end_['month'] - 1]} {end_['year']} : " +
             ", ".join(f"{MONTHS[mo['month'] - 1]} {mo['year']} {mo['total']}" for mo in months) +
             f". Total {d['total']}.")
    return svg(W, H, label, body, t["bg"])


def bars_panel(title, subtitle, rows, domain, t, label):
    """Barres horizontales classées, une teinte, étiquettes directes."""
    W = 1200
    top, row_h, bh = 128, 36, 16
    H = top + row_h * len(rows) + 52
    x0, x1 = 200, 1040
    body = [
        text(48, 62, title, size=24, weight="700", fill=t["text"]),
        text(48, 90, subtitle, size=15, fill=t["muted"]),
    ]
    for i, (name, pct, is_other) in enumerate(rows):
        y = top + row_h * i
        w = (x1 - x0) * pct / domain
        body.append(text(x0 - 16, y + 13, name, size=16, fill=t["muted"] if is_other else t["text"],
                         anchor="end"))
        color = t["other"] if is_other else t["mark"]
        body.append(f'<path d="{bar_path(x0, y, max(w, 2), bh, side="right")}" fill="{color}">'
                    f"<title>{escape(name)} : {fr_pct(pct)}</title></path>")
        body.append(text(x0 + w + 10, y + 13, fr_pct(pct), size=14, family=MONO, fill=t["text"]))
    body.append(f'<line x1="{x0}" x2="{x0}" y1="{top - 8}" y2="{top + row_h * len(rows) - 12}" '
                f'stroke="{t["grid"]}" stroke-width="1"/>')
    return W, H, body


def language_rows(languages, top_n=6):
    total = sum(languages.values())
    ranked = sorted(languages.items(), key=lambda kv: (-kv[1], kv[0]))
    rows = [(n, 100 * v / total, False) for n, v in ranked[:top_n]]
    rest = sum(v for _, v in ranked[top_n:])
    if rest:
        rows.append(("Autres", 100 * rest / total, True))
    return rows


def chart_languages(d, t):
    rows = language_rows(d["languages"])
    domain = math.ceil(max(p for _, p, _ in rows) / 10) * 10
    W, H, body = bars_panel(
        "Langages",
        f"Part en octets, {d['repo_count']} dépôts publics non forkés",
        rows, domain, t, "")
    body.append(text(48, H - 28, "MIS À JOUR CHAQUE MOIS PAR GITHUB ACTIONS",
                     size=12, family=MONO, fill=t["muted"], spacing="1"))
    label = "Langages des dépôts publics, part en octets : " + ", ".join(
        f"{n} {fr_pct(p)}" for n, p, _ in rows)
    return svg(W, H, label, body, t["bg"])


# ---------------------------------------------------------------- main ----

def write(path, content):
    if path.exists() and path.read_text() == content:
        return False
    path.write_text(content)
    return True


def stats_json(d):
    """Schéma public et stable (schema_version) servi par raw.githubusercontent.com."""
    user = d["user"]
    months = d["months"]
    last_day = next_month(dt.date(months[-1]["year"], months[-1]["month"], 1)) - dt.timedelta(days=1)
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": d["now"].strftime("%Y-%m-%dT%H:%M:%SZ"),
        "profile": {
            "login": user["login"],
            "name": user["name"],
            "url": user["html_url"],
            "created_at": user["created_at"],
            "years_on_github": d["years"],
        },
        "totals": {
            "public_repos": d["public_repos"],
            "original_public_repos": d["repo_count"],
            "stars": d["stars"],
            "contributions_last_12_months": d["total"],
        },
        "contributions": {
            "from": f"{months[0]['year']}-{months[0]['month']:02d}-01",
            "to": last_day.isoformat(),
            "total": d["total"],
            "public": d["total"] - d["private"],
            "private": d["private"],
            "months": [{"month": f"{m['year']}-{m['month']:02d}", "total": m["total"],
                        "public": m["public"], "private": m["private"]} for m in months],
        },
        "languages": {
            "basis": "bytes in original public repositories",
            "items": [{"name": "Other" if other else n, "percent": round(p, 1)}
                      for n, p, other in language_rows(d["languages"])],
        },
        "featured_repos": [{
            "name": r["name"],
            "description": r["description"],
            "url": r["html_url"],
            "stars": r["stargazers_count"],
            "language": r["language"],
        } for r in d["featured"]],
    }


def main():
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    d = collect(now)

    changed = []
    for mode, t in THEMES.items():
        outputs = {
            f"stats-{mode}.svg": chart_stats(d, t),
            f"activity-{mode}.svg": chart_activity(d, t),
            f"languages-{mode}.svg": chart_languages(d, t),
        }
        for name, content in outputs.items():
            if write(ASSETS / name, content):
                changed.append(name)

    payload = json.dumps(stats_json(d), ensure_ascii=False, indent=2) + "\n"
    if write(ROOT / "stats.json", payload):
        changed.append("stats.json")
    print("changed:", ", ".join(changed) or "none")


if __name__ == "__main__":
    main()
