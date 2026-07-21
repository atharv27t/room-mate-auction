import streamlit as st
import pandas as pd
import numpy as np
from collections import defaultdict
import json
import plotly.express as px
import plotly.graph_objects as go
import random
import time
from datetime import datetime
from pathlib import Path
import string

# ─── Config ─────────────────────────────────────────────────────────
DATA_DIR = Path(__file__).parent / "auctions"
DATA_DIR.mkdir(exist_ok=True)
ADMIN_PIN = "aravali2024"

def generate_id(length=6):
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=length))

def auction_dir(auction_id):
    d = DATA_DIR / auction_id
    d.mkdir(exist_ok=True)
    return d

def load_auction(auction_id):
    f = auction_dir(auction_id) / "config.json"
    if f.exists():
        return json.loads(f.read_text(encoding="utf-8"))
    return None

def save_auction(auction_id, data):
    f = auction_dir(auction_id) / "config.json"
    f.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")

def load_prefs(auction_id):
    f = auction_dir(auction_id) / "preferences.json"
    if f.exists():
        return json.loads(f.read_text(encoding="utf-8"))
    return {}

def save_prefs(auction_id, data):
    f = auction_dir(auction_id) / "preferences.json"
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(f)

def load_results(auction_id):
    f = auction_dir(auction_id) / "results.json"
    if f.exists():
        return json.loads(f.read_text(encoding="utf-8"))
    return None

def save_results(auction_id, data):
    f = auction_dir(auction_id) / "results.json"
    f.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")

def is_locked(auction_id):
    return (auction_dir(auction_id) / "locked.txt").exists()

def lock_auction(auction_id):
    config = load_auction(auction_id)
    prefs = load_prefs(auction_id)
    participants = config["participants"]
    if len(prefs) < len(participants):
        return False, [p for p in participants if p not in prefs]
    rooms = consensus_match(participants, prefs)
    save_results(auction_id, {"rooms": rooms, "timestamp": datetime.now().isoformat(), "algo": "Consensus"})
    (auction_dir(auction_id) / "locked.txt").write_text("locked", encoding="utf-8")
    return True, []

def list_auctions():
    auctions = []
    if DATA_DIR.exists():
        for d in sorted(DATA_DIR.iterdir(), reverse=True):
            config = load_auction(d.name)
            if config:
                auctions.append({"id": d.name, **config})
    return auctions

# ─── Algorithms ─────────────────────────────────────────────────────

def mutual_matcher(participants, preferences):
    pts = list(participants)
    scores = {}
    for i, p1 in enumerate(pts):
        for p2 in pts[i + 1:]:
            scores[(p1, p2)] = (preferences.get(p1, {}).get(p2, 0) + preferences.get(p2, {}).get(p1, 0)) / 2
    sorted_pairs = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    matched, rooms = set(), []
    for (a, b), sc in sorted_pairs:
        if a not in matched and b not in matched:
            rooms.append({"members": [a, b], "score": round(sc, 2),
                          "individual_scores": {a: preferences.get(a, {}).get(b, 0), b: preferences.get(b, {}).get(a, 0)}})
            matched.add(a); matched.add(b)
    return rooms

def irving_algo(participants, preferences):
    pts = list(participants)
    rankings = {p: sorted([x for x in pts if x != p], key=lambda x: preferences.get(p, {}).get(x, 0), reverse=True) for p in pts}
    partner = {p: None for p in pts}
    free, prop_idx = set(pts), {p: 0 for p in pts}
    while free:
        p = min(free, key=lambda x: prop_idx.get(x, 0))
        if prop_idx[p] >= len(rankings[p]): free.discard(p); continue
        target = rankings[p][prop_idx[p]]; prop_idx[p] += 1
        if partner[target] is None:
            partner[target] = p; partner[p] = target; free.discard(p); free.discard(target)
        else:
            cur = partner[target]
            if preferences.get(target, {}).get(p, 0) > preferences.get(target, {}).get(cur, 0):
                partner[target] = p; partner[p] = target; free.discard(p); free.add(cur); partner[cur] = None
    rooms, used = [], set()
    for p in pts:
        if p in used: continue
        q = partner.get(p)
        if q:
            s = (preferences.get(p, {}).get(q, 0) + preferences.get(q, {}).get(p, 0)) / 2
            rooms.append({"members": [p, q], "score": round(s, 2),
                          "individual_scores": {p: preferences.get(p, {}).get(q, 0), q: preferences.get(q, {}).get(p, 0)}})
            used.add(p); used.add(q)
    return rooms

def auction_matcher(participants, preferences):
    pts = list(participants)
    bids = defaultdict(list)
    for p in pts:
        ranked = sorted([x for x in pts if x != p], key=lambda x: preferences.get(p, {}).get(x, 0), reverse=True)
        for rank, choice in enumerate(ranked):
            bids[choice].append((p, preferences.get(p, {}).get(choice, 0), rank))
    assignments, used = {}, set()
    for target, bidders in bids.items():
        if target in used or not bidders: continue
        bidders.sort(key=lambda x: (-x[1], x[2]))
        for bidder, sc, _ in bidders:
            if bidder not in used:
                assignments[target] = bidder; assignments[bidder] = target
                used.add(target); used.add(bidder); break
    rooms, seen = [], set()
    for p in pts:
        if p in seen or p not in assignments: continue
        q = assignments[p]
        sc = (preferences.get(p, {}).get(q, 0) + preferences.get(q, {}).get(p, 0)) / 2
        rooms.append({"members": [p, q], "score": round(sc, 2),
                      "individual_scores": {p: preferences.get(p, {}).get(q, 0), q: preferences.get(q, {}).get(p, 0)}})
        seen.add(p); seen.add(q)
    return rooms

