import streamlit as st
import pandas as pd
import numpy as np
from collections import defaultdict
import json
import plotly.express as px
import random
from datetime import datetime
from pathlib import Path
import time

# ─── Config ─────────────────────────────────────────────────────────
PARTICIPANTS = ["Dikshant", "Shashank", "Bhumanyu", "Deepak", "Atharv", "Sidharth", "Aniket", "Arnav", "Shourya"]
ADMIN_PIN = "dhany2024"
DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)
PREFS_FILE = DATA_DIR / "preferences.json"
RESULTS_FILE = DATA_DIR / "results.json"
LOCK_FILE = DATA_DIR / "locked.txt"

# ─── File helpers (safe for concurrent access) ──────────────────────

def load_prefs():
    try:
        if PREFS_FILE.exists():
            return json.loads(PREFS_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        pass
    return {}

def save_prefs(data):
    tmp = PREFS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(PREFS_FILE)

def load_results():
    try:
        if RESULTS_FILE.exists():
            return json.loads(RESULTS_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        pass
    return None

def save_results(data):
    tmp = RESULTS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    tmp.replace(RESULTS_FILE)

def is_locked():
    return LOCK_FILE.exists()

def lock_and_match():
    prefs = load_prefs()
    missing = [p for p in PARTICIPANTS if p not in prefs]
    if missing:
        return False, missing
    rooms = run_3_algo_consensus(PARTICIPANTS, prefs)
    save_results({"rooms": rooms, "timestamp": datetime.now().isoformat(), "algo": "Consensus"})
    LOCK_FILE.write_text("locked", encoding="utf-8")
    return True, []

def reset_all():
    for f in [PREFS_FILE, RESULTS_FILE, LOCK_FILE]:
        try:
            if f.exists():
                f.unlink()
        except OSError:
            pass

# ─── Algorithms ─────────────────────────────────────────────────────

def mutual_preference_matcher(participants, preferences):
    pts = list(participants)
    scores = {}
    for i, p1 in enumerate(pts):
        for p2 in pts[i + 1:]:
            scores[(p1, p2)] = (
                preferences.get(p1, {}).get(p2, 0) + preferences.get(p2, {}).get(p1, 0)
            ) / 2
    sorted_pairs = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    matched, rooms = set(), []
    for (a, b), sc in sorted_pairs:
        if a not in matched and b not in matched:
            rooms.append({
                "members": [a, b],
                "score": round(sc, 2),
                "individual_scores": {
                    a: preferences.get(a, {}).get(b, 0),
                    b: preferences.get(b, {}).get(a, 0),
                },
            })
            matched.add(a)
            matched.add(b)
    return rooms

def irving_algorithm(participants, preferences):
    pts = list(participants)
    rankings = {
        p: sorted(
            [x for x in pts if x != p],
            key=lambda x: preferences.get(p, {}).get(x, 0),
            reverse=True,
        )
        for p in pts
    }
    partner = {p: None for p in pts}
    free = set(pts)
    prop_idx = {p: 0 for p in pts}

    while free:
        p = min(free, key=lambda x: prop_idx.get(x, 0))
        if prop_idx[p] >= len(rankings[p]):
            free.discard(p)
            continue
        target = rankings[p][prop_idx[p]]
        prop_idx[p] += 1

        if partner[target] is None:
            partner[target] = p
            partner[p] = target
            free.discard(p)
            free.discard(target)
        else:
            cur = partner[target]
            if preferences.get(target, {}).get(p, 0) > preferences.get(target, {}).get(cur, 0):
                partner[target] = p
                partner[p] = target
                free.discard(p)
                free.add(cur)
                partner[cur] = None

    rooms, used = [], set()
    for p in pts:
        if p in used:
            continue
        q = partner.get(p)
        if q:
            s = (preferences.get(p, {}).get(q, 0) + preferences.get(q, {}).get(p, 0)) / 2
            rooms.append({
                "members": [p, q],
                "score": round(s, 2),
                "individual_scores": {
                    p: preferences.get(p, {}).get(q, 0),
                    q: preferences.get(q, {}).get(p, 0),
                },
            })
            used.add(p)
            used.add(q)
    return rooms

def auction_based_matcher(participants, preferences):
    pts = list(participants)
    bids = defaultdict(list)
    for p in pts:
        ranked = sorted(
            [x for x in pts if x != p],
            key=lambda x: preferences.get(p, {}).get(x, 0),
            reverse=True,
        )
        for rank, choice in enumerate(ranked):
            bids[choice].append((p, preferences.get(p, {}).get(choice, 0), rank))

    assignments, used = {}, set()
    for target, bidders in bids.items():
        if target in used or not bidders:
            continue
        bidders.sort(key=lambda x: (-x[1], x[2]))
        for bidder, sc, _ in bidders:
            if bidder not in used:
                assignments[target] = bidder
                assignments[bidder] = target
                used.add(target)
                used.add(bidder)
                break

    rooms, seen = [], set()
    for p in pts:
        if p in seen or p not in assignments:
            continue
        q = assignments[p]
        sc = (preferences.get(p, {}).get(q, 0) + preferences.get(q, {}).get(p, 0)) / 2
        rooms.append({
            "members": [p, q],
            "score": round(sc, 2),
            "individual_scores": {
                p: preferences.get(p, {}).get(q, 0),
                q: preferences.get(q, {}).get(p, 0),
            },
        })
        seen.add(p)
        seen.add(q)
    return rooms

def run_3_algo_consensus(participants, preferences):
    m = mutual_preference_matcher(participants, preferences)
    i = irving_algorithm(participants, preferences)
    a = auction_based_matcher(participants, preferences)
    return max([m, i, a], key=lambda r: sum(x["score"] for x in r))

def detect_cycles(participants, preferences):
    cycles = []

    def dfs(path, current, start):
        if len(path) > 2 and current == start:
            cycles.append(path[:])
            return
        if current in path:
            return
        ranked = sorted(
            [x for x in participants if x != current],
            key=lambda x: preferences.get(current, {}).get(x, 0),
            reverse=True,
        )
        if ranked:
            path.append(current)
            dfs(path, ranked[0], start)
            path.pop()

    for p in participants:
        dfs([], p, p)

    seen, unique = set(), []
    for c in cycles:
        k = tuple(sorted(c))
        if k not in seen:
            seen.add(k)
            unique.append(c)
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
.wait-card h2 { color: #667eea; }
.wait-card .big-num { font-size: 4rem; font-weight: 800; color: #f093fb; }

.done-card {
    background: linear-gradient(135deg, rgba(40,167,69,0.15) 0%, rgba(32,201,151,0.15) 100%);
    border: 1px solid rgba(40,167,69,0.3); border-radius: 20px;
    padding: 2rem; text-align: center; margin: 1rem 0;
}

.name-pill {
    display: inline-block; padding: 10px 24px; border-radius: 30px;
    background: linear-gradient(135deg, #667eea, #764ba2);
    color: white; font-weight: 600; font-size: 1.1rem; margin: 4px;
    border: 2px solid transparent; transition: all 0.3s;
}
.name-pill.submitted {
    background: linear-gradient(135deg, #28a745, #20c997);
    border-color: #28a745;
}
.name-pill.waiting {
    background: rgba(255,255,255,0.08);
    border-color: rgba(255,255,255,0.2); color: #888;
}

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

.hero-gallery {
    display: flex; gap: 16px; justify-content: center;
    margin: 1.5rem 0 0.5rem 0; flex-wrap: wrap;
}
.hero-img-wrapper {
    flex: 1; min-width: 280px; max-width: 48%;
    border-radius: 16px; overflow: hidden;
    border: 2px solid rgba(240,147,251,0.35);
    box-shadow: 0 8px 32px rgba(240,147,251,0.15), 0 2px 8px rgba(0,0,0,0.3);
    transition: transform 0.3s, border-color 0.3s;
}
.hero-img-wrapper:hover {
    transform: translateY(-4px) scale(1.01);
    border-color: rgba(245,87,108,0.6);
}
.hero-img-wrapper img {
    width: 100%; height: 280px; object-fit: cover; display: block;
}
.hero-caption {
    text-align: center; padding: 8px 12px;
    background: linear-gradient(135deg, rgba(15,12,41,0.9), rgba(48,43,99,0.9));
    color: #b8b8d4; font-size: 0.8rem; font-weight: 600;
}
.footer {
    text-align: center; padding: 2rem 0 1rem 0; margin-top: 2rem;
    border-top: 1px solid rgba(240,147,251,0.15);
}
.footer .made-by {
    font-size: 0.95rem; color: #b8b8d4; font-weight: 600;
}
.footer .made-by .name {
    background: linear-gradient(135deg, #f093fb, #f5576c);
    -webkit-background-clip: text; -webkit-text-fill-color: transparent;
    font-weight: 800;
}
.footer .insta {
    font-size: 0.85rem; color: #888; margin-top: 6px;
}
.footer .insta a {
    color: #f093fb; text-decoration: none; font-weight: 600;
}
.footer .insta a:hover { text-decoration: underline; }
.footer .cutu {
    font-size: 1.4rem; margin-top: 6px;
}
</style>
"""

# ─── Streamlit Page Config ──────────────────────────────────────────
st.set_page_config(page_title="Aravali Roommate Auction", page_icon="💘", layout="wide")
st.markdown(CSS, unsafe_allow_html=True)

# ─── State init ─────────────────────────────────────────────────────
if "user" not in st.session_state:
    st.session_state.user = None
if "page" not in st.session_state:
    st.session_state.page = "login"

# ═══════════════════════════════════════════════════════════════════════
# PAGE: LOGIN
# ═══════════════════════════════════════════════════════════════════════
def show_login():
    st.markdown('<div class="main-title">💘 Aravali Roommate Auction</div>', unsafe_allow_html=True)
    st.markdown('<div class="sub-title">9 legends • 2-person rooms • 4 rooms + 1 wildcard</div>', unsafe_allow_html=True)
    st.markdown('<div class="tagline">"Your future roommate is one click away"</div>', unsafe_allow_html=True)
    st.markdown("---")

    # Hero gallery
    img1_path = str(Path(__file__).parent / "static" / "img1.jpeg")
    img2_path = str(Path(__file__).parent / "static" / "img2.jpeg")
    if Path(img1_path).exists() and Path(img2_path).exists():
        import base64
        def img_to_base64(path):
            with open(path, "rb") as f:
                return base64.b64encode(f.read()).decode()
        b64_1 = img_to_base64(img1_path)
        b64_2 = img_to_base64(img2_path)
        st.markdown(f"""
        <div class="hero-gallery">
            <div class="hero-img-wrapper">
                <img src="data:image/jpeg;base64,{b64_1}" alt="Aravali vibe 1" />
                <div class="hero-caption">💘 Aravali — Where Roommates Find Each Other</div>
            </div>
            <div class="hero-img-wrapper">
                <img src="data:image/jpeg;base64,{b64_2}" alt="Aravali vibe 2" />
                <div class="hero-caption">🏠 Fair, Private, Algorithm-Driven Matching</div>
            </div>
        </div>
        """, unsafe_allow_html=True)

    prefs = load_prefs()
    locked = is_locked()
    all_submitted = all(p in prefs for p in PARTICIPANTS)

    if locked:
        st.markdown("""
        <div class="done-card">
            <h2 style="color:#28a745">✅ Auction Complete!</h2>
            <p style="color:#b8b8d4">Results are ready. Select your name to see your match.</p>
        </div>
        """, unsafe_allow_html=True)
    elif all_submitted:
        st.markdown("""
        <div class="done-card">
            <h2 style="color:#28a745">🎉 All 9 profiles submitted!</h2>
            <p style="color:#b8b8d4">Lock in preferences and run the algorithm.</p>
        </div>
        """, unsafe_allow_html=True)
        if st.button("🔒 LOCK & RUN MATCH", type="primary", use_container_width=True):
            with st.spinner("Running 3 algorithms and picking the best..."):
                ok, missing = lock_and_match()
            if ok:
                st.success("Matching complete!")
                time.sleep(0.5)
                st.rerun()
            else:
                st.error(f"Still waiting on: {', '.join(missing)}")
    else:
        submitted_names = list(prefs.keys())
        waiting = [p for p in PARTICIPANTS if p not in submitted_names]
        pct = len(submitted_names) / len(PARTICIPANTS)
        st.progress(pct, text=f"{len(submitted_names)}/{len(PARTICIPANTS)} have submitted")

        st.markdown('<div class="wait-card">', unsafe_allow_html=True)
        st.markdown(f'<div class="big-num">{len(waiting)}</div>', unsafe_allow_html=True)
        label = "person still needs to submit" if len(waiting) == 1 else "people still need to submit"
        st.markdown(f'<p style="color:#b8b8d4;font-size:1.1rem">{label}</p>', unsafe_allow_html=True)
        st.markdown("</div>", unsafe_allow_html=True)

        st.markdown("**Status of each participant:**")
        for p in PARTICIPANTS:
            if p in submitted_names:
                st.markdown(f'<span class="name-pill submitted">✅ {p}</span>', unsafe_allow_html=True)
            else:
                st.markdown(f'<span class="name-pill waiting">⏳ {p}</span>', unsafe_allow_html=True)

    st.markdown("---")
    st.markdown("### 🙋 Who are you?")
    col1, col2 = st.columns([3, 1])
    with col1:
        selected = st.selectbox("Select your name", [""] + PARTICIPANTS, label_visibility="collapsed")
    with col2:
        if st.button("Enter", use_container_width=True, type="primary"):
            if selected:
                st.session_state.user = selected
                if is_locked():
                    st.session_state.page = "results"
                elif selected in prefs:
                    st.session_state.page = "submitted"
                else:
                    st.session_state.page = "preferences"
                st.rerun()
            else:
                st.warning("Pick your name!")

    st.markdown("""
    <div class="footer">
        <div class="cutu">🥰</div>
        <div class="made-by">Made with 💗 by <span class="name">Atharv</span></div>
        <div class="insta">Follow me on Instagram → <a href="https://instagram.com/atharvv_t" target="_blank">@atharvv_t</a></div>
    </div>
    """, unsafe_allow_html=True)

# ═══════════════════════════════════════════════════════════════════════
# PAGE: PREFERENCES
# ═══════════════════════════════════════════════════════════════════════
def show_preferences():
    user = st.session_state.user
    st.markdown(f'<div class="main-title">💘 {user}&#39;s Preferences</div>', unsafe_allow_html=True)
    st.markdown('<div class="tagline">Rate every other person — your ratings are completely private</div>', unsafe_allow_html=True)
    st.markdown("---")

    others = [p for p in PARTICIPANTS if p != user]
    saved = load_prefs().get(user, {})

    st.markdown("#### How much do you want each person as your roommate?")
    prefs = {}
    for other in others:
        col1, col2 = st.columns([2, 3])
        with col1:
            st.markdown(f"**{other}**")
        with col2:
            default = saved.get(other, 3)
            val = st.slider(
                "",
                min_value=1, max_value=5, value=default,
                key=f"my_pref_{user}_{other}",
                label_visibility="collapsed",
            )
            labels = {1: "❌ Nope", 2: "😬 Meh", 3: "🤝 Fine", 4: "😊 Want", 5: "🔥 Dream"}
            st.caption(f"{labels[val]}")
            prefs[other] = val

    st.markdown("---")
    if saved:
        st.info("📝 You already submitted. Submitting again will overwrite your previous preferences.")

    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        btn_label = "🔄 UPDATE MY PREFERENCES" if saved else "💾 SUBMIT MY PREFERENCES"
        if st.button(btn_label, type="primary", use_container_width=True):
            all_prefs = load_prefs()
            all_prefs[user] = prefs
            save_prefs(all_prefs)
            st.session_state.page = "submitted"
            st.rerun()

    if st.button("← Back to Login"):
        st.session_state.user = None
        st.session_state.page = "login"
        st.rerun()

# ═══════════════════════════════════════════════════════════════════════
# PAGE: SUBMITTED (waiting)
# ═══════════════════════════════════════════════════════════════════════
def show_submitted():
    user = st.session_state.user
    st.markdown(f'<div class="main-title">💘 Thanks, {user}!</div>', unsafe_allow_html=True)
    st.markdown("---")

    prefs = load_prefs()
    submitted = list(prefs.keys())
    waiting = [p for p in PARTICIPANTS if p not in submitted]
    done = len(submitted)

    st.markdown("""
    <div class="done-card">
        <h2 style="color:#28a745">✅ Your preferences are locked in!</h2>
        <p style="color:#b8b8d4">No one can see your ratings. Sit tight while others submit.</p>
    </div>
    """, unsafe_allow_html=True)

    pct = done / len(PARTICIPANTS)
    st.progress(pct, text=f"{done}/{len(PARTICIPANTS)} have submitted")

    st.markdown("**Who's done:**")
    for p in PARTICIPANTS:
        if p in submitted:
            st.markdown(f'<span class="name-pill submitted">✅ {p}</span>', unsafe_allow_html=True)
        else:
            st.markdown(f'<span class="name-pill waiting">⏳ {p}</span>', unsafe_allow_html=True)

    if waiting:
        st.info(f"Waiting for: {', '.join(waiting)}")
    else:
        st.success("All done! Ask someone to lock & run the match.")

    st.markdown("---")
    col1, col2 = st.columns(2)
    with col1:
        if st.button("🔄 Refresh status", use_container_width=True):
            st.rerun()
    with col2:
        if st.button("✏️ Change my prefs", use_container_width=True):
            st.session_state.page = "preferences"
            st.rerun()
    if st.button("🚪 Logout"):
        st.session_state.user = None
        st.session_state.page = "login"
        st.rerun()

# ═══════════════════════════════════════════════════════════════════════
# PAGE: RESULTS (private per user)
# ═══════════════════════════════════════════════════════════════════════
def show_results():
    user = st.session_state.user
    results = load_results()
    if not results:
        st.warning("Results not ready yet.")
        col1, col2 = st.columns(2)
        with col1:
            if st.button("🔄 Refresh"):
                st.rerun()
        with col2:
            if st.button("🚪 Logout"):
                st.session_state.user = None
                st.session_state.page = "login"
                st.rerun()
        return

    st.markdown(f'<div class="main-title">💘 {user}&#39;s Match Result</div>', unsafe_allow_html=True)
    st.markdown('<div class="tagline">The algorithm has spoken...</div>', unsafe_allow_html=True)
    st.markdown("---")

    # Find user's room
    my_room = None
    my_roommate = None
    room_num = 0
    for i, room in enumerate(results["rooms"]):
        if user in room["members"]:
            my_room = room
            others_in_room = [m for m in room["members"] if m != user]
            my_roommate = others_in_room[0] if others_in_room else None
            room_num = i + 1
            break

    if not my_room or not my_roommate:
        st.error("Could not find your match. Contact admin.")
        if st.button("🚪 Logout"):
            st.session_state.user = None
            st.session_state.page = "login"
            st.rerun()
        return

    sc = my_room["score"]
    sa = my_room["individual_scores"].get(user, 0)
    sb = my_room["individual_scores"].get(my_roommate, 0)

    if sc >= 4:
        badge, css, emoji = "IT'S A MATCH!", "fire", "🔥"
    elif sc >= 3.5:
        badge, css, emoji = "GREAT MATCH", "great", "💜"
    else:
        badge, css, emoji = "SOLID MATCH", "ok", "🤝"

    st.markdown(f"""
    <div class="match-card" style="text-align:center; padding:3rem">
        <h3>{emoji} Your Roommate {emoji}</h3>
        <div class="names" style="font-size:2.5rem; margin:15px 0">
            {user} &nbsp;❤️&nbsp; {my_roommate}
        </div>
        <div style="margin:15px 0">
            <span class="score-badge {css}" style="font-size:1.1rem; padding:8px 24px">
                {badge} — {sc:.1f}/5.0
            </span>
        </div>
        <div style="margin-top:20px; color:#b8b8d4">
            <p>You rated {my_roommate}: <b>{'❤️' * sa} ({sa}/5)</b></p>
            <p>{my_roommate} rated you: <b>{'❤️' * sb} ({sb}/5)</b></p>
        </div>
        <div style="margin-top:15px; color:#888; font-size:0.85rem">
            Room #{room_num} &nbsp;|&nbsp; Algorithm: {results.get('algo', 'Consensus')}
        </div>
    </div>
    """, unsafe_allow_html=True)

    # Show all rooms
    st.markdown("#### All Room Assignments")
    for i, room in enumerate(results["rooms"]):
        names = " & ".join(room["members"])
        is_me = user in room["members"]
        highlight = "🔥" if is_me else "🏠"
        if is_me:
            st.markdown(f"""
            <div class="match-card" style="border-color:#f093fb">
                <h3>{highlight} Room {i+1} (YOU)</h3>
                <div class="names" style="font-size:1.2rem">{names}</div>
            </div>
            """, unsafe_allow_html=True)
        else:
            st.markdown(f"""
            <div class="match-card" style="opacity:0.6">
                <h3>{highlight} Room {i+1}</h3>
                <div class="names" style="font-size:1.2rem">{names}</div>
            </div>
            """, unsafe_allow_html=True)

    st.markdown("---")
    st.caption("🔒 Your preferences and scores are private. Only you can see your individual ratings.")

    if st.button("🚪 Logout"):
        st.session_state.user = None
        st.session_state.page = "login"
        st.rerun()

# ═══════════════════════════════════════════════════════════════════════
# PAGE: ADMIN
# ═══════════════════════════════════════════════════════════════════════
def show_admin():
    st.markdown('<div class="main-title">🔑 Admin Dashboard</div>', unsafe_allow_html=True)
    st.markdown("---")

    pin = st.text_input("Admin PIN", type="password")
    if pin != ADMIN_PIN:
        st.warning("Enter correct PIN to access admin panel.")
        if st.button("← Back"):
            st.session_state.page = "login"
            st.session_state.user = None
            st.rerun()
        return

    prefs = load_prefs()
    results = load_results()

    c1, c2, c3 = st.columns(3)
    c1.metric("Submissions", f"{len(prefs)}/{len(PARTICIPANTS)}")
    c2.metric("Locked", "Yes" if is_locked() else "No")
    c3.metric("Results", "Ready" if results else "Pending")

    # Full preference matrix
    st.subheader("📋 Full Preference Matrix")
    if prefs:
        missing = [p for p in PARTICIPANTS if p not in prefs]
        if missing:
            st.warning(f"Missing submissions from: {', '.join(missing)}")
        df = pd.DataFrame(
            {p: {o: prefs.get(p, {}).get(o, "-") for o in PARTICIPANTS} for p in PARTICIPANTS if p in prefs}
        )
        if not df.empty:
            st.dataframe(df.style.highlight_max(axis=0, color="#c8e6c9").highlight_min(axis=0, color="#ffcdd2"), use_container_width=True)

    # Cycles
    if len(prefs) >= 2:
        st.subheader("🔄 Preference Cycles")
        cycles = detect_cycles(PARTICIPANTS, prefs)
        if cycles:
            for cyc in cycles:
                st.warning(f"Cycle: {' → '.join(cyc)} → {cyc[0]}")
        else:
            st.success("No cycles detected!")

    # Most wanted
    if prefs:
        st.subheader("👑 Most Wanted")
        wanted = defaultdict(int)
        for other in PARTICIPANTS:
            for p in PARTICIPANTS:
                if other != p and prefs.get(other, {}).get(p, 0) >= 4:
                    wanted[p] += 1
        if wanted:
            df_w = pd.DataFrame(sorted(wanted.items(), key=lambda x: -x[1]), columns=["Person", "Times Rated ≥4"])
            fig = px.bar(df_w, x="Person", y="Times Rated ≥4", color="Times Rated ≥4", color_continuous_scale="RdPu")
            fig.update_layout(paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", font=dict(color="#b8b8d4"))
            st.plotly_chart(fig, use_container_width=True)

        # Heatmap
        st.subheader("🗺️ Preference Heatmap")
        df_h = pd.DataFrame({p: {o: prefs.get(p, {}).get(o, 0) for o in PARTICIPANTS} for p in PARTICIPANTS if p in prefs})
        if not df_h.empty:
            fig2 = px.imshow(df_h.values, x=list(df_h.columns), y=list(df_h.index), color_continuous_scale="RdPu", text_auto=True)
            fig2.update_layout(height=600, paper_bgcolor="rgba(0,0,0,0)", font=dict(color="#b8b8d4"))
            st.plotly_chart(fig2, use_container_width=True)

    # Results
    if results:
        st.subheader("🏠 All Room Assignments")
        for i, room in enumerate(results["rooms"]):
            members = room["members"]
            sc = room["score"]
            names_html = " ❤️ ".join(members)
            indiv_parts = []
            for m in members:
                indiv_parts.append(f"{m}→{'/'.join(o for o in members if o != m)}: {room['individual_scores'].get(m, '?')}")
            indiv_str = " | ".join(indiv_parts)
            badge_cls = 'fire' if sc >= 4 else 'great' if sc >= 3.5 else 'ok'
            st.markdown(f"""
            <div class="match-card">
                <h3>Room {i+1}</h3>
                <div class="names">{names_html}</div>
                <span class="score-badge {badge_cls}">Score: {sc:.1f}/5.0</span>
                <div style="margin-top:8px;color:#b8b8d4;font-size:0.85rem">{indiv_str}</div>
            </div>
            """, unsafe_allow_html=True)

        avg = np.mean([r["score"] for r in results["rooms"]])
        st.metric("Average Compatibility", f"{avg:.2f}")

        # Monte Carlo
        st.subheader("🎲 Simulation (1000 rounds)")
        sim_scores = []
        pts = PARTICIPANTS[:]
        for _ in range(1000):
            random.shuffle(pts)
            round_score = 0
            for j in range(0, len(pts) - 1, 2):
                a, b = pts[j], pts[j + 1]
                round_score += (prefs.get(a, {}).get(b, 0) + prefs.get(b, {}).get(a, 0)) / 2
            sim_scores.append(round_score / (len(pts) // 2))
        fig4 = px.histogram(x=sim_scores, nbins=40, title="Your Match vs 1000 Random Pairings", color_discrete_sequence=["#764ba2"])
        fig4.add_vline(x=avg, line_dash="dash", line_color="#f5576c", line_width=3, annotation_text=f"Your match: {avg:.2f}")
        fig4.update_layout(paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", font=dict(color="#b8b8d4"))
        st.plotly_chart(fig4, use_container_width=True)
        pct = (np.array(sim_scores) < avg).mean() * 100
        st.info(f"Your matching beats {pct:.0f}% of random pairings!")

    # Actions
    st.markdown("---")
    st.subheader("⚙️ Actions")
    col1, col2 = st.columns(2)
    with col1:
        if st.button("🔒 Force Lock & Match", type="primary", use_container_width=True):
            ok, missing = lock_and_match()
            if ok:
                st.success("Locked and matched!")
                st.rerun()
            else:
                st.error(f"Still waiting on: {', '.join(missing)}")
    with col2:
        if st.button("🗑️ RESET EVERYTHING", type="secondary", use_container_width=True):
            reset_all()
            st.success("All data cleared!")
            st.rerun()

    if st.button("🚪 Logout"):
        st.session_state.user = None
        st.session_state.page = "login"
        st.rerun()

# ═══════════════════════════════════════════════════════════════════════
# ROUTER
# ═══════════════════════════════════════════════════════════════════════
query_params = st.query_params
if query_params.get("admin") == "1":
    show_admin()
elif st.session_state.user is None:
    show_login()
elif st.session_state.page == "preferences":
    show_preferences()
elif st.session_state.page == "submitted":
    if is_locked():
        st.session_state.page = "results"
        show_results()
    else:
        show_submitted()
elif st.session_state.page == "results":
    if is_locked():
        show_results()
    else:
        st.session_state.page = "submitted"
        show_submitted()
else:
    show_login()
