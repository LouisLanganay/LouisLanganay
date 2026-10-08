#!/usr/bin/env python3
"""Statistiques d'utilisation de Claude Code, lues dans les transcripts locaux.

Tourne SUR LE SERVEUR (une GitHub Action ne voit pas ces fichiers) :

    python3 scripts/claude_stats.py [--projects ~/.claude/projects] [--state FICHIER]

Lit en streaming ~/.claude/projects/*/*.jsonl (sessions principales) et
~/.claude/projects/*/*/subagents/*.jsonl (sous-agents), ne garde que des
agrégats : jetons, nombre de messages, sessions, heatmap jour x heure
(Europe/Paris), modèles. Aucun contenu de message, nom de projet, chemin,
identifiant de session ni coût n'est écrit.

Écrit claude-stats.json à la racine du dépôt et assets/claude-*-{light,dark}.svg.

--state garde les agrégats mensuels d'une exécution à l'autre (hors dépôt),
pour ne pas perdre un mois si des transcripts anciens sont supprimés.
"""

import argparse
import datetime as dt
import glob
import json
import os
import pathlib
import sys
from html import escape
from zoneinfo import ZoneInfo

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from build_charts import (MONO, MONTHS, NNBSP, ROOT, ASSETS, THEMES,  # noqa: E402
                          bar_path, fr_int, nice_ceiling, svg, text, write)

PARIS = ZoneInfo("Europe/Paris")
SCHEMA_VERSION = 1
KINDS = ["output", "input", "cache_read", "cache_write"]
USAGE_KEYS = {"input": "input_tokens", "output": "output_tokens",
              "cache_read": "cache_read_input_tokens",
              "cache_write": "cache_creation_input_tokens"}
DAYS = ["lun.", "mar.", "mer.", "jeu.", "ven.", "sam.", "dim."]

# Rampe séquentielle d'une seule teinte (vert), validée --ordinal par le
# validateur dataviz sur chaque fond ; « zero » = case sans activité.
RAMPS = {
    "light": {"zero": "#e6e8ec", "steps": ["#66bf9a", "#3fa87c", "#288c65", "#1b6e4f", "#114f38"]},
    "dark": {"zero": "#1b1e24", "steps": ["#1f5f45", "#237a57", "#2a986b", "#48b986", "#86dbb2"]},
}


# ------------------------------------------------------------- lecture ----

def empty_month():
    return {"tokens": {k: 0 for k in KINDS}, "messages": 0,
            "agent_messages": 0, "agent_output": 0,
            "sessions": 0, "subagents": 0,
            "heatmap": [[0] * 24 for _ in range(7)], "models": {}}


def scan(projects):
    """Un passage en streaming ; dédoublonnage par message.id."""
    main_files = glob.glob(os.path.join(projects, "*", "*.jsonl"))
    agent_files = glob.glob(os.path.join(projects, "*", "*", "subagents", "*.jsonl"))
    seen = {}  # message.id -> index dans records
    records = []  # [ts, is_agent, model, {usage}]
    file_first = []  # (premier horodatage, is_agent) par fichier
    for is_agent, files in ((False, main_files), (True, agent_files)):
        for path in files:
            first = None
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    if '"assistant"' not in line or '"usage"' not in line:
                        continue
                    try:
                        row = json.loads(line)
                    except ValueError:
                        continue
                    if row.get("type") != "assistant":
                        continue
                    msg = row.get("message") or {}
                    usage = msg.get("usage")
                    mid = msg.get("id")
                    model = msg.get("model") or "inconnu"
                    ts = row.get("timestamp")
                    if not usage or not mid or not ts or model == "<synthetic>":
                        continue
                    when = dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
                    if first is None or when < first:
                        first = when
                    vals = {k: int(usage.get(src) or 0) for k, src in USAGE_KEYS.items()}
                    if mid in seen:
                        rec = records[seen[mid]]
                        for k in KINDS:  # les lignes d'un même message répètent l'usage
                            rec[3][k] = max(rec[3][k], vals[k])
                        if when < rec[0]:
                            rec[0] = when
                        continue
                    seen[mid] = len(records)
                    records.append([when, is_agent, model, vals])
            if first is not None:
                file_first.append((first, is_agent))
    return records, file_first


