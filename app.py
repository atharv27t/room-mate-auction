import streamlit as st
import pandas as pd
import numpy as np
from collections import defaultdict
from itertools import combinations
import json
import plotly.express as px
import plotly.graph_objects as go
import random
import time
import hashlib
from datetime import datetime
from pathlib import Path
import string

# ─── Config ─────────────────────────────────────────────────────────
DATA_DIR = Path(__file__).parent / "auctions"
DATA_DIR.mkdir(exist_ok=True)
ADMIN_PIN = "aravali2024"

# ─── Auction ID generator ───────────────────────────────────────────
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

CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Poppins:wght@400;600;700;800&display=swap');
* { font-family: 'Poppins', sans-serif; }
.stApp { background: linear-gradient(170deg, #0f0c29 0%, #302b63 50%, #24243e 100%); }
.main-title {
    font-size: 2.8rem; font-weight: 800;
    background: linear-gradient(135deg, #f093fb 0%, #f5576c 50%, #fda085 100%);
    -webkit-background-clip: text; -webkit-text-fill-color: transparent;
    text-align: center; padding: 0.8rem 0 0.2rem 0;
}
.sub-title { font-size: 1rem; color: #b8b8d4; text-align: center; margin-bottom: 0.3rem; }
.tagline { font-size: 0.85rem; color: #f093fb; text-align: center; margin-bottom: 1.5rem; font-style: italic; }

.match-card {
    background: linear-gradient(135deg, rgba(240,147,251,0.15) 0%, rgba(245,87,108,0.15) 100%);
    border: 1px solid rgba(240,147,251,0.3); border-radius: 20px;
    padding: 1.5rem 2rem; margin: 0.8rem 0; backdrop-filter: blur(10px);
}
.match-card h3 { margin: 0; color: #f5576c; font-size: 1.1rem; }
.match-card .names { font-size: 1.6rem; font-weight: 700; color: white; margin: 6px 0; }
.match-card .score-badge {
    display: inline-block; padding: 4px 14px; border-radius: 20px;
    font-weight: 600; font-size: 0.85rem; color: white;
}
.fire { background: linear-gradient(135deg, #f5576c, #ff6b6b); }
.great { background: linear-gradient(135deg, #f093fb, #c471ed); }
.ok { background: linear-gradient(135deg, #667eea, #764ba2); }

.wait-card {
    background: linear-gradient(135deg, rgba(102,126,234,0.15) 0%, rgba(118,75,162,0.15) 100%);
    border: 1px solid rgba(102,126,234,0.3); border-radius: 20px;
    padding: 2rem; text-align: center; margin: 1rem 0;
}
.wait-card .big-num { font-size: 4rem; font-weight: 800; color: #f093fb; }

.done-card {
    background: linear-gradient(135deg, rgba(40,167,69,0.15) 0%, rgba(32,201,151,0.15) 100%);
    border: 1px solid rgba(40,167,69,0.3); border-radius: 20px;
    padding: 2rem; text-align: center; margin: 1rem 0;
}

.auction-card {
    background: linear-gradient(135deg, rgba(240,147,251,0.1) 0%, rgba(245,87,108,0.1) 100%);
    border: 1px solid rgba(240,147,251,0.3); border-radius: 16px;
    padding: 1.5rem; margin: 0.5rem 0; cursor: pointer; transition: all 0.3s;
}
.auction-card:hover { border-color: #f093fb; transform: translateY(-2px); }

.name-pill {
    display: inline-block; padding: 10px 24px; border-radius: 30px;
    background: linear-gradient(135deg, #667eea, #764ba2);
    color: white; font-weight: 600; font-size: 1.1rem; margin: 4px;
    border: 2px solid transparent; transition: all 0.3s;
}
.name-pill.submitted { background: linear-gradient(135deg, #28a745, #20c997); border-color: #28a745; }
.name-pill.waiting { background: rgba(255,255,255,0.08); border-color: rgba(255,255,255,0.2); color: #888; }

.stTabs [data-baseweb="tab"] { color: #b8b8d4 !important; font-weight: 600; }
.stTabs [aria-selected="true"] { color: #f093fb !important; border-bottom-color: #f093fb !important; }
div[data-testid="stMetric"] {
    background: rgba(255,255,255,0.05); border-radius: 12px;
    padding: 12px 16px; border: 1px solid rgba(255,255,255,0.08);
}
div[data-testid="stMetricValue"] { color: #f093fb !important; }
div[data-testid="stMetricLabel"] { color: #b8b8d4 !important; }
.stProgress > div > div { background: linear-gradient(90deg, #f093fb, #f5576c); }
.streamlit-expanderHeader { color: #b8b8d4 !important; font-weight: 600; }

.footer {
    text-align: center; padding: 2rem 0 1rem 0; margin-top: 2rem;
    border-top: 1px solid rgba(240,147,251,0.15);
}
.footer .made-by { font-size: 0.95rem; color: #b8b8d4; font-weight: 600; }
.footer .made-by .name {
    background: linear-gradient(135deg, #f093fb, #f5576c);
    -webkit-background-clip: text; -webkit-text-fill-color: transparent; font-weight: 800;
}
.footer .insta { font-size: 0.85rem; color: #888; margin-top: 6px; }
.footer .insta a { color: #f093fb; text-decoration: none; font-weight: 600; }
.footer .insta a:hover { text-decoration: underline; }
.footer .cutu { font-size: 1.4rem; margin-top: 6px; }
</style>
"""

# ─── Streamlit Setup ────────────────────────────────────────────────
st.set_page_config(page_title="Roommate Auction", page_icon="💘", layout="wide")
st.markdown(CSS, unsafe_allow_html=True)

if "user" not in st.session_state:
    st.session_state.user = None
if "auction_id" not in st.session_state:
    st.session_state.auction_id = None
if "page" not in st.session_state:
    st.session_state.page = "home"

# ─── PAGES ──────────────────────────────────────────────────────────

def show_home():
    st.markdown('<div class="main-title">💘 Roommate Auction</div>', unsafe_allow_html=True)
    st.markdown('<div class="sub-title">Create your own auction or join an existing one</div>', unsafe_allow_html=True)
    st.markdown("---")

    col1, col2 = st.columns(2)

    with col1:
        st.markdown("### 🆕 Create New Auction")
        st.caption("Set up a roommate auction for your group")
        group_name = st.text_input("Group / Hostel Name", key="create_group", placeholder="e.g. Aravali Block A")
        num = st.number_input("How many people?", min_value=2, max_value=50, value=4, key="create_num")
        names = []
        for i in range(num):
            name = st.text_input(f"Person {i+1}", key=f"create_name_{i}", placeholder=f"Name {i+1}")
            if name.strip():
                names.append(name.strip())

        if st.button("🔨 Create Auction", type="primary", use_container_width=True):
            if not group_name.strip():
                st.error("Enter a group name!")
            elif len(names) < 2:
                st.error("Add at least 2 people!")
            elif len(names) != len(set(names)):
                st.error("Duplicate names found!")
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
        st.markdown("### 🔗 Join Existing Auction")
        st.caption("Enter the auction code shared by your group admin")
        join_code = st.text_input("Auction Code", key="join_code", placeholder="e.g. ABC123").strip().upper()

        if st.button("🚀 Join Auction", type="primary", use_container_width=True):
            if join_code:
                config = load_auction(join_code)
                if config:
                    st.session_state.auction_id = join_code
                    st.session_state.page = "login"
                    st.rerun()
                else:
                    st.error("Auction not found! Check the code.")

        # Show existing auctions
        auctions = list_auctions()
        if auctions:
            st.markdown("---")
            st.markdown("### 📋 Active Auctions")
            for a in auctions[:5]:
                locked = is_locked(a["id"])
                status = "🔒 Locked" if locked else f"⏳ {len(load_prefs(a['id']))}/{len(a['participants'])} submitted"
                if st.button(f"**{a['group_name']}** ({a['id']}) — {status}", key=f"join_{a['id']}", use_container_width=True):
                    st.session_state.auction_id = a["id"]
                    if locked:
                        st.session_state.page = "login"
                    else:
                        st.session_state.page = "login"
                    st.rerun()

    st.markdown("""
    <div class="footer">
        <div class="cutu">🥰</div>
        <div class="made-by">Made with 💗 by <span class="name">Atharv</span></div>
        <div class="insta">Follow me on Instagram → <a href="https://instagram.com/atharvv_t" target="_blank">@atharvv_t</a></div>
    </div>
    """, unsafe_allow_html=True)


def show_login():
    aid = st.session_state.auction_id
    config = load_auction(aid)
    if not config:
        st.error("Auction not found.")
        if st.button("← Back"): st.session_state.page = "home"; st.session_state.auction_id = None; st.rerun()
        return

    st.markdown(f'<div class="main-title">💘 {config["group_name"]}</div>', unsafe_allow_html=True)
    st.markdown(f'<div class="sub-title">Auction Code: <b>{aid}</b> • {len(config["participants"])} participants</div>', unsafe_allow_html=True)
    st.markdown("---")

    locked = is_locked(aid)
    prefs = load_prefs(aid)
    all_submitted = all(p in prefs for p in config["participants"])

    if locked:
        st.markdown('<div class="done-card"><h2 style="color:#28a745">✅ Auction Complete!</h2><p style="color:#b8b8d4">Select your name to see your match.</p></div>', unsafe_allow_html=True)
    elif all_submitted:
        st.markdown('<div class="done-card"><h2 style="color:#28a745">🎉 All profiles submitted!</h2></div>', unsafe_allow_html=True)
        if st.button("🔒 LOCK & RUN MATCH", type="primary", use_container_width=True):
            with st.spinner("Running algorithms..."):
                ok, missing = lock_auction(aid)
            if ok:
                st.success("Done!"); time.sleep(0.5); st.rerun()
            else:
                st.error(f"Missing: {', '.join(missing)}")
    else:
        submitted_names = list(prefs.keys())
        waiting = [p for p in config["participants"] if p not in submitted_names]
        pct = len(submitted_names) / len(config["participants"])
        st.progress(pct, text=f"{len(submitted_names)}/{len(config['participants'])} have submitted")

        st.markdown('<div class="wait-card">', unsafe_allow_html=True)
        st.markdown(f'<div class="big-num">{len(waiting)}</div>', unsafe_allow_html=True)
        label = "person still needs to submit" if len(waiting) == 1 else "people still need to submit"
        st.markdown(f'<p style="color:#b8b8d4;font-size:1.1rem">{label}</p>', unsafe_allow_html=True)
        st.markdown("</div>", unsafe_allow_html=True)

        for p in config["participants"]:
            if p in submitted_names:
                st.markdown(f'<span class="name-pill submitted">✅ {p}</span>', unsafe_allow_html=True)
            else:
                st.markdown(f'<span class="name-pill waiting">⏳ {p}</span>', unsafe_allow_html=True)

    st.markdown("---")
    st.markdown("### 🙋 Who are you?")
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

    if st.button("← Back to Home"):
        st.session_state.user = None; st.session_state.auction_id = None; st.session_state.page = "home"; st.rerun()


def show_preferences():
    user = st.session_state.user
    aid = st.session_state.auction_id
    config = load_auction(aid)
    others = [p for p in config["participants"] if p != user]
    saved = load_prefs(aid).get(user, {})

    st.markdown(f'<div class="main-title">💘 {user}&#39;s Preferences</div>', unsafe_allow_html=True)
    st.markdown('<div class="tagline">Rate every other person — completely private</div>', unsafe_allow_html=True)
    st.markdown("---")

    prefs = {}
    for other in others:
        col1, col2 = st.columns([2, 3])
        with col1: st.markdown(f"**{other}**")
        with col2:
            default = saved.get(other, 3)
            val = st.slider("", min_value=1, max_value=5, value=default, key=f"pref_{user}_{other}", label_visibility="collapsed")
            labels = {1: "❌ Nope", 2: "😬 Meh", 3: "🤝 Fine", 4: "😊 Want", 5: "🔥 Dream"}
            st.caption(f"{labels[val]}")
            prefs[other] = val

    st.markdown("---")
    if saved: st.info("📝 Already submitted. Submitting again overwrites your previous preferences.")
    btn_label = "🔄 UPDATE MY PREFERENCES" if saved else "💾 SUBMIT MY PREFERENCES"
    if st.button(btn_label, type="primary", use_container_width=True):
        all_prefs = load_prefs(aid)
        all_prefs[user] = prefs
        save_prefs(aid, all_prefs)
        st.session_state.page = "submitted"; st.rerun()
    if st.button("← Back"):
        st.session_state.user = None; st.session_state.page = "login"; st.rerun()


def show_submitted():
    user = st.session_state.user
    aid = st.session_state.auction_id
    config = load_auction(aid)
    prefs = load_prefs(aid)
    submitted = list(prefs.keys())
    waiting = [p for p in config["participants"] if p not in submitted]
    done = len(submitted)

    st.markdown(f'<div class="main-title">💘 Thanks, {user}!</div>', unsafe_allow_html=True)
    st.markdown("---")
    st.markdown('<div class="done-card"><h2 style="color:#28a745">✅ Your preferences are locked in!</h2><p style="color:#b8b8d4">No one can see your ratings.</p></div>', unsafe_allow_html=True)

    pct = done / len(config["participants"])
    st.progress(pct, text=f"{done}/{len(config['participants'])} have submitted")
    for p in config["participants"]:
        if p in submitted:
            st.markdown(f'<span class="name-pill submitted">✅ {p}</span>', unsafe_allow_html=True)
        else:
            st.markdown(f'<span class="name-pill waiting">⏳ {p}</span>', unsafe_allow_html=True)

    if waiting: st.info(f"Waiting for: {', '.join(waiting)}")
    else: st.success("All done!")

    col1, col2 = st.columns(2)
    with col1:
        if st.button("🔄 Refresh", use_container_width=True): st.rerun()
    with col2:
        if st.button("✏️ Change my prefs", use_container_width=True): st.session_state.page = "preferences"; st.rerun()
    if st.button("🚪 Logout"):
        st.session_state.user = None; st.session_state.page = "login"; st.rerun()


def show_results():
    user = st.session_state.user
    aid = st.session_state.auction_id
    results = load_results(aid)
    config = load_auction(aid)
    if not results:
        st.warning("Results not ready yet.")
        col1, col2 = st.columns(2)
        with col1:
            if st.button("🔄 Refresh"): st.rerun()
        with col2:
            if st.button("🚪 Logout"): st.session_state.user = None; st.session_state.page = "login"; st.rerun()
        return

    st.markdown(f'<div class="main-title">💘 {user}&#39;s Match Result</div>', unsafe_allow_html=True)
    st.markdown('<div class="tagline">The algorithm has spoken...</div>', unsafe_allow_html=True)
    st.markdown("---")

    my_room = None; my_roommate = None; room_num = 0
    for i, room in enumerate(results["rooms"]):
        if user in room["members"]:
            my_room = room
            others_in_room = [m for m in room["members"] if m != user]
            my_roommate = others_in_room[0] if others_in_room else None
            room_num = i + 1; break

    if not my_room or not my_roommate:
        st.error("You're unmatched (wildcard). Ask the admin about joining as a 3rd.")
        st.markdown("#### All Room Assignments")
        for i, room in enumerate(results["rooms"]):
            names = " & ".join(room["members"])
            st.markdown(f'<div class="match-card"><h3>Room {i+1}</h3><div class="names" style="font-size:1.2rem">{names}</div></div>', unsafe_allow_html=True)
        if st.button("🚪 Logout"): st.session_state.user = None; st.session_state.page = "login"; st.rerun()
        return

    sc = my_room["score"]
    sa = int(my_room["individual_scores"].get(user, 0))
    sb = int(my_room["individual_scores"].get(my_roommate, 0))

    if sc >= 4: badge, css, emoji = "IT'S A MATCH!", "fire", "🔥"
    elif sc >= 3.5: badge, css, emoji = "GREAT MATCH", "great", "💜"
    else: badge, css, emoji = "SOLID MATCH", "ok", "🤝"

    st.markdown(f"""
    <div class="match-card" style="text-align:center; padding:3rem">
        <h3>{emoji} Your Roommate {emoji}</h3>
        <div class="names" style="font-size:2.5rem; margin:15px 0">{user} &nbsp;❤️&nbsp; {my_roommate}</div>
        <div style="margin:15px 0"><span class="score-badge {css}" style="font-size:1.1rem; padding:8px 24px">{badge} — {sc:.1f}/5.0</span></div>
        <div style="margin-top:20px; color:#b8b8d4">
            <p>You rated {my_roommate}: <b>{'❤️' * sa} ({sa}/5)</b></p>
            <p>{my_roommate} rated you: <b>{'❤️' * sb} ({sb}/5)</b></p>
        </div>
        <div style="margin-top:15px; color:#888; font-size:0.85rem">Room #{room_num}</div>
    </div>
    """, unsafe_allow_html=True)

    st.markdown("#### All Rooms")
    for i, room in enumerate(results["rooms"]):
        names = " & ".join(room["members"])
        is_me = user in room["members"]
        if is_me:
            st.markdown(f'<div class="match-card" style="border-color:#f093fb"><h3>🔥 Room {i+1} (YOU)</h3><div class="names" style="font-size:1.2rem">{names}</div></div>', unsafe_allow_html=True)
        else:
            st.markdown(f'<div class="match-card" style="opacity:0.6"><h3>🏠 Room {i+1}</h3><div class="names" style="font-size:1.2rem">{names}</div></div>', unsafe_allow_html=True)

    st.markdown("---")
    if st.button("🚪 Logout"): st.session_state.user = None; st.session_state.page = "login"; st.rerun()


def show_admin():
    aid = st.session_state.auction_id
    config = load_auction(aid)
    if not config:
        st.error("Auction not found.")
        if st.button("← Back"): st.session_state.page = "home"; st.session_state.auction_id = None; st.rerun()
        return

    st.markdown(f'<div class="main-title">🔑 Admin — {config["group_name"]}</div>', unsafe_allow_html=True)
    st.markdown(f'<div class="sub-title">Auction Code: <b>{aid}</b> • Share this with participants</div>', unsafe_allow_html=True)
    st.markdown("---")

    pin = st.text_input("Admin PIN", type="password")
    if pin != ADMIN_PIN:
        st.warning("Enter correct PIN.")
        if st.button("← Back"): st.session_state.page = "home"; st.session_state.auction_id = None; st.session_state.user = None; st.rerun()
        return

    participants = config["participants"]
    prefs = load_prefs(aid)
    results = load_results(aid)

    c1, c2, c3 = st.columns(3)
    c1.metric("Submissions", f"{len(prefs)}/{len(participants)}")
    c2.metric("Locked", "Yes" if is_locked(aid) else "No")
    c3.metric("Results", "Ready" if results else "Pending")

    # Live preview
    if len(prefs) >= 2:
        st.subheader("👁️ Live Matching Preview")
        if len(prefs) < len(participants):
            missing = [p for p in participants if p not in prefs]
            st.warning(f"Missing: {', '.join(missing)}")
        preview_rooms = consensus_match(list(prefs.keys()), prefs)
        for i, room in enumerate(preview_rooms):
            members_str = " ❤️ ".join(room["members"])
            sc = room["score"]
            badge_cls = 'fire' if sc >= 4 else 'great' if sc >= 3.5 else 'ok'
            st.markdown(f'<div class="match-card"><h3>Room {i+1} <span style="font-size:0.75rem;color:#888">(preview)</span></h3><div class="names">{members_str}</div><span class="score-badge {badge_cls}">Score: {sc:.1f}/5.0</span></div>', unsafe_allow_html=True)

    # Full matrix
    st.subheader("📋 Full Preference Matrix")
    if prefs:
        missing = [p for p in participants if p not in prefs]
        if missing: st.warning(f"Missing: {', '.join(missing)}")
        df = pd.DataFrame({p: {o: prefs.get(p, {}).get(o, "-") for o in participants} for p in participants if p in prefs})
        if not df.empty:
            try:
                df_num = df.apply(pd.to_numeric, errors="coerce")
                st.dataframe(df_num.style.highlight_max(axis=0, color="#c8e6c9").highlight_min(axis=0, color="#ffcdd2"), use_container_width=True)
            except Exception:
                st.dataframe(df, use_container_width=True)

    if len(prefs) >= 2:
        st.markdown("---")
        st.markdown("## 🧪 Deep Analytics")

        # Cycles
        st.subheader("🔄 Preference Cycles")
        cycles = detect_cycles(participants, prefs)
        if cycles:
            for cyc in cycles: st.warning(f"Cycle: {' → '.join(cyc)} → {cyc[0]}")
        else:
            st.success("No cycles!")

        # Radar
        st.subheader("🕸️ Preference Profiles")
        radar_people = list(prefs.keys())
        cols_r = st.columns(min(3, len(radar_people)))
        for idx, person in enumerate(radar_people):
            others = [o for o in participants if o != person and o in prefs]
            values = [prefs.get(person, {}).get(o, 0) for o in others]
            if values:
                values.append(values[0])
                others_closed = others + [others[0]]
                fig_r = go.Figure(go.Scatterpolar(r=values, theta=others_closed, fill='toself', line=dict(color='#f093fb'), fillcolor='rgba(240,147,251,0.2)'))
                fig_r.update_layout(polar=dict(radialaxis=dict(visible=True, range=[0, 5], color='#b8b8d4'), angularaxis=dict(color='#b8b8d4'), bgcolor='rgba(0,0,0,0)'), showlegend=False, height=320, margin=dict(t=30, b=30, l=40, r=40), paper_bgcolor='rgba(0,0,0,0)')
                with cols_r[idx % len(cols_r)]:
                    st.markdown(f"**{person}**")
                    st.plotly_chart(fig_r, use_container_width=True)

        # Pickiness
        col_a, col_b = st.columns(2)
        with col_a:
            st.subheader("🔴 Pickiest People")
            pickiness = {p: round(np.std(list(prefs[p].values())), 2) for p in prefs}
            df_p = pd.DataFrame(sorted(pickiness.items(), key=lambda x: -x[1]), columns=["Person", "Pickiness"])
            fig_p = px.bar(df_p, x="Person", y="Pickiness", color="Pickiness", color_continuous_scale="Inferno")
            fig_p.update_layout(paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", font=dict(color="#b8b8d4"))
            st.plotly_chart(fig_p, use_container_width=True)
        with col_b:
            st.subheader("🟢 Friendliest People")
            friendliness = {p: round(np.mean(list(prefs[p].values())), 2) for p in prefs}
            df_f = pd.DataFrame(sorted(friendliness.items(), key=lambda x: -x[1]), columns=["Person", "Avg Rating"])
            fig_f = px.bar(df_f, x="Person", y="Avg Rating", color="Avg Rating", color_continuous_scale="Greens")
            fig_f.update_layout(yaxis=dict(range=[0, 5.5]), paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", font=dict(color="#b8b8d4"))
            st.plotly_chart(fig_f, use_container_width=True)

        # Dream & Nightmare pairs
        all_pairs = []
        ppl = list(prefs.keys())
        for i, p1 in enumerate(ppl):
            for p2 in ppl[i+1:]:
                s1 = prefs.get(p1, {}).get(p2, 0); s2 = prefs.get(p2, {}).get(p1, 0)
                all_pairs.append({"Pair": f"{p1} ↔ {p2}", "Score": (s1+s2)/2})
        df_pairs = pd.DataFrame(all_pairs).sort_values("Score", ascending=False)
        col_d, col_n = st.columns(2)
        with col_d:
            st.subheader("💕 Top Dream Pairs")
            for _, row in df_pairs.head(5).iterrows():
                st.markdown(f'<div class="done-card" style="padding:0.5rem 1rem;margin:0.3rem 0"><b>{row["Pair"]}</b> — {row["Score"]:.1f}</div>', unsafe_allow_html=True)
        with col_n:
            st.subheader("💀 Bottom Pairs")
            for _, row in df_pairs.tail(5).iterrows():
                st.markdown(f'<div style="background:rgba(245,87,108,0.1);border-left:3px solid #f5576c;padding:0.5rem 1rem;border-radius:8px;margin:0.3rem 0;color:#f5576c"><b>{row["Pair"]}</b> — {row["Score"]:.1f}</div>', unsafe_allow_html=True)

        # Box plot
        st.subheader("📊 Rating Distribution")
        box_data = [{"Rater": p, "Rating": v} for p in prefs for o, v in prefs[p].items()]
        df_box = pd.DataFrame(box_data)
        fig_box = px.box(df_box, x="Rater", y="Rating", color="Rater", color_discrete_sequence=px.colors.qualitative.Set2)
        fig_box.update_layout(yaxis=dict(range=[0, 5.5]), showlegend=False, paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", font=dict(color="#b8b8d4"))
        st.plotly_chart(fig_box, use_container_width=True)

        # Flow network
        st.subheader("🔀 Preference Flow")
        node_pos = {}
        for i, n in enumerate(ppl):
            angle = 2 * np.pi * i / len(ppl)
            node_pos[n] = (0.5 + 0.35 * np.cos(angle), 0.5 + 0.35 * np.sin(angle))
        fig_flow = go.Figure()
        colors_map = {5: '#f5576c', 4: '#f093fb', 3: '#667eea'}
        for p in ppl:
            for o in ppl:
                if p != o:
                    rating = prefs.get(p, {}).get(o, 0)
                    if rating >= 3:
                        x0, y0 = node_pos[p]; x1, y1 = node_pos[o]
                        fig_flow.add_trace(go.Scatter(x=[x0, x1], y=[y0, y1], mode='lines', line=dict(width=rating*0.8, color=colors_map.get(rating, '#888'), dash='dot' if rating < 4 else 'solid'), opacity=0.6, hoverinfo='text', text=f"{p} → {o}: {rating}/5"))
        for n in ppl:
            x, y = node_pos[n]
            fig_flow.add_trace(go.Scatter(x=[x], y=[y], mode='markers+text', marker=dict(size=30, color='#302b63', line=dict(color='#f093fb', width=2)), text=[n], textposition='top center', textfont=dict(color='white', size=11), hoverinfo='text'))
        fig_flow.update_layout(showlegend=False, height=500, xaxis=dict(showgrid=False, zeroline=False, showticklabels=False), yaxis=dict(showgrid=False, zeroline=False, showticklabels=False), paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)', title="Who wants whom?")
        st.plotly_chart(fig_flow, use_container_width=True)

        # Mutual heatmap
        st.subheader("🤝 Mutual Compatibility")
        mutual_data = {}
        for p1 in ppl:
            mutual_data[p1] = {}
            for p2 in ppl:
                if p1 == p2: mutual_data[p1][p2] = 0
                else: mutual_data[p1][p2] = (prefs.get(p1, {}).get(p2, 0) + prefs.get(p2, {}).get(p1, 0)) / 2
        df_mut = pd.DataFrame(mutual_data)
        fig_mut = px.imshow(df_mut.values, x=list(df_mut.columns), y=list(df_mut.index), color_continuous_scale="RdYlGn", text_auto=True)
        fig_mut.update_layout(height=500, paper_bgcolor="rgba(0,0,0,0)", font=dict(color="#b8b8d4"))
        st.plotly_chart(fig_mut, use_container_width=True)

        # Most wanted
        st.subheader("👑 Most Wanted")
        wanted = defaultdict(int)
        for other in participants:
            for p in participants:
                if other != p and prefs.get(other, {}).get(p, 0) >= 4: wanted[p] += 1
        if wanted:
            df_w = pd.DataFrame(sorted(wanted.items(), key=lambda x: -x[1]), columns=["Person", "Times Rated ≥4"])
            fig_w = px.bar(df_w, x="Person", y="Times Rated ≥4", color="Times Rated ≥4", color_continuous_scale="RdPu")
            fig_w.update_layout(paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", font=dict(color="#b8b8d4"))
            st.plotly_chart(fig_w, use_container_width=True)

        # Heatmap
        st.subheader("🗺️ Raw Preference Heatmap")
        df_h = pd.DataFrame({p: {o: prefs.get(p, {}).get(o, 0) for o in participants} for p in participants if p in prefs})
        if not df_h.empty:
            fig2 = px.imshow(df_h.values, x=list(df_h.columns), y=list(df_h.index), color_continuous_scale="RdPu", text_auto=True)
            fig2.update_layout(height=500, paper_bgcolor="rgba(0,0,0,0)", font=dict(color="#b8b8d4"))
            st.plotly_chart(fig2, use_container_width=True)

    # Final results
    if results:
        st.markdown("---")
        st.subheader("🏠 Final Room Assignments")
        for i, room in enumerate(results["rooms"]):
            members = room["members"]; sc = room["score"]
            names_html = " ❤️ ".join(members)
            indiv = " | ".join([f"{m}→{[x for x in members if x!=m][0]}: {room['individual_scores'].get(m, '?')}" for m in members if len(members) > 1])
            badge_cls = 'fire' if sc >= 4 else 'great' if sc >= 3.5 else 'ok'
            st.markdown(f'<div class="match-card"><h3>Room {i+1}</h3><div class="names">{names_html}</div><span class="score-badge {badge_cls}">Score: {sc:.1f}/5.0</span><div style="margin-top:8px;color:#b8b8d4;font-size:0.85rem">{indiv}</div></div>', unsafe_allow_html=True)

        unmatched = [p for p in participants if p not in [m for r in results["rooms"] for m in r["members"]]]
        if unmatched:
            st.markdown(f'<div class="wait-card"><h3>⚠️ Wildcard (Unmatched)</h3><p style="color:#b8b8d4">{", ".join(unmatched)}</p></div>', unsafe_allow_html=True)

        avg = np.mean([r["score"] for r in results["rooms"]])
        st.metric("Average Compatibility", f"{avg:.2f}")

        # Monte Carlo
        st.subheader("🎲 Simulation (1000 rounds)")
        sim_scores = []
        pts = participants[:]
        for _ in range(1000):
            random.shuffle(pts)
            rs = sum((prefs.get(pts[j], {}).get(pts[j+1], 0) + prefs.get(pts[j+1], {}).get(pts[j], 0)) / 2 for j in range(0, len(pts) - 1, 2))
            sim_scores.append(rs / (len(pts) // 2))
        fig4 = px.histogram(x=sim_scores, nbins=40, title="Match vs 1000 Random Pairings", color_discrete_sequence=["#764ba2"])
        fig4.add_vline(x=avg, line_dash="dash", line_color="#f5576c", line_width=3, annotation_text=f"Your match: {avg:.2f}")
        fig4.update_layout(paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", font=dict(color="#b8b8d4"))
        st.plotly_chart(fig4, use_container_width=True)
        pct = (np.array(sim_scores) < avg).mean() * 100
        st.info(f"Beats {pct:.0f}% of random pairings!")

    # Actions
    st.markdown("---")
    st.subheader("⚙️ Actions")
    col1, col2 = st.columns(2)
    with col1:
        if st.button("🔒 Force Lock & Match", type="primary", use_container_width=True):
            ok, missing = lock_auction(aid)
            if ok: st.success("Locked!"); st.rerun()
            else: st.error(f"Missing: {', '.join(missing)}")
    with col2:
        if st.button("🗑️ DELETE THIS AUCTION", type="secondary", use_container_width=True):
            import shutil
            shutil.rmtree(auction_dir(aid), ignore_errors=True)
            st.success("Deleted!"); st.session_state.auction_id = None; st.session_state.page = "home"; st.rerun()

    if st.button("🚪 Logout"):
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
