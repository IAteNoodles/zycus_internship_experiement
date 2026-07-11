"""Streamlit frontend for Zycus - Project Health Reporting Agent."""
from __future__ import annotations
import os
from datetime import date

import streamlit as st
import pandas as pd

import database
from database import project_to_domain
from models.rag import compute_rag
from models.sentiment import analyze_sentiment
from reports import weekly_narrative
from schedule import _run_weekly, _run_monthly
from seed import seed

@st.cache_resource
def _init():
    database.init()
    seed()

_init()

st.set_page_config(page_title="Zycus", page_icon=":material/analytics:", layout="wide")

RAG_COLORS = {"Green": "#22c55e", "Amber": "#f59e0b", "Red": "#ef4444", "N/A": "#9ca3af"}
RAG_BADGE = {"Green": "green", "Amber": "orange", "Red": "red", "N/A": "gray"}

# ── Helpers ────────────────────────────────────────────────

def _get_rag(pid: int):
    proj = database.get_project(pid)
    if not proj:
        return None, None, None
    try:
        p = project_to_domain(proj)
    except Exception as e:
        st.warning(f"Could not parse project data: {e}")
        return proj, None, None
    snap = p.latest
    if not snap:
        return proj, None, p
    for e in snap.stakeholder_sentiment:
        e.score = analyze_sentiment(e.comment)
    result = compute_rag(p, snap)
    return proj, result, p


def _fetch_projects():
    rows = database.list_projects()
    data = []
    for row in rows:
        proj = database.get_project(row["id"])
        try:
            p = project_to_domain(proj)
        except Exception:
            data.append({"id": proj["id"], "name": proj["name"], "budget": proj["budget"],
                         "start_date": proj["start_date"], "end_date": proj["end_date"],
                         "stakeholders": proj.get("stakeholders", []),
                         "milestones": proj.get("milestones", []), "snapshots": proj.get("snapshots", []),
                         "RAG": "N/A"})
            continue
        snap = p.latest
        rag = "N/A"
        if snap:
            for e in snap.stakeholder_sentiment:
                e.score = analyze_sentiment(e.comment)
            result = compute_rag(p, snap)
            rag = result.status
        data.append({"id": proj["id"], "name": proj["name"], "budget": proj["budget"],
                     "start_date": proj["start_date"], "end_date": proj["end_date"],
                     "stakeholders": proj.get("stakeholders", []),
                     "milestones": proj.get("milestones", []), "snapshots": proj.get("snapshots", []),
                     "RAG": rag, "_proj_domain": p, "_rag_result": result if snap else None})
    return data


def _run_weekly_checked():
    _run_weekly()
    st.toast("Weekly report generated", icon=":material/description:")


def _run_monthly_checked():
    _run_monthly()
    st.toast("Monthly report generated", icon=":material/description:")


# ── Sidebar ────────────────────────────────────────────────

with st.sidebar:
    st.title(":material/analytics: Zycus")
    st.caption("Project health reporting")
    page = st.segmented_control("Navigate", ["Dashboard", "Projects", "Reports"],
                                label_visibility="collapsed", default="Dashboard")

    all_projs = database.list_projects()
    st.caption(f"{len(all_projs)} project{'s' if len(all_projs) != 1 else ''}"
        )


# ═══════════════════════════════════════════════════════════
#  DASHBOARD
# ═══════════════════════════════════════════════════════════