def aggregate(records, file_first):
    months = {}
    first_ts = None
    for when, is_agent, model, vals in records:
        local = when.astimezone(PARIS)
        key = f"{local.year}-{local.month:02d}"
        m = months.setdefault(key, empty_month())
        for k in KINDS:
            m["tokens"][k] += vals[k]
        m["messages"] += 1
        m["heatmap"][local.weekday()][local.hour] += 1
        mm = m["models"].setdefault(model, {"messages": 0, "output": 0})
        mm["messages"] += 1
        mm["output"] += vals["output"]
        if is_agent:
            m["agent_messages"] += 1
            m["agent_output"] += vals["output"]
        if first_ts is None or when < first_ts:
            first_ts = when
    for when, is_agent in file_first:
        local = when.astimezone(PARIS)
        m = months.setdefault(f"{local.year}-{local.month:02d}", empty_month())
        m["subagents" if is_agent else "sessions"] += 1
    return months, first_ts


def merge_state(months, first_ts, state_path):
    """Garde, mois par mois, la version qui compte le plus de messages."""
    if not state_path:
        return months, first_ts
    path = pathlib.Path(state_path)
    if path.exists():
        old = json.loads(path.read_text())
        for key, rec in old.get("months", {}).items():
            if rec["messages"] > months.get(key, {"messages": -1})["messages"]:
                months[key] = rec
        if old.get("first_ts"):
            prev = dt.datetime.fromisoformat(old["first_ts"])
            first_ts = min(first_ts, prev) if first_ts else prev
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"first_ts": first_ts.isoformat() if first_ts else None,
                                "months": months}))
    return months, first_ts


# ------------------------------------------------------------- format ----

def compact(n):
    """Jetons en notation courte française : 1,23 Md, 456 M, 12 k."""
    for div, unit in ((1e9, "Md"), (1e6, "M"), (1e3, "k")):
        if n >= div:
            v = n / div
            s = f"{v:.2f}" if v < 10 else f"{v:.1f}" if v < 100 else f"{v:.0f}"
            return s.replace(".", ",") + NNBSP + unit
    return str(n)


def pct(x):
    return f"{x:.0f}{NNBSP}%"


def month_label(key, with_year=False):
    y, m = key.split("-")
    return MONTHS[int(m) - 1] + (f" {y[2:]}" if with_year else "")


# -------------------------------------------------------------- charts ----

KIND_LABELS = {"output": "Sortie (écrits par Claude)", "input": "Entrée",
               "cache_read": "Lecture de cache", "cache_write": "Écriture de cache"}


def chart_key(s, t):
    W, H = 1200, 196
    tot = s["totals"]
    tiles = [
        (compact(tot["tokens"]["total"]), "JETONS AU TOTAL"),
        (compact(tot["tokens"]["output"]), "JETONS ÉCRITS PAR CLAUDE"),
        (fr_int(tot["sessions"]), "SESSIONS"),
        (pct(100 * tot["subagent_share"]["messages"]), "MESSAGES DE SOUS-AGENTS"),
    ]
    body = [text(48, 50, f"Claude Code sur mon serveur, agents compris, hors Mac. "
                         f"Depuis le {s['period']['from_display']}.",
                 size=15, fill=t["muted"])]
    tw = W / len(tiles)
    for i, (value, label) in enumerate(tiles):
        x = i * tw + 48
        if i:
            body.append(f'<line x1="{i * tw:.1f}" x2="{i * tw:.1f}" y1="80" y2="158" '
                        f'stroke="{t["grid"]}" stroke-width="1"/>')
        body.append(text(x, 128, value, size=44, weight="700", fill=t["text"], spacing="-1"))
        body.append(text(x + 2, 156, label, size=12, family=MONO, fill=t["muted"], spacing="1"))
    label = ("Claude Code sur mon serveur, agents compris : " +
             ", ".join(f"{v} {l.lower()}" for v, l in tiles))
    return svg(W, H, label, body, t["bg"])