def consensus_match(participants, preferences):
    m = mutual_matcher(participants, preferences)
    i = irving_algo(participants, preferences)
    a = auction_matcher(participants, preferences)
    return max([m, i, a], key=lambda r: sum(x["score"] for x in r))

def detect_cycles(participants, preferences):
    cycles = []
    def dfs(path, current, start):
        if len(path) > 2 and current == start: cycles.append(path[:]); return
        if current in path: return
        ranked = sorted([x for x in participants if x != current], key=lambda x: preferences.get(current, {}).get(x, 0), reverse=True)
        if ranked: path.append(current); dfs(path, ranked[0], start); path.pop()
    for p in participants: dfs([], p, p)
    seen, unique = set(), []
    for c in cycles:
        k = tuple(sorted(c))
        if k not in seen: seen.add(k); unique.append(c)
    return unique

# ─── Styling ────────────────────────────────────────────────────────

CUSTOM_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=Space+Grotesk:wght@500;700&display=swap');

/* Global */
.stApp { font-family: 'Inter', sans-serif !important; }

/* Hero match card */
.hero-match {
    position: relative;
    background: linear-gradient(145deg, #0c1929 0%, #0f2027 50%, #112a34 100%);
    border: 1px solid rgba(6,182,212,0.3);
    border-radius: 20px;
    padding: 3rem 2rem;
    text-align: center;
    margin: 1.5rem 0;
    overflow: hidden;
}
.hero-match::before {
    content: '';
    position: absolute;
    top: -50%; left: -50%;
    width: 200%; height: 200%;
    background: radial-gradient(circle at 30% 40%, rgba(6,182,212,0.08) 0%, transparent 50%),
                radial-gradient(circle at 70% 60%, rgba(14,116,144,0.06) 0%, transparent 50%);
    pointer-events: none;
}
.hero-match .hero-label {
    font-size: 0.7rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.2em;
    color: #06b6d4;
    position: relative;
}
.hero-match .hero-names {
    font-family: 'Space Grotesk', sans-serif;
    font-size: 2.2rem;
    font-weight: 700;
    color: #f1f5f9;
    margin: 16px 0;
    position: relative;
}
.hero-match .hero-sub {
    font-size: 0.9rem;
    color: #94a3b8;
    margin-top: 10px;
    position: relative;
}

/* Score badges */
.badge {
    display: inline-block;
    padding: 5px 16px;
    border-radius: 20px;
    font-weight: 700;
    font-size: 0.75rem;
    letter-spacing: 0.03em;
    color: white;
}
.badge-fire { background: linear-gradient(135deg, #06b6d4, #0891b2); }
.badge-great { background: linear-gradient(135deg, #0891b2, #0e7490); }
.badge-ok { background: linear-gradient(135deg, #0e7490, #155e75); }

/* Name status pills */
.pill {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 6px 14px;
    border-radius: 8px;
    font-weight: 500;
    font-size: 0.82rem;
    margin: 3px;
    border: 1px solid rgba(255,255,255,0.06);
    transition: all 0.2s;
}
.pill-done {
    background: rgba(16,185,129,0.12);
    color: #34d399;
    border-color: rgba(16,185,129,0.25);
}
.pill-wait {
    background: rgba(255,255,255,0.03);
    color: #475569;
    border-color: rgba(255,255,255,0.06);
}

/* Room cards */
.room-card {
    background: linear-gradient(135deg, rgba(17,24,39,0.9), rgba(15,23,42,0.9));
    border: 1px solid rgba(255,255,255,0.07);
    border-radius: 14px;
    padding: 1.2rem 1.5rem;
    margin: 0.6rem 0;
    transition: all 0.2s ease;
    backdrop-filter: blur(10px);
}
.room-card:hover { border-color: rgba(6,182,212,0.25); transform: translateY(-1px); }
.room-card.room-me {
    border-color: rgba(6,182,212,0.4);
    background: linear-gradient(135deg, rgba(6,182,212,0.08), rgba(8,145,178,0.05));
    box-shadow: 0 0 30px rgba(6,182,212,0.06);
}
.room-card h4 {
    margin: 0 0 6px 0;
    color: #64748b;
    font-size: 0.7rem;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    font-weight: 700;
}
.room-card .room-names {
    font-family: 'Space Grotesk', sans-serif;
    font-size: 1.15rem;
    font-weight: 700;
    color: #f1f5f9;
}
.room-card .room-meta { color: #475569; font-size: 0.8rem; margin-top: 8px; }

/* Process steps in sidebar */
.step-box {
    display: flex;
    gap: 10px;
    align-items: flex-start;
    margin: 8px 0;
    padding: 8px 10px;
    border-radius: 10px;
    background: rgba(6,182,212,0.06);
    border: 1px solid rgba(6,182,212,0.12);
}
.step-num {
    flex-shrink: 0;
    width: 24px;
    height: 24px;
    border-radius: 50%;
    background: rgba(6,182,212,0.2);
    color: #06b6d4;
    font-size: 0.7rem;
    font-weight: 800;
    display: flex;
    align-items: center;
    justify-content: center;
}
.step-text {
    font-size: 0.78rem;
    color: #94a3b8;
    line-height: 1.4;
}
.step-text b { color: #e2e8f0; }

/* Sidebar branding */
.sidebar-brand {
    font-family: 'Space Grotesk', sans-serif;
    font-weight: 700;
    font-size: 1.1rem;
    color: #f1f5f9;
    padding: 0.3rem 0;
}

/* Preference slider row */
.pref-row {
    padding: 1rem 1.2rem;
    border-radius: 12px;
    background: rgba(255,255,255,0.02);
    border: 1px solid rgba(255,255,255,0.05);
    margin: 0.4rem 0;
    transition: border-color 0.2s;
}
.pref-row:hover { border-color: rgba(6,182,212,0.2); }

/* Stat cards for admin */
.stat-card {
    background: rgba(17,24,39,0.8);
    border: 1px solid rgba(255,255,255,0.07);
    border-radius: 12px;
    padding: 1rem 1.2rem;
    text-align: center;
}
.stat-card .stat-value {
    font-family: 'Space Grotesk', sans-serif;
    font-size: 1.6rem;
    font-weight: 700;
    color: #06b6d4;
}
.stat-card .stat-label {
    font-size: 0.7rem;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: #64748b;
    margin-top: 4px;
}
</style>
"""

# ─── Streamlit Setup ────────────────────────────────────────────────
st.set_page_config(page_title="Roommate Auction", page_icon=":material/favorite:", layout="wide")
st.markdown(CUSTOM_CSS, unsafe_allow_html=True)

if "user" not in st.session_state:
    st.session_state.user = None
if "auction_id" not in st.session_state:
    st.session_state.auction_id = None
if "page" not in st.session_state:
    st.session_state.page = "home"

# ─── Sidebar ────────────────────────────────────────────────────────

def render_sidebar():
    with st.sidebar:
        st.markdown('<div class="sidebar-brand">:material/heart_check: Roommate Auction</div>', unsafe_allow_html=True)
        st.divider()

        # Process explanation
        st.markdown("**How it works**")
        st.markdown("""
        <div class="step-box"><div class="step-num">1</div><div class="step-text"><b>Create</b> an auction for your group and share the code.</div></div>
        <div class="step-box"><div class="step-num">2</div><div class="step-text"><b>Rate</b> every other person 1-5 (completely private).</div></div>
        <div class="step-box"><div class="step-num">3</div><div class="step-text"><b>Lock</b> the auction once everyone submits.</div></div>
        <div class="step-box"><div class="step-num">4</div><div class="step-text"><b>See</b> your best-match roommate instantly.</div></div>
        """, unsafe_allow_html=True)

        if st.session_state.auction_id:
            st.divider()
            config = load_auction(st.session_state.auction_id)
            if config:
                st.markdown(f"**:material/home: {config['group_name']}**")
                st.caption(f"Code: `{st.session_state.auction_id}`")
                st.caption(f"{len(config['participants'])} participants")

                if st.session_state.user:
                    st.divider()
                    st.markdown(f"Signed in as **{st.session_state.user}**")
                    prefs = load_prefs(st.session_state.auction_id)
                    locked = is_locked(st.session_state.auction_id)
                    if locked:
                        st.success("Auction complete", icon=":material/check_circle:")
                    else:
                        submitted = len(prefs)
                        total = len(config["participants"])
                        st.progress(submitted / total if total > 0 else 0, text=f"{submitted}/{total} submitted")

        st.divider()

        if st.session_state.user or st.session_state.auction_id:
            if st.button(":material/logout: Sign out", use_container_width=True):
                st.session_state.user = None
                st.session_state.auction_id = None
                st.session_state.page = "home"
                st.rerun()

        st.divider()
        st.caption("Made by **Atharv**")


# ─── PAGES ──────────────────────────────────────────────────────────

def show_home():
    render_sidebar()

    st.markdown(
        '<div style="text-align:center;padding:1rem 0 0.5rem 0">'
        '<div style="font-size:0.7rem;font-weight:700;text-transform:uppercase;letter-spacing:0.2em;color:#06b6d4;margin-bottom:0.3rem">Roommate Matching</div>'
        '<div style="font-family:\'Space Grotesk\',sans-serif;font-size:2.2rem;font-weight:800;color:#f1f5f9">Find Your Perfect Roommate</div>'
        '<div style="font-size:0.9rem;color:#64748b;margin-top:0.4rem">Create an auction or join one to get matched algorithmically</div>'
        '</div>', unsafe_allow_html=True)
    st.divider()

    col1, col2 = st.columns(2)

    with col1:
        with st.container(border=True):
            st.markdown("**:material/add_circle: Create New Auction**")
            st.caption("Set up a roommate auction for your group")
            group_name = st.text_input("Group / Hostel Name", key="create_group", placeholder="e.g. Aravali Block A")
            num = st.number_input("How many people?", min_value=2, max_value=50, value=4, key="create_num")
            names = []
            name_cols = st.columns(2)
            for i in range(num):
                with name_cols[i % 2]:
                    name = st.text_input(f"Person {i+1}", key=f"create_name_{i}", placeholder=f"Name {i+1}")
                    if name.strip():
                        names.append(name.strip())

            if st.button(":material/hammer: Create Auction", type="primary", use_container_width=True):
                if not group_name.strip():
                    st.error("Enter a group name")
                elif len(names) < 2:
                    st.error("Add at least 2 people")
                elif len(names) != len(set(names)):
                    st.error("Duplicate names found")
                else:
                    aid = generate_id()
                    save_auction(aid, {
                        "group_name": group_name.strip(),
                        "participants": names,
                        "room_size": 2,
                        "created": datetime.now().isoformat(),
                    })
                    st.session_state.auction_id = aid
                    st.session_state.page = "admin"
                    st.rerun()

    with col2:
        with st.container(border=True):
            st.markdown("**:material/link: Join Existing Auction**")
            st.caption("Enter the auction code shared by your group")
            join_code = st.text_input("Auction Code", key="join_code", placeholder="e.g. ABC123").strip().upper()

            if st.button(":material/rocket_launch: Join Auction", type="primary", use_container_width=True):
                if join_code:
                    config = load_auction(join_code)
                    if config:
                        st.session_state.auction_id = join_code
                        st.session_state.page = "login"
                        st.rerun()
                    else:
                        st.error("Auction not found. Check the code.")

            auctions = list_auctions()
            if auctions:
                st.divider()
                st.markdown("**:material/list: Active Auctions**")
                for a in auctions[:5]:
                    locked = is_locked(a["id"])
                    status = "Locked" if locked else f"{len(load_prefs(a['id']))}/{len(a['participants'])} submitted"
                    if st.button(f"**{a['group_name']}** ({a['id']})  \u2014  {status}", key=f"join_{a['id']}", use_container_width=True):
                        st.session_state.auction_id = a["id"]
                        st.session_state.page = "login"
                        st.rerun()

    st.divider()
    st.caption("Made with :material/favorite: by **Atharv**  |  Instagram: [@atharvv_t](https://instagram.com/atharvv_t)")


def show_login():
    render_sidebar()
    aid = st.session_state.auction_id
    config = load_auction(aid)
    if not config:
        st.error("Auction not found.")
        if st.button(":material/arrow_back: Back"):
            st.session_state.page = "home"; st.session_state.auction_id = None; st.rerun()
        return

    st.markdown(f"## {config['group_name']}")
    st.caption(f"Auction Code: `{aid}`  |  {len(config['participants'])} participants")
    st.divider()

    locked = is_locked(aid)
    prefs = load_prefs(aid)
    all_submitted = all(p in prefs for p in config["participants"])

    if locked:
        with st.container(border=True):
            st.success("Auction complete. Select your name below to see your match.", icon=":material/check_circle:")
    elif all_submitted:
        with st.container(border=True):
            st.success("All profiles submitted!", icon=":material/celebration:")
            if st.button(":material/lock: Lock & Run Match", type="primary", use_container_width=True):
                with st.spinner("Running 3 algorithms..."):
                    ok, missing = lock_auction(aid)
                if ok:
                    st.toast("Match complete!", icon=":material/check_circle:"); time.sleep(0.5); st.rerun()
                else:
                    st.error(f"Missing: {', '.join(missing)}")
    else:
        submitted_names = list(prefs.keys())
        waiting = [p for p in config["participants"] if p not in submitted_names]
        pct = len(submitted_names) / len(config["participants"])
        st.progress(pct, text=f"{len(submitted_names)}/{len(config['participants'])} have submitted")

        with st.container(border=True):
            st.markdown(f"**{len(waiting)}** {'person' if len(waiting) == 1 else 'people'} still need to submit")
            pill_html = ""
            for p in config["participants"]:
                if p in submitted_names:
                    pill_html += f'<span class="pill pill-done"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#34d399" stroke-width="2.5"><path d="M20 6L9 17l-5-5"/></svg> {p}</span>'
                else:
                    pill_html += f'<span class="pill pill-wait"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#475569" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/></svg> {p}</span>'
            st.markdown(pill_html, unsafe_allow_html=True)

    st.divider()
    st.markdown("**:material/person: Who are you?**")
    col1, col2 = st.columns([3, 1])
    with col1:
        selected = st.selectbox("Select your name", [""] + config["participants"], label_visibility="collapsed")
    with col2:
        if st.button("Enter", use_container_width=True, type="primary"):
            if selected:
                st.session_state.user = selected
                if is_locked(aid):
                    st.session_state.page = "results"
                elif selected in prefs:
                    st.session_state.page = "submitted"
                else:
                    st.session_state.page = "preferences"
                st.rerun()

    if st.button(":material/arrow_back: Back to Home"):
        st.session_state.user = None; st.session_state.auction_id = None; st.session_state.page = "home"; st.rerun()


def show_preferences():
    render_sidebar()
    user = st.session_state.user
    aid = st.session_state.auction_id
    config = load_auction(aid)
    others = [p for p in config["participants"] if p != user]
    saved = load_prefs(aid).get(user, {})

    st.markdown(f"## {user}'s Preferences")
    st.caption("Rate every other person \u2014 completely private")
    st.divider()

    prefs = {}
    for other in others:
        with st.container(border=True):
            c1, c2 = st.columns([1, 3])
            with c1:
                st.markdown(f"**{other}**")
            with c2:
                default = saved.get(other, 3)
                val = st.slider("", min_value=1, max_value=5, value=default, key=f"pref_{user}_{other}", label_visibility="collapsed")
                labels = {1: "Nope", 2: "Meh", 3: "Fine", 4: "Want", 5: "Dream"}
                st.caption(f"{labels[val]}")
                prefs[other] = val

    st.divider()
    if saved:
        st.info("Already submitted. Submitting again overwrites your previous preferences.")
    btn_label = ":material/update: Update My Preferences" if saved else ":material/save: Submit My Preferences"
    if st.button(btn_label, type="primary", use_container_width=True):
        all_prefs = load_prefs(aid)
        all_prefs[user] = prefs
        save_prefs(aid, all_prefs)
        st.toast("Preferences saved!", icon=":material/check_circle:")
        st.session_state.page = "submitted"; st.rerun()
    if st.button(":material/arrow_back: Back"):
        st.session_state.user = None; st.session_state.page = "login"; st.rerun()


def show_submitted():
    render_sidebar()
    user = st.session_state.user
    aid = st.session_state.auction_id
    config = load_auction(aid)
    prefs = load_prefs(aid)
    submitted = list(prefs.keys())
    waiting = [p for p in config["participants"] if p not in submitted]
    done = len(submitted)

    st.markdown(f"## Thanks, {user}")
    st.divider()

    with st.container(border=True):
        st.success("Your preferences are locked in. No one can see your ratings.", icon=":material/lock:")
        pct = done / len(config["participants"])
        st.progress(pct, text=f"{done}/{len(config['participants'])} have submitted")

        pill_html = ""
        for p in config["participants"]:
            if p in submitted:
                pill_html += f'<span class="pill pill-done"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#34d399" stroke-width="2.5"><path d="M20 6L9 17l-5-5"/></svg> {p}</span>'
            else:
                pill_html += f'<span class="pill pill-wait"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#475569" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/></svg> {p}</span>'
        st.markdown(pill_html, unsafe_allow_html=True)

        if waiting:
            st.caption(f"Waiting for: {', '.join(waiting)}")

    st.divider()
    c1, c2 = st.columns(2)
    with c1:
        if st.button(":material/refresh: Refresh", use_container_width=True): st.rerun()
    with c2:
        if st.button(":material/edit: Change my prefs", use_container_width=True): st.session_state.page = "preferences"; st.rerun()
    if st.button(":material/logout: Sign out"):
        st.session_state.user = None; st.session_state.page = "login"; st.rerun()


def show_results():
    render_sidebar()
    user = st.session_state.user
    aid = st.session_state.auction_id
    results = load_results(aid)
    config = load_auction(aid)
    if not results:
        st.warning("Results not ready yet.")
        c1, c2 = st.columns(2)
        with c1:
            if st.button(":material/refresh: Refresh"): st.rerun()
        with c2:
            if st.button(":material/logout: Sign out"): st.session_state.user = None; st.session_state.page = "login"; st.rerun()
        return

    st.markdown(f"## {user}'s Match")
    st.caption("The algorithm has spoken...")
    st.divider()

    my_room = None; my_roommate = None; room_num = 0
    for i, room in enumerate(results["rooms"]):
        if user in room["members"]:
            my_room = room
            others_in_room = [m for m in room["members"] if m != user]
            my_roommate = others_in_room[0] if others_in_room else None
            room_num = i + 1; break

    if not my_room or not my_roommate:
        st.error("You're unmatched. Contact the admin.")
        st.markdown("##### All Assignments")
        for i, room in enumerate(results["rooms"]):
            names = " & ".join(room["members"])
            st.markdown(f'<div class="room-card"><h4>Room {i+1}</h4><div class="room-names">{names}</div></div>', unsafe_allow_html=True)
        if st.button(":material/logout: Sign out"): st.session_state.user = None; st.session_state.page = "login"; st.rerun()
        return

    sc = my_room["score"]
    sa = int(my_room["individual_scores"].get(user, 0))
    sb = int(my_room["individual_scores"].get(my_roommate, 0))

    if sc >= 4: badge, badge_cls, label = "IT'S A MATCH", "badge-fire", "Perfect match"
    elif sc >= 3.5: badge, badge_cls, label = "GREAT MATCH", "badge-great", "Strong compatibility"
    else: badge, badge_cls, label = "SOLID MATCH", "badge-ok", "Good fit"

    hearts_sa = '<svg width="16" height="16" viewBox="0 0 24 24" fill="#06b6d4" style="vertical-align:middle"><path d="M12 21.35l-1.45-1.32C5.4 15.36 2 12.28 2 8.5 2 5.42 4.42 3 7.5 3c1.74 0 3.41.81 4.5 2.09C13.09 3.81 14.76 3 16.5 3 19.58 3 22 5.42 22 8.5c0 3.78-3.4 6.86-8.55 11.54L12 21.35z"/></svg>'
    hearts_sb = '<svg width="16" height="16" viewBox="0 0 24 24" fill="#06b6d4" style="vertical-align:middle"><path d="M12 21.35l-1.45-1.32C5.4 15.36 2 12.28 2 8.5 2 5.42 4.42 3 7.5 3c1.74 0 3.41.81 4.5 2.09C13.09 3.81 14.76 3 16.5 3 19.58 3 22 5.42 22 8.5c0 3.78-3.4 6.86-8.55 11.54L12 21.35z"/></svg>'

    st.markdown(f"""
    <div class="hero-match">
        <div class="hero-label">Your Roommate</div>
        <div class="hero-names">{user} &mdash; {my_roommate}</div>
        <div style="margin:16px 0;position:relative"><span class="badge {badge_cls}">{badge} &middot; {sc:.1f}/5.0</span></div>
        <div class="hero-sub">
            You rated {my_roommate}: <b>{hearts_sa * sa} ({sa}/5)</b> &nbsp;&nbsp;
            {my_roommate} rated you: <b>{hearts_sb * sb} ({sb}/5)</b>
        </div>
        <div class="hero-sub" style="margin-top:12px;font-size:0.75rem;color:#475569">Room #{room_num}</div>
    </div>
    """, unsafe_allow_html=True)

    st.markdown("##### All Assignments")
    for i, room in enumerate(results["rooms"]):
        names = " & ".join(room["members"])
        is_me = user in room["members"]
        card_cls = "room-card room-me" if is_me else "room-card"
        label = "Room {}{}".format(i+1, " (You)" if is_me else "")
        st.markdown(f'<div class="{card_cls}"><h4>{label}</h4><div class="room-names">{names}</div></div>', unsafe_allow_html=True)

    st.divider()
    st.caption("Your preferences and scores are private. Only you can see your individual ratings.")
    if st.button(":material/logout: Sign out"):
        st.session_state.user = None; st.session_state.page = "login"; st.rerun()


def show_admin():
    render_sidebar()
    aid = st.session_state.auction_id
    config = load_auction(aid)
    if not config:
        st.error("Auction not found.")
        if st.button(":material/arrow_back: Back"):
            st.session_state.page = "home"; st.session_state.auction_id = None; st.rerun()
        return

    st.markdown("## Admin Dashboard")
    st.caption(f"**{config['group_name']}** | Code: `{aid}`")
    st.divider()

    pin = st.text_input("Admin PIN", type="password")
    if pin != ADMIN_PIN:
        st.warning("Enter correct PIN to access admin panel.")
        if st.button(":material/arrow_back: Back"):
            st.session_state.page = "home"; st.session_state.auction_id = None; st.session_state.user = None; st.rerun()
        return

    participants = config["participants"]
    prefs = load_prefs(aid)
    results = load_results(aid)

    # Stat cards
    stat_html = f"""
    <div style="display:flex;gap:16px;margin:0.5rem 0 1.5rem 0">
        <div class="stat-card" style="flex:1">
            <div class="stat-value">{len(prefs)}/{len(participants)}</div>
            <div class="stat-label">Submissions</div>
        </div>
        <div class="stat-card" style="flex:1">
            <div class="stat-value">{'Locked' if is_locked(aid) else 'Open'}</div>
            <div class="stat-label">Status</div>
        </div>
        <div class="stat-card" style="flex:1">
            <div class="stat-value">{'Ready' if results else 'Pending'}</div>
            <div class="stat-label">Results</div>
        </div>
    </div>
    """
    st.markdown(stat_html, unsafe_allow_html=True)

    # Live preview
    if len(prefs) >= 2:
        st.divider()
        st.markdown("**:material/visibility: Live Matching Preview**")
        if len(prefs) < len(participants):
            missing = [p for p in participants if p not in prefs]
            st.warning(f"Missing: {', '.join(missing)}")
        preview_rooms = consensus_match(list(prefs.keys()), prefs)
        for i, room in enumerate(preview_rooms):
            members_str = " \u2022 ".join(room["members"])
            sc = room["score"]
            badge_cls = 'badge-fire' if sc >= 4 else 'badge-great' if sc >= 3.5 else 'badge-ok'
            st.markdown(f'<div class="room-card"><h4>Room {i+1} <span style="text-transform:none;letter-spacing:normal;font-weight:400;color:#475569">(preview)</span></h4><div class="room-names">{members_str}</div><span class="badge {badge_cls}" style="margin-top:8px">{sc:.1f}/5.0</span></div>', unsafe_allow_html=True)

    # Full matrix
    st.divider()
    st.markdown("**:material/table_chart: Preference Matrix**")
    if prefs:
        missing = [p for p in participants if p not in prefs]
        if missing:
            st.warning(f"Missing: {', '.join(missing)}")
        df = pd.DataFrame({p: {o: prefs.get(p, {}).get(o, "-") for o in participants} for p in participants if p in prefs})
        if not df.empty:
            try:
                df_num = df.apply(pd.to_numeric, errors="coerce")
                st.dataframe(df_num.style.highlight_max(axis=0, color="#d1fae5").highlight_min(axis=0, color="#fecaca"), use_container_width=True)
            except Exception:
                st.dataframe(df, use_container_width=True)

    # Deep Analytics
    if len(prefs) >= 2:
        st.divider()
        st.markdown("**:material/analytics: Deep Analytics**")

        with st.expander(":material/autorenew: Preference Cycles"):
            cycles = detect_cycles(participants, prefs)
            if cycles:
                for cyc in cycles:
                    arrow = " \u2192 "
                    st.warning(f"Cycle: {arrow.join(cyc)} \u2192 {cyc[0]}")
            else:
                st.success("No cycles detected.")

        with st.expander(":material/radar: Preference Profiles"):
            radar_people = list(prefs.keys())
            cols_r = st.columns(min(3, len(radar_people)))
            for idx, person in enumerate(radar_people):
                others = [o for o in participants if o != person and o in prefs]
                values = [prefs.get(person, {}).get(o, 0) for o in others]
                if values:
                    values.append(values[0])
                    others_closed = others + [others[0]]
                    fig_r = go.Figure(go.Scatterpolar(r=values, theta=others_closed, fill='toself', line=dict(color='#06b6d4'), fillcolor='rgba(6,182,212,0.12)'))
                    fig_r.update_layout(polar=dict(radialaxis=dict(visible=True, range=[0, 5], color='#64748b'), angularaxis=dict(color='#94a3b8'), bgcolor='rgba(0,0,0,0)'), showlegend=False, height=300, margin=dict(t=20, b=20, l=40, r=40), paper_bgcolor='rgba(0,0,0,0)')
                    with cols_r[idx % len(cols_r)]:
                        st.markdown(f"**{person}**")
                        st.plotly_chart(fig_r, use_container_width=True)

        col_a, col_b = st.columns(2)
        with col_a:
            with st.expander(":material/tune: Pickiest People", expanded=True):
                pickiness = {p: round(np.std(list(prefs[p].values())), 2) for p in prefs}
                df_p = pd.DataFrame(sorted(pickiness.items(), key=lambda x: -x[1]), columns=["Person", "Pickiness"])
                fig_p = px.bar(df_p, x="Person", y="Pickiness", color="Pickiness", color_continuous_scale="Teal")
                fig_p.update_layout(paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", font=dict(color="#94a3b8"), coloraxis_colorbar=dict(title=""))
                st.plotly_chart(fig_p, use_container_width=True)
        with col_b:
            with st.expander(":material/emoji_events: Friendliest People", expanded=True):
                friendliness = {p: round(np.mean(list(prefs[p].values())), 2) for p in prefs}
                df_f = pd.DataFrame(sorted(friendliness.items(), key=lambda x: -x[1]), columns=["Person", "Avg Rating"])
                fig_f = px.bar(df_f, x="Person", y="Avg Rating", color="Avg Rating", color_continuous_scale="Emerald")
                fig_f.update_layout(yaxis=dict(range=[0, 5.5]), paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", font=dict(color="#94a3b8"), coloraxis_colorbar=dict(title=""))
                st.plotly_chart(fig_f, use_container_width=True)

        all_pairs = []
        ppl = list(prefs.keys())
        for i, p1 in enumerate(ppl):
            for p2 in ppl[i+1:]:
                s1 = prefs.get(p1, {}).get(p2, 0); s2 = prefs.get(p2, {}).get(p1, 0)
                all_pairs.append({"Pair": f"{p1} \u2194 {p2}", "Score": (s1+s2)/2})
        df_pairs = pd.DataFrame(all_pairs).sort_values("Score", ascending=False)

        with st.expander(":material/favorite: Dream Pairs vs :material/skull: Bottom Pairs"):
            col_d, col_n = st.columns(2)
            with col_d:
                st.markdown("**Top Dream Pairs**")
                for _, row in df_pairs.head(5).iterrows():
                    st.markdown(f'<div style="background:rgba(6,182,212,0.06);border-left:3px solid #06b6d4;padding:0.5rem 0.8rem;border-radius:6px;margin:0.25rem 0;color:#e2e8f0"><b>{row["Pair"]}</b> &mdash; {row["Score"]:.1f}</div>', unsafe_allow_html=True)
            with col_n:
                st.markdown("**Bottom Pairs**")
                for _, row in df_pairs.tail(5).iterrows():
                    st.markdown(f'<div style="background:rgba(239,68,68,0.06);border-left:3px solid #ef4444;padding:0.5rem 0.8rem;border-radius:6px;margin:0.25rem 0;color:#fca5a5"><b>{row["Pair"]}</b> &mdash; {row["Score"]:.1f}</div>', unsafe_allow_html=True)

        with st.expander(":material/bar_chart: Rating Distribution"):
            box_data = [{"Rater": p, "Rating": v} for p in prefs for o, v in prefs[p].items()]
            df_box = pd.DataFrame(box_data)
            fig_box = px.box(df_box, x="Rater", y="Rating", color="Rater", color_discrete_sequence=px.colors.qualitative.Set2)
            fig_box.update_layout(yaxis=dict(range=[0, 5.5]), showlegend=False, paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", font=dict(color="#94a3b8"))
            st.plotly_chart(fig_box, use_container_width=True)

        with st.expander(":material/account_tree: Preference Flow Network"):
            node_pos = {}
            for i, n in enumerate(ppl):
                angle = 2 * np.pi * i / len(ppl)
                node_pos[n] = (0.5 + 0.35 * np.cos(angle), 0.5 + 0.35 * np.sin(angle))
            fig_flow = go.Figure()
            colors_map = {5: '#06b6d4', 4: '#0891b2', 3: '#0e7490'}
            for p in ppl:
                for o in ppl:
                    if p != o:
                        rating = prefs.get(p, {}).get(o, 0)
                        if rating >= 3:
                            x0, y0 = node_pos[p]; x1, y1 = node_pos[o]
                            fig_flow.add_trace(go.Scatter(x=[x0, x1], y=[y0, y1], mode='lines', line=dict(width=rating*0.8, color=colors_map.get(rating, '#64748b'), dash='dot' if rating < 4 else 'solid'), opacity=0.6, hoverinfo='text', text=f"{p} \u2192 {o}: {rating}/5"))
            for n in ppl:
                x, y = node_pos[n]
                fig_flow.add_trace(go.Scatter(x=[x], y=[y], mode='markers+text', marker=dict(size=30, color='#111827', line=dict(color='#06b6d4', width=2)), text=[n], textposition='top center', textfont=dict(color='#f1f5f9', size=11), hoverinfo='text'))
            fig_flow.update_layout(showlegend=False, height=450, xaxis=dict(showgrid=False, zeroline=False, showticklabels=False), yaxis=dict(showgrid=False, zeroline=False, showticklabels=False), paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)')
            st.plotly_chart(fig_flow, use_container_width=True)
            st.caption("Lines = ratings of 3+. Thicker = stronger preference.")

        with st.expander(":material/grid_on: Mutual Compatibility"):
            mutual_data = {}
            for p1 in ppl:
                mutual_data[p1] = {}
                for p2 in ppl:
                    if p1 == p2: mutual_data[p1][p2] = 0
                    else: mutual_data[p1][p2] = (prefs.get(p1, {}).get(p2, 0) + prefs.get(p2, {}).get(p1, 0)) / 2
            df_mut = pd.DataFrame(mutual_data)
            fig_mut = px.imshow(df_mut.values, x=list(df_mut.columns), y=list(df_mut.index), color_continuous_scale="Teal", text_auto=True)
            fig_mut.update_layout(height=max(400, len(ppl) * 50), paper_bgcolor="rgba(0,0,0,0)", font=dict(color="#94a3b8"))
            st.plotly_chart(fig_mut, use_container_width=True)

        with st.expander(":material/leaderboard: Most Wanted"):
            wanted = defaultdict(int)
            for other in participants:
                for p in participants:
                    if other != p and prefs.get(other, {}).get(p, 0) >= 4: wanted[p] += 1
            if wanted:
                df_w = pd.DataFrame(sorted(wanted.items(), key=lambda x: -x[1]), columns=["Person", "Times Rated 4+"])
                fig_w = px.bar(df_w, x="Person", y="Times Rated 4+", color="Times Rated 4+", color_continuous_scale="Teal")
                fig_w.update_layout(paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", font=dict(color="#94a3b8"), coloraxis_colorbar=dict(title=""))
                st.plotly_chart(fig_w, use_container_width=True)

        with st.expander(":material/heatmap: Raw Preference Heatmap"):
            df_h = pd.DataFrame({p: {o: prefs.get(p, {}).get(o, 0) for o in participants} for p in participants if p in prefs})
            if not df_h.empty:
                fig2 = px.imshow(df_h.values, x=list(df_h.columns), y=list(df_h.index), color_continuous_scale="Teal", text_auto=True)
                fig2.update_layout(height=max(400, len(ppl) * 50), paper_bgcolor="rgba(0,0,0,0)", font=dict(color="#94a3b8"))
                st.plotly_chart(fig2, use_container_width=True)

    # Final results
    if results:
        st.divider()
        st.markdown("**:material/home: Final Room Assignments**")
        for i, room in enumerate(results["rooms"]):
            members = room["members"]; sc = room["score"]
            names_html = " &middot; ".join(members)
            indiv = " | ".join([f"{m}\u2192{[x for x in members if x!=m][0]}: {room['individual_scores'].get(m, '?')}" for m in members if len(members) > 1])
            badge_cls = 'badge-fire' if sc >= 4 else 'badge-great' if sc >= 3.5 else 'badge-ok'
            st.markdown(f'<div class="room-card"><h4>Room {i+1}</h4><div class="room-names">{names_html}</div><span class="badge {badge_cls}" style="margin-top:8px">{sc:.1f}/5.0</span><div class="room-meta">{indiv}</div></div>', unsafe_allow_html=True)

        unmatched = [p for p in participants if p not in [m for r in results["rooms"] for m in r["members"]]]
        if unmatched:
            with st.container(border=True):
                st.warning(f"Wildcard (unmatched): {', '.join(unmatched)}")

        avg = np.mean([r["score"] for r in results["rooms"]])
        st.metric("Average Compatibility", f"{avg:.2f}")

        with st.expander(":material/casino: Monte Carlo Simulation (1000 rounds)", expanded=True):
            sim_scores = []
            pts = participants[:]
            for _ in range(1000):
                random.shuffle(pts)
                rs = sum((prefs.get(pts[j], {}).get(pts[j+1], 0) + prefs.get(pts[j+1], {}).get(pts[j], 0)) / 2 for j in range(0, len(pts) - 1, 2))
                sim_scores.append(rs / (len(pts) // 2))
            fig4 = px.histogram(x=sim_scores, nbins=40, color_discrete_sequence=["#0891b2"])
            fig4.add_vline(x=avg, line_dash="dash", line_color="#06b6d4", line_width=2, annotation_text=f"Your match: {avg:.2f}")
            fig4.update_layout(paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", font=dict(color="#94a3b8"), showlegend=False)
            st.plotly_chart(fig4, use_container_width=True)
            pct = (np.array(sim_scores) < avg).mean() * 100
            st.info(f"Your matching beats {pct:.0f}% of random pairings.")

    # Actions
    st.divider()
    st.markdown("**:material/settings: Actions**")
    c1, c2 = st.columns(2)
    with c1:
        if st.button(":material/lock: Force Lock & Match", type="primary", use_container_width=True):
            ok, missing = lock_auction(aid)
            if ok: st.toast("Locked!", icon=":material/check_circle:"); st.rerun()
            else: st.error(f"Missing: {', '.join(missing)}")
    with c2:
        if st.button(":material/delete: Delete Auction", use_container_width=True):
            import shutil
            shutil.rmtree(auction_dir(aid), ignore_errors=True)
            st.toast("Deleted", icon=":material/delete:"); st.session_state.auction_id = None; st.session_state.page = "home"; st.rerun()

    if st.button(":material/logout: Sign out"):
        st.session_state.user = None; st.session_state.page = "login"; st.rerun()

# ─── Router ─────────────────────────────────────────────────────────
query_params = st.query_params
if query_params.get("admin") == "1" and st.session_state.auction_id:
    show_admin()
elif st.session_state.page == "home" or st.session_state.auction_id is None:
    show_home()
elif st.session_state.page == "login":
    show_login()
elif st.session_state.page == "preferences":
    show_preferences()
elif st.session_state.page == "submitted":
    if is_locked(st.session_state.auction_id):
        st.session_state.page = "results"; show_results()
    else: show_submitted()
elif st.session_state.page == "results":
    if is_locked(st.session_state.auction_id): show_results()
    else: st.session_state.page = "submitted"; show_submitted()
elif st.session_state.page == "admin":
    show_admin()
else:
    show_home()
