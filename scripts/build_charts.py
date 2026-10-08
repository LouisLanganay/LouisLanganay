#!/usr/bin/env python3
"""Génère les graphiques SVG du profil GitHub (versions claire et sombre).

Données lues en direct sur l'API GitHub, sauf le bloc WakaTime, figé au
03/10/2026. Python standard, sans dépendance.

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

# Historique WakaTime, chiffres figés (le compte n'est plus suivi).
WAKATIME = {
    "total": "2 549 h 44 min",
    "from": "02/11/2022",
    "to": "03/10/2026",
    "langs": [("TypeScript", 29.8), ("C", 19.2), ("JavaScript", 13.7),
              ("C++", 9.7), ("Python", 4.7)],
}

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


CONTRIB_QUERY = """
query($login: String!, $from: DateTime!, $to: DateTime!) {
  user(login: $login) {
    contributionsCollection(from: $from, to: $to) {
      totalCommitContributions
      restrictedContributionsCount
      contributionCalendar {
        totalContributions
        weeks { contributionDays { date contributionCount } }
      }
    }
  }
}
"""


def collect(today):
    user = rest(f"/users/{LOGIN}")
    # /users/{login}/repos ne renvoie que les dépôts publics, quel que soit
    # le jeton : même périmètre en local et en CI.
    repos = [r for r in rest_all(f"/users/{LOGIN}/repos?type=owner")
             if not r["fork"] and not r["private"]]

    languages = {}
    for repo in repos:
        for lang, size in rest(f"/repos/{LOGIN}/{repo['name']}/languages").items():
            languages[lang] = languages.get(lang, 0) + size

    # 12 mois glissants : les 11 mois pleins précédents + le mois en cours.
    first = dt.date(today.year, today.month, 1)
    for _ in range(11):
        first = (first - dt.timedelta(days=1)).replace(day=1)
    data = graphql(CONTRIB_QUERY, {
        "login": LOGIN,
        "from": f"{first.isoformat()}T00:00:00Z",
        "to": f"{today.isoformat()}T23:59:59Z",
    })["user"]["contributionsCollection"]

    months = []
    cursor = first
    while cursor <= today:
        months.append((cursor.year, cursor.month))
        cursor = (cursor.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
    per_month = {m: 0 for m in months}
    for week in data["contributionCalendar"]["weeks"]:
        for day in week["contributionDays"]:
            d = dt.date.fromisoformat(day["date"])
            if (d.year, d.month) in per_month and d <= today:
                per_month[(d.year, d.month)] += day["contributionCount"]

    created = dt.date.fromisoformat(user["created_at"][:10])
    years = today.year - created.year - (
        (today.month, today.day) < (created.month, created.day))

    return {
        "today": today,
        "public_repos": user["public_repos"],
        "stars": sum(r["stargazers_count"] for r in repos),
        "repo_count": len(repos),
        "created": created,
        "years": years,
        "languages": languages,
        "months": [(y, m, per_month[(y, m)]) for y, m in months],
        "total": data["contributionCalendar"]["totalContributions"],
        "private": data["restrictedContributionsCount"],
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
    peak = max(c for _, _, c in months)
    ymax, step = nice_ceiling(peak)
    plot_h = base - top
    band = (right - left) / len(months)
    bw = 44
    today = d["today"]

    body = [
        text(48, 62, "Contributions GitHub", size=24, weight="700", fill=t["text"]),
        text(48, 90, f"12 derniers mois, dont {fr_int(d['private'])} dans des dépôts privés",
             size=15, fill=t["muted"]),
        text(1152, 66, fr_int(d["total"]), size=40, weight="700", fill=t["text"],
             anchor="end", spacing="-1"),
        text(1152, 90, f"AU {today.strftime('%d/%m/%Y')}", size=12, family=MONO,
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

    peak_i = max(range(len(months)), key=lambda i: months[i][2])
    for i, (y_, m, count) in enumerate(months):
        cx = left + band * i + band / 2
        h = plot_h * count / ymax
        current = i == len(months) - 1
        name = f"{MONTHS[m - 1]} {y_}"
        if h > 0:
            op = ' fill-opacity="0.45"' if current else ""
            body.append(f'<path d="{bar_path(cx - bw / 2, base - h, bw, h)}" fill="{t["mark"]}"{op}>'
                        f"<title>{escape(name)} : {fr_int(count)} contributions"
                        f"{' (mois en cours)' if current else ''}</title></path>")
        if i == peak_i or current:
            body.append(text(cx, base - h - 10, fr_int(count), size=13, family=MONO,
                             fill=t["text"], anchor="middle"))
        label = MONTHS[m - 1] + (f" {str(y_)[2:]}" if m == 1 or i == 0 else "")
        body.append(text(cx, base + 24, label, size=13, family=MONO,
                         fill=t["muted"], anchor="middle"))
        if current:
            body.append(text(cx, base + 42, "en cours", size=11, family=MONO,
                             fill=t["muted"], anchor="middle"))

    first = months[0]
    label = (f"Contributions GitHub par mois, de {MONTHS[first[1] - 1]} {first[0]} "
             f"à {MONTHS[today.month - 1]} {today.year} : " +
             ", ".join(f"{MONTHS[m - 1]} {y_} {c}" for y_, m, c in months) +
             f". Total {d['total']}.")
    return svg(W, H, label, body, t["bg"])


def bars_panel(title, subtitle, rows, domain, t, label):
    """Barres horizontales classées, une teinte, étiquettes directes."""
    W = 600
    top, row_h, bh = 132, 38, 16
    H = top + row_h * len(rows) + 52
    x0, x1 = 158, 486
    body = [
        text(36, 58, title, size=25, weight="700", fill=t["text"]),
        text(36, 88, subtitle, size=16, fill=t["muted"]),
    ]
    for i, (name, pct, is_other) in enumerate(rows):
        y = top + row_h * i
        w = (x1 - x0) * pct / domain
        body.append(text(x0 - 16, y + 14, name, size=17, fill=t["muted"] if is_other else t["text"],
                         anchor="end"))
        color = t["other"] if is_other else t["mark"]
        body.append(f'<path d="{bar_path(x0, y, max(w, 2), bh, side="right")}" fill="{color}">'
                    f"<title>{escape(name)} : {fr_pct(pct)}</title></path>")
        body.append(text(x0 + w + 10, y + 14, fr_pct(pct), size=15, family=MONO, fill=t["text"]))
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


def wakatime_rows():
    rows = [(n, p, False) for n, p in WAKATIME["langs"]]
    rows.append(("Autres", round(100 - sum(p for _, p in WAKATIME["langs"]), 1), True))
    return rows


def charts_languages(d, t, domain):
    rows = language_rows(d["languages"])
    W, H, body = bars_panel(
        "Langages",
        f"Part en octets, {d['repo_count']} dépôts publics non forkés",
        rows, domain, t, "")
    body.append(text(36, H - 28, "MIS À JOUR CHAQUE JOUR PAR GITHUB ACTIONS",
                     size=12, family=MONO, fill=t["muted"], spacing="1"))
    label = "Langages des dépôts publics, part en octets : " + ", ".join(
        f"{n} {fr_pct(p)}" for n, p, _ in rows)
    return W, H, body, label


def charts_wakatime(t, domain, height):
    rows = wakatime_rows()
    W, H, body = bars_panel(
        "Temps de code, WakaTime",
        f"{WAKATIME['total']} du {WAKATIME['from']} au {WAKATIME['to']}",
        rows, domain, t, "")
    body.append(text(36, height - 28, f"HISTORIQUE FIGÉ AU {WAKATIME['to']}, NON MIS À JOUR",
                     size=12, family=MONO, fill=t["muted"], spacing="1"))
    label = (f"Historique WakaTime figé au {WAKATIME['to']} : {WAKATIME['total']} de code, " +
             ", ".join(f"{n} {fr_pct(p)}" for n, p, _ in rows))
    return W, height, body, label


# ---------------------------------------------------------------- main ----

def write(name, content):
    path = ASSETS / name
    if path.exists() and path.read_text() == content:
        return False
    path.write_text(content)
    return True


def main():
    today = dt.datetime.now(dt.timezone.utc).date()
    d = collect(today)
    lang_rows = language_rows(d["languages"])
    peak = max([p for _, p, _ in lang_rows] + [p for _, p, _ in wakatime_rows()])
    domain = math.ceil(peak / 10) * 10  # même échelle pour les deux panneaux

    changed = []
    for mode, t in THEMES.items():
        lw, lh, lbody, llabel = charts_languages(d, t, domain)
        ww, wh, wbody, wlabel = charts_wakatime(t, domain, lh)
        lh = max(lh, wh)
        outputs = {
            f"stats-{mode}.svg": chart_stats(d, t),
            f"activity-{mode}.svg": chart_activity(d, t),
            f"languages-{mode}.svg": svg(lw, lh, llabel, lbody, t["bg"]),
            f"wakatime-{mode}.svg": svg(ww, lh, wlabel, wbody, t["bg"]),
        }
        for name, content in outputs.items():
            if write(name, content):
                changed.append(name)

    print(json.dumps({
        "public_repos": d["public_repos"], "stars": d["stars"],
        "total_12m": d["total"], "private": d["private"], "years": d["years"],
        "months": d["months"],
        "languages": [(n, round(p, 1)) for n, p, _ in lang_rows],
        "changed": changed,
    }, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