def chart_tokens(s, t):
    """Petits multiples : une échelle par type de jeton (ordres de grandeur trop
    différents pour un seul axe)."""
    W, H = 1200, 330
    months = s["chart_months"]
    body = [
        text(48, 62, "Jetons par mois", size=24, weight="700", fill=t["text"]),
        text(48, 90, "Une échelle par panneau, le mois en cours est en clair",
             size=15, fill=t["muted"]),
    ]
    pw, gap, x_start = 258, 16, 48
    top, base = 170, 278
    for p, kind in enumerate(KINDS):
        px = x_start + p * (pw + gap)
        vals = [m["tokens"][kind] for m in months]
        peak = max(vals) or 1
        ymax, _ = nice_ceiling(peak)
        body.append(text(px, 132, KIND_LABELS[kind], size=14, fill=t["text"], weight="600"))
        body.append(text(px, 152, f"{compact(sum(vals))} sur la période", size=12,
                         family=MONO, fill=t["muted"]))
        body.append(f'<line x1="{px}" x2="{px + pw}" y1="{base}" y2="{base}" '
                    f'stroke="{t["grid"]}" stroke-width="1"/>')
        band = pw / max(len(months), 1)
        bw = min(28, band * 0.6)
        for i, m in enumerate(months):
            cx = px + band * i + band / 2
            h = (base - top) * m["tokens"][kind] / ymax
            op = ' fill-opacity="0.45"' if not m["complete"] else ""
            if h > 0:
                body.append(f'<path d="{bar_path(cx - bw / 2, base - h, bw, h)}" fill="{t["mark"]}"{op}>'
                            f"<title>{escape(month_label(m['month'], True))} : "
                            f"{compact(m['tokens'][kind])}</title></path>")
            if m["tokens"][kind] == peak:
                body.append(text(cx, base - h - 8, compact(peak), size=12, family=MONO,
                                 fill=t["text"], anchor="middle"))
            if len(months) <= 6 or i in (0, len(months) - 1):
                body.append(text(cx, base + 20, month_label(m["month"], i == 0), size=12,
                                 family=MONO, fill=t["muted"], anchor="middle"))
    label = "Jetons Claude par mois : " + "; ".join(
        f"{month_label(m['month'], True)} " + ", ".join(
            f"{KIND_LABELS[k].lower()} {compact(m['tokens'][k])}" for k in KINDS)
        for m in months)
    return svg(W, H, label, body, t["bg"])


def chart_heatmap(s, t, mode):
    x0, y0, cell, g = 112, 132, 38, 4
    W, H = 1200, y0 + 7 * (cell + g) + 92
    grid = s["heatmap"]["counts"]
    ramp = RAMPS[mode]
    # 5 classes par quantiles des cases non nulles : une heure de pointe isolée
    # n'écrase pas le reste de la grille.
    nz = sorted(v for r in grid for v in r if v)
    edges = [nz[min(len(nz) - 1, int(len(nz) * q))] for q in (0.2, 0.4, 0.6, 0.8)] + [nz[-1]] if nz else [1] * 5

    def color(v):
        if v == 0:
            return ramp["zero"]
        for i, e in enumerate(edges):
            if v <= e:
                return ramp["steps"][i]
        return ramp["steps"][-1]

    body = [
        text(48, 62, "Quand je travaille avec Claude", size=24, weight="700", fill=t["text"]),
        text(48, 90, "Messages de Claude par jour et par heure (heure de Paris), agents compris",
             size=15, fill=t["muted"]),
    ]
    for d in range(7):
        y = y0 + d * (cell + g)
        body.append(text(x0 - 16, y + cell / 2 + 5, DAYS[d], size=13, family=MONO,
                         fill=t["muted"], anchor="end"))
        for h in range(24):
            x = x0 + h * (cell + g)
            v = grid[d][h]
            body.append(f'<rect x="{x}" y="{y}" width="{cell}" height="{cell}" rx="4" '
                        f'fill="{color(v)}"><title>{DAYS[d]} {h} h : {fr_int(v)} messages'
                        f"</title></rect>")
    for h in range(0, 24, 3):
        x = x0 + h * (cell + g) + cell / 2
        body.append(text(x, y0 + 7 * (cell + g) + 14, f"{h} h", size=12, family=MONO,
                         fill=t["muted"], anchor="middle"))
    # Légende de la rampe.
    ly = H - 44
    body.append(text(x0, ly + 13, "moins", size=12, family=MONO, fill=t["muted"]))
    lx = x0 + 56
    for i, c in enumerate([ramp["zero"]] + ramp["steps"]):
        body.append(f'<rect x="{lx + i * 22}" y="{ly}" width="18" height="18" rx="3" fill="{c}"/>')
    body.append(text(lx + 6 * 22 + 6, ly + 13, "plus", size=12, family=MONO, fill=t["muted"]))
    top = s["heatmap"]["busiest_hours"]
    body.append(text(x0 + 24 * (cell + g) - g, ly + 13, "HEURES LES PLUS ACTIVES : " + ", ".join(
        f"{h} H" for h in top), size=12, family=MONO, fill=t["muted"], anchor="end", spacing="1"))
    label = ("Heatmap des messages de Claude par jour de la semaine et heure (Paris). "
             "Heures les plus actives : " + ", ".join(f"{h} h" for h in top))
    return svg(W, H, label, body, t["bg"])


# ---------------------------------------------------------------- main ----