if page == "Dashboard":
    st.title("Project dashboard")

    data = _fetch_projects()

    if not data:
        st.info("No projects yet. Go to **Projects** to create one.", icon=":material/info:")
    else:
        gs = [d for d in data if d["RAG"] == "Green"]
        am = [d for d in data if d["RAG"] == "Amber"]
        rd = [d for d in data if d["RAG"] == "Red"]

        with st.container(horizontal=True):
            st.metric("Total", len(data), border=True)
            st.metric(":green[Green]", len(gs), border=True)
            st.metric(":orange[Amber]", len(am), border=True)
            st.metric(":red[Red]", len(rd), border=True)

        def _d_warn(d):
            return ":red[⚠ Bad dates]" if d["start_date"] > d["end_date"] else ""

        col_g, col_a, col_r = st.columns(3)
        with col_g:
            st.markdown(f":green[**Green ({len(gs)})**]")
            for d in gs:
                with st.container(border=True):
                    st.markdown(f"**:green-badge[{d['RAG']}]** {d['name']}")
                    st.caption(f"${d['budget']:,.0f}  ·  {d['start_date']} → {d['end_date']} {_d_warn(d)}")
                    st.caption(f"{len(d['milestones'])} milestones, {len(d['snapshots'])} snapshots")
        with col_a:
            st.markdown(f":orange[**Amber ({len(am)})**]")
            for d in am:
                with st.container(border=True):
                    st.markdown(f"**:orange-badge[{d['RAG']}]** {d['name']}")
                    st.caption(f"${d['budget']:,.0f}  ·  {d['start_date']} → {d['end_date']} {_d_warn(d)}")
                    st.caption(f"{len(d['milestones'])} milestones, {len(d['snapshots'])} snapshots")
        with col_r:
            st.markdown(f":red[**Red ({len(rd)})**]")
            for d in rd:
                with st.container(border=True):
                    st.markdown(f"**:red-badge[{d['RAG']}]** {d['name']}")
                    st.caption(f"${d['budget']:,.0f}  ·  {d['start_date']} → {d['end_date']} {_d_warn(d)}")
                    st.caption(f"{len(d['milestones'])} milestones, {len(d['snapshots'])} snapshots")


# ═══════════════════════════════════════════════════════════
#  PROJECTS
# ═══════════════════════════════════════════════════════════

