"""Select verified nodes and prefer repeatedly successful candidates."""
MAX_HISTORY = 64


def history_candidates(history):
    return sorted(history, key=lambda ip: (-history[ip]["passes"], history[ip]["worst"]))[:12]


def update_history(history, rows):
    updated = {**history}
    for ip, *_, worst in rows:
        updated[ip] = {"passes": min(history.get(ip, {}).get("passes", 0) + 1, 255), "worst": worst}
    keep = sorted(updated, key=lambda ip: (-updated[ip]["passes"], updated[ip]["worst"]))
    return {ip: updated[ip] for ip in keep[:MAX_HISTORY]}


def choose(ranked, absolute, previous, slots):
    if len(ranked) >= 4:
        return ranked[:min(6, slots)]
    reserve = [row for row in absolute if row[0] in previous and row not in ranked]
    selection = [*ranked, *reserve][:min(8, slots)]
    return selection if len(selection) == 8 else []