def build(months, first_ts, now):
    now_local = now.astimezone(PARIS)
    current = f"{now_local.year}-{now_local.month:02d}"
    keys = sorted(months)
    # Le mois en cours n'est montré qu'à partir du 2 (le cron passe le 1er).
    if now_local.day == 1:
        keys = [k for k in keys if k < current]
    keys = keys[-12:]
    period_months = [months[k] for k in keys]

    def sum_tokens(ms):
        out = {k: sum(m["tokens"][k] for m in ms) for k in KINDS}
        out["total"] = sum(out.values())
        return out

    all_months = [months[k] for k in sorted(months)]
    heat = [[sum(m["heatmap"][d][h] for m in period_months) for h in range(24)] for d in range(7)]
    by_hour = [sum(heat[d][h] for d in range(7)) for h in range(24)]
    busiest = sorted(range(24), key=lambda h: -by_hour[h])[:3]
    by_day = [sum(r) for r in heat]
    models = {}
    for m in period_months:
        for name, v in m["models"].items():
            agg = models.setdefault(name, {"messages": 0, "output": 0})
            agg["messages"] += v["messages"]
            agg["output"] += v["output"]
    msg_total = sum(v["messages"] for v in models.values()) or 1
    total_msgs = sum(m["messages"] for m in all_months) or 1
    total_out = sum(m["tokens"]["output"] for m in all_months) or 1
    first_local = first_ts.astimezone(PARIS)

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "Claude Code transcripts on my own server, autonomous agents and "
                  "sub-agents included; other machines (Mac) excluded",
        "timezone": "Europe/Paris",
        "period": {"from": first_local.date().isoformat(), "to": now_local.date().isoformat(),
                   "from_display": first_local.strftime("%d/%m/%Y")},
        "totals": {
            "tokens": sum_tokens(all_months),
            "assistant_messages": sum(m["messages"] for m in all_months),
            "sessions": sum(m["sessions"] for m in all_months),
            "subagents": sum(m["subagents"] for m in all_months),
            "subagent_share": {
                "messages": round(sum(m["agent_messages"] for m in all_months) / total_msgs, 3),
                "output_tokens": round(sum(m["agent_output"] for m in all_months) / total_out, 3),
            },
        },
        "chart_months": [{
            "month": k, "complete": k < current,
            "tokens": {**months[k]["tokens"], "total": sum(months[k]["tokens"].values())},
            "assistant_messages": months[k]["messages"],
            "sessions": months[k]["sessions"], "subagents": months[k]["subagents"],
            "subagent_messages": months[k]["agent_messages"],
        } for k in keys],
        "heatmap": {"weekdays": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
                    "hours": list(range(24)), "counts": heat,
                    "busiest_hours": busiest,
                    "busiest_weekday": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][
                        max(range(7), key=lambda d: by_day[d])]},
        "models": sorted(({"model": n, "assistant_messages": v["messages"],
                           "output_tokens": v["output"],
                           "share_of_messages": round(v["messages"] / msg_total, 3)}
                          for n, v in models.items()),
                         key=lambda x: -x["assistant_messages"]),
    }


def public_json(s):
    out = {k: v for k, v in s.items() if k not in ("chart_months",)}
    out["period"] = {k: v for k, v in s["period"].items() if k != "from_display"}
    out["months"] = s["chart_months"]
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--projects", default=os.path.expanduser("~/.claude/projects"))
    ap.add_argument("--state", default=None)
    ap.add_argument("--render-only", action="store_true",
                    help="ne relit pas les transcripts, redessine depuis --state")
    args = ap.parse_args()

    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    if args.render_only:
        if not args.state:
            sys.exit("--render-only demande --state")
        months, first_ts = {}, None
    else:
        records, file_first = scan(args.projects)
        months, first_ts = aggregate(records, file_first)
        del records
    months, first_ts = merge_state(months, first_ts, args.state)
    if not months:
        sys.exit("aucun transcript lu")
    s = build(months, first_ts, now)

    changed = []
    for mode, t in THEMES.items():
        for name, content in ((f"claude-key-{mode}.svg", chart_key(s, t)),
                              (f"claude-tokens-{mode}.svg", chart_tokens(s, t)),
                              (f"claude-heatmap-{mode}.svg", chart_heatmap(s, t, mode))):
            if write(ASSETS / name, content):
                changed.append(name)
    payload = json.dumps(public_json(s), ensure_ascii=False, indent=2) + "\n"
    if write(ROOT / "claude-stats.json", payload):
        changed.append("claude-stats.json")
    print("changed:", ", ".join(changed) or "none")


if __name__ == "__main__":
    main()