elif page == "Projects":
    st.title("Projects")

    data = _fetch_projects()
    project_ids = [d["id"] for d in data]
    project_map = {d["id"]: d for d in data}

    with st.expander(":material/add: New project", expanded=not bool(data)):
        with st.form("new_project", border=False):
            cols = st.columns(2)
            with cols[0]:
                np_name = st.text_input("Name")
                np_stake = st.text_input("Stakeholders (comma-sep)")
            with cols[1]:
                np_budget = st.number_input("Budget ($)", min_value=0.0, step=10000.0)
                np_start = st.date_input("Start date", value=None)
                np_end = st.date_input("End date", value=None)
            if st.form_submit_button("Create", type="primary"):
                if np_name and np_start and np_end:
                    if np_end < np_start:
                        st.error("End date must be after start date.")
                    else:
                        database.upsert_project({"name": np_name,
                            "stakeholders": [s.strip() for s in np_stake.split(",") if s.strip()],
                            "budget": np_budget, "start_date": np_start.isoformat(), "end_date": np_end.isoformat()})
                        st.toast(f"Created '{np_name}'", icon=":material/check:")
                        st.rerun()

    if not data:
        st.stop()

    if "expanded_pid" not in st.session_state:
        st.session_state.expanded_pid = None

    for d in data:
        pid = d["id"]
        rag = d["RAG"]
        expanded = st.session_state.expanded_pid == pid

        cols = st.columns([1, 11])
        with cols[0]:
            toggle = st.toggle("", value=expanded, key=f"exp_{pid}", label_visibility="collapsed")
            if toggle != expanded:
                st.session_state.expanded_pid = pid if toggle else None
                st.rerun()

        with cols[1]:
            badge = f":{RAG_BADGE.get(rag, 'gray')}-badge[{rag}]"
            dw = " :red[⚠ Bad dates]" if d["start_date"] > d["end_date"] else ""
            st.markdown(f"{badge} **{d['name']}**{dw}  ·  ${d['budget']:,.0f}  ·  {d['start_date']} → {d['end_date']}")
            st.caption(f"{', '.join(d.get('stakeholders', [])) or 'No stakeholders'}  ·  {len(d['milestones'])} milestones, {len(d['snapshots'])} snapshots")

        if expanded:
            proj, rag_result, p_obj = _get_rag(pid)
            if not proj:
                continue

            with st.container(border=True):
                if p_obj and p_obj.latest and rag_result:
                    narrative = weekly_narrative(p_obj, rag_result)
                    if narrative:
                        st.info(narrative, icon=":material/summarize:")

                if rag_result:
                    with st.container(horizontal=True):
                        for s in rag_result.signals:
                            score = s.score if s.score is not None else "N/A"
                            cl = {0: "green", 1: "orange", 2: "red"}.get(s.score, "gray") if s.score is not None else "gray"
                            st.metric(f":{cl}[{score}/2]", s.name.capitalize(), s.rationale[:60], border=True)

                    for o in rag_result.overrides_applied:
                        st.warning(o, icon=":material/warning:")

                # Edit / Delete
                @st.dialog("Delete project")
                def _confirm_delete(pid: int, name: str):
                    st.write(f"Delete **{name}** and all its data?")
                    cols = st.columns(2)
                    with cols[0]:
                        if st.button("Cancel"):
                            st.rerun()
                    with cols[1]:
                        if st.button("Delete", type="primary"):
                            database.delete_project(pid)
                            st.toast(f"Deleted '{name}'", icon=":material/delete:")
                            st.rerun()

                with st.container(horizontal=True):
                    edit_key = f"show_edit_{pid}"
                    if st.button(":material/edit: Edit", key=f"edit_{pid}"):
                        st.session_state[edit_key] = not st.session_state.get(edit_key, False)
                        st.rerun()
                    if st.button(":material/delete: Delete", key=f"del_{pid}"):
                        _confirm_delete(pid, d["name"])

                if st.session_state.get(edit_key, False):
                    with st.form(f"edit_{pid}", border=False):
                        cols = st.columns(2)
                        with cols[0]:
                            e_name = st.text_input("Name", value=proj["name"])
                            e_stake = st.text_input("Stakeholders (comma-sep)", value=", ".join(proj.get("stakeholders", [])))
                        with cols[1]:
                            e_budget = st.number_input("Budget ($)", value=float(proj["budget"]), min_value=0.0)
                            e_start = st.date_input("Start date", value=date.fromisoformat(proj["start_date"]))
                            e_end = st.date_input("End date", value=date.fromisoformat(proj["end_date"]))
                        if st.form_submit_button("Save", type="primary"):
                            if e_end < e_start:
                                st.error("End date must be after start date.")
                            else:
                                database.upsert_project({"id": pid, "name": e_name,
                                    "stakeholders": [s.strip() for s in e_stake.split(",") if s.strip()],
                                    "budget": e_budget, "start_date": e_start.isoformat(), "end_date": e_end.isoformat()})
                                st.toast("Saved", icon=":material/check:")
                                st.rerun()

                # Milestones
                with st.expander(f":material/flag: Milestones ({len(proj.get('milestones', []))})"):
                    milestones = proj.get("milestones", [])
                    if milestones:
                        ms_df = pd.DataFrame(milestones)
                        ms_df = ms_df.drop(columns=["id", "project_id"], errors="ignore")
                        st.dataframe(ms_df, use_container_width=True, hide_index=True)

                    with st.form(f"ms_{pid}", border=False):
                        cols = st.columns(3)
                        with cols[0]: m_name = st.text_input("Name", placeholder="Milestone name")
                        with cols[1]: m_due = st.date_input("Due date", value=None)
                        with cols[2]: m_status = st.selectbox("Status", ["not_started", "in_progress", "complete", "at_risk", "blocked"])
                        if st.form_submit_button(":material/add: Add milestone"):
                            if m_name and m_due:
                                database.upsert_milestone({"project_id": pid, "name": m_name, "due_date": m_due.isoformat(), "status": m_status})
                                st.toast("Milestone added", icon=":material/check:")
                                st.rerun()

                # Snapshots
                with st.expander(f":material/camera: Snapshots ({len(proj.get('snapshots', []))})"):
                    for s in proj.get("snapshots", []):
                        with st.container(border=True):
                            st.markdown(f"**{s['snapshot_date']}**")
                            st.caption(f"Spent ${s.get('budget_spent', 0):,.0f}  ·  {s.get('percent_complete', 'N/A')}% complete  ·  `{s.get('status', 'in_progress')}`")

                            if s.get("blockers"):
                                bcols = st.columns([4, 2, 1])
                                for b in s["blockers"]:
                                    sev = b["severity"].upper()
                                    sc = {"LOW": "gray", "MEDIUM": "orange", "HIGH": "red", "CRITICAL": "red"}.get(sev, "gray")
                                    badge = ":green-badge[Resolved]" if b.get("resolved") else ":red-badge[Open]"
                                    with bcols[0]:
                                        st.markdown(f":{sc}[**{sev}**] {b['description']}")
                                    with bcols[1]:
                                        st.markdown(badge)
                                    with bcols[2]:
                                        if not b.get("resolved"):
                                            if st.button("Resolve", key=f"resolve_{b['id']}"):
                                                database.upsert_blocker({"id": b["id"], "resolved": True, "date_resolved": date.today().isoformat()})
                                                st.rerun()

                            with st.form(f"blocker_{s['id']}", border=False):
                                bcols = st.columns(3)
                                with bcols[0]: b_desc = st.text_input("Description", placeholder="Blocker", key=f"bd_{s['id']}")
                                with bcols[1]: b_sev = st.selectbox("Severity", ["low", "medium", "high", "critical"], key=f"bs_{s['id']}")
                                with bcols[2]: b_date = st.date_input("Date raised", value=None, key=f"br_{s['id']}")
                                if st.form_submit_button(":material/add: Add blocker"):
                                    if b_desc and b_date:
                                        database.upsert_blocker({"snapshot_id": s["id"], "description": b_desc,
                                            "date_raised": b_date.isoformat(), "severity": b_sev})
                                        st.toast("Blocker added", icon=":material/check:")
                                        st.rerun()

                            if s.get("sentiment"):
                                for e in s["sentiment"]:
                                    st.markdown(f"*{e['source']}*: {e['comment']} `[{e['score']}]`")

                            with st.form(f"sentiment_{s['id']}", border=False):
                                scols = st.columns(3)
                                with scols[0]: src = st.text_input("Source", placeholder="Who said it", key=f"src_{s['id']}")
                                with scols[1]: cmt = st.text_input("Comment", placeholder="What they said", key=f"cmt_{s['id']}")
                                with scols[2]: sdt = st.date_input("Date", value=date.today(), key=f"sdt_{s['id']}")
                                if st.form_submit_button(":material/feedback: Add sentiment"):
                                    if src and cmt:
                                        database.upsert_sentiment({"snapshot_id": s["id"], "source": src,
                                            "date_recorded": sdt.isoformat(), "comment": cmt})
                                        st.toast("Sentiment added", icon=":material/check:")
                                        st.rerun()

                    with st.form(f"snap_{pid}", border=False):
                        cols = st.columns(4)
                        with cols[0]: sd = st.date_input("Date", value=None)
                        with cols[1]: sb = st.number_input("Budget spent ($)", min_value=0.0, step=1000.0)
                        with cols[2]: sp = st.number_input("% Complete", min_value=0.0, max_value=100.0, step=5.0)
                        with cols[3]: sn = st.text_input("Notes")
                        if st.form_submit_button(":material/add: Add snapshot"):
                            if sd:
                                database.upsert_snapshot({"project_id": pid, "snapshot_date": sd.isoformat(),
                                    "budget_spent": sb or None, "percent_complete": sp or None, "notes": sn or None})
                                st.toast("Snapshot added", icon=":material/check:")
                                st.rerun()


# ═══════════════════════════════════════════════════════════
#  REPORTS
# ═══════════════════════════════════════════════════════════

elif page == "Reports":
    st.title("Reports")

    with st.container(horizontal=True):
        st.button(":material/description: Generate weekly", type="primary", on_click=_run_weekly_checked)
        st.button(":material/description: Generate monthly", type="primary", on_click=_run_monthly_checked)

    reports = database.list_reports()
    if not reports:
        st.info("No reports generated yet.", icon=":material/info:")
    else:
        for r in reports:
            with st.container(border=True):
                cols = st.columns([1, 1, 3, 2, 1])
                tp = r["type"].upper()
                with cols[0]:
                    c = {"WEEKLY": "blue", "MONTHLY": "violet"}.get(tp, "gray")
                    st.markdown(f":{c}-badge[{tp}]")
                with cols[1]:
                    st.write(r["report_date"])
                with cols[2]:
                    summary = (r.get("summary") or "")[:120]
                    st.caption(summary or "No summary")
                with cols[3]:
                    fp = r.get("file_path")
                    if fp and os.path.exists(fp):
                        with open(fp, "rb") as f:
                            mime = (
                                "application/vnd.openxmlformats-officedocument.presentationml.presentation"
                                if fp.endswith(".pptx") else "application/pdf"
                                if fp.endswith(".pdf") else "text/plain"
                            )
                            st.download_button(":material/download: Download", data=f,
                                               file_name=os.path.basename(fp), mime=mime, key=f"dl_{r['id']}")
                    else:
                        st.caption("No file")
                with cols[4]:
                    if st.button(":material/delete:", key=f"del_{r['id']}", help="Delete report"):
                        database.delete_report(r["id"])
                        st.rerun()
