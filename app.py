import streamlit as st
import json
import uuid
import random
import pandas as pd
import urllib.parse
from streamlit_autorefresh import st_autorefresh

# --- PAGE CONFIGURATION ---
st.set_page_config(page_title="EHR Clinical Simulation", page_icon="🩺", layout="wide")
st_autorefresh(interval=3000, limit=None, key="auto_refresh")

st.markdown("""
<style>
    .patient-banner { background-color: #0073b3; color: white; padding: 14px 25px; border-radius: 6px; font-size: 22px; display: flex; justify-content: space-between; margin-bottom: 20px; }
    .content-title { font-size: 26px; font-weight: 600; color: #0073b3; border-bottom: 2px solid #d9d9d9; padding-bottom: 10px; margin-bottom: 20px; }
    .correct-banner { background-color: #d4edda; color: #155724; padding: 20px; border-radius: 8px; font-size: 24px; text-align: center; font-weight: bold; margin-bottom: 15px; }
    .incorrect-banner { background-color: #f8d7da; color: #721c24; padding: 20px; border-radius: 8px; font-size: 24px; text-align: center; font-weight: bold; margin-bottom: 15px; }
</style>
""", unsafe_allow_html=True)

@st.cache_data
def load_data(file_name):
    with open(file_name, "r") as f:
        return json.load(f)

case_catalog = {
    "acute_cystitis": load_data("case_data.json"),
    "glomerulonephritis": load_data("case_data_glomerulonephritis.json"),
}
def get_patient(case_id):
    fallback_patients = {
        "acute_cystitis": {"name": "Susan", "age": 42, "mrn": "MRN84350"},
    }
    return case_catalog[case_id].get("patient", fallback_patients.get(case_id, {
        "name": "Unassigned Patient", "age": "", "mrn": "",
    }))


patient_labels = {
    case_id: get_patient(case_id)["name"]
    for case_id in case_catalog
}

# --- SHARED GLOBAL STATE ---
@st.cache_resource
def get_global_state():
    return {
        "status": "lobby", 
        "case_id": next(iter(case_catalog)),
        "current_scene": "start",
        "votes": {},               
        "sata_votes": {},          
        "voters": {}, 
        "patient_history": {},     
        "performed_exams": {},     
        "placed_orders": {},       
        "available_results": {},   
        "orders_unlocked": False,
        "new_results_alert": False,
        "revealed": False,
        "reveal_score_awarded": False,
        "score": 0,
        "max_score": 4
    }
gs = get_global_state()
gs.setdefault("case_id", next(iter(case_catalog)))

def get_active_case():
    return case_catalog.get(gs["case_id"], case_catalog[next(iter(case_catalog))])

if "user_id" not in st.session_state:
    st.session_state.user_id = str(uuid.uuid4())
if "selected_tab" not in st.session_state:
    st.session_state.selected_tab = "Clinical Notes"

# --- CORE FUNCTIONS ---
def advance_scene(next_scene_id):
    active_case = get_active_case()
    if not next_scene_id or next_scene_id not in active_case["scenes"]:
        return False

    # prompt_to_order is an instructional scene; decision_labs owns the order form.
    if next_scene_id == "prompt_to_order":
        next_scene_id = "decision_labs"

    gs["current_scene"] = next_scene_id
    gs["votes"] = {}
    gs["sata_votes"] = {}
    gs["voters"] = {}
    gs["revealed"] = False
    gs["reveal_score_awarded"] = False
    
    if next_scene_id in ("prompt_to_order", "decision_labs"):
        gs["orders_unlocked"] = True
        st.session_state.selected_tab = "Orders"
    elif next_scene_id == "results_pending":
        gs["new_results_alert"] = True
        st.session_state.selected_tab = "Lab Results"
    else:
        st.session_state.selected_tab = "Clinical Notes"
    return True

def reset_case(case_id=None, return_home=True):
    if case_id is not None and case_id in case_catalog:
        gs["case_id"] = case_id
    gs.update({
        "status": "lobby" if return_home else "active",
        "current_scene": "start",
        "votes": {},
        "sata_votes": {},
        "voters": {},
        "patient_history": {},
        "performed_exams": {},
        "placed_orders": {},
        "available_results": {},
        "orders_unlocked": False,
        "new_results_alert": False,
        "revealed": False,
        "reveal_score_awarded": False,
        "score": 0,
    })
    st.session_state.selected_tab = "Clinical Notes"

def auto_resolve_scene():
    active_case = get_active_case()
    scene = active_case["scenes"].get(gs["current_scene"], {})
    total_voters = max(len(gs["voters"]), 1)
    
    if scene.get("isSataScreen"):
        target_dict = gs["patient_history"] if "History" in scene["title"] else gs["performed_exams"]
        for opt_key, opt_data in scene["sataOptions"].items():
            if gs["sata_votes"].get(opt_key, 0) / total_voters > 0.5:
                target_dict[opt_key] = opt_data["finding"]
        return advance_scene(scene.get("onSubmit"))
        
    elif "decision" in scene:
        if not gs["votes"]:
            return False

        winning_choice = max(gs["votes"].items(), key=lambda vote: vote[1])[0]
        next_scene_id = next(
            (choice["path"] for choice in scene["decision"]["choices"]
             if choice["text"] == winning_choice),
            None,
        )
        return advance_scene(next_scene_id)
    return False

def check_answer_correctness():
    active_case = get_active_case()
    scene = active_case["scenes"].get(gs["current_scene"], {})
    if "decision" in scene and gs["revealed"]:
        for c in scene["decision"]["choices"]:
            if c.get("isCorrect") and gs["voters"].get(st.session_state.user_id) == c["text"]:
                return True
    elif scene.get("isDdxScreen") and gs["revealed"]:
        if gs["voters"].get(st.session_state.user_id) == scene["correctAnswer"]:
            return True
    return False

def check_if_wrong():
    if gs["revealed"] and st.session_state.user_id in gs["voters"] and not check_answer_correctness():
        return True
    return False

def simulate_bot_votes(scene, bot_count=75):
    for bot_number in range(bot_count):
        bot_id = f"dev_bot_{bot_number}"
        if bot_id in gs["voters"]:
            continue

        if scene.get("isSataScreen") or scene.get("isOrderScreen"):
            options = (
                scene.get("sataOptions", {})
                if scene.get("isSataScreen")
                else {
                    test["id"]: test
                    for tests in scene.get("orderSets", {}).values()
                    for test in tests
                }
            )
            selected_options = [
                option_key
                for option_key in options
                if random.choice([True, False])
            ]
            for option_key in selected_options:
                gs["sata_votes"][option_key] = gs["sata_votes"].get(option_key, 0) + 1
            gs["voters"][bot_id] = "voted"
        elif scene.get("isDdxScreen"):
            choice = random.choice(scene["ddxOptions"])
            gs["votes"][choice] = gs["votes"].get(choice, 0) + 1
            gs["voters"][bot_id] = choice
        elif "decision" in scene:
            choice = random.choice(scene["decision"]["choices"])["text"]
            gs["votes"][choice] = gs["votes"].get(choice, 0) + 1
            gs["voters"][bot_id] = choice

# --- SIDEBAR (ALWAYS VISIBLE) ---
st.sidebar.title("🩺 Navigation")
role = st.sidebar.radio("Select View:", ["Student (Voter)", "Instructor (Host)"])
active_case = get_active_case()

if role == "Instructor (Host)":
    with st.sidebar.expander("Case Controls", expanded=True):
        selected_case = st.selectbox(
            "Patient:",
            options=list(case_catalog),
            format_func=lambda case_id: patient_labels[case_id],
            index=list(case_catalog).index(gs["case_id"]),
            key="case_selector",
        )
        if st.button("🏠 Reset and Switch Case", use_container_width=True):
            reset_case(selected_case)
            st.rerun()
        if st.button("↻ Reset Current Case", use_container_width=True):
            reset_case(gs["case_id"])
            st.rerun()
        if gs["status"] == "active" and st.button("⌂ Return to Case Home", use_container_width=True):
            reset_case(gs["case_id"])
            st.rerun()

# EHR Menu renders here only if active
if gs["status"] == "active":
    st.sidebar.divider()
    st.sidebar.markdown("### 📑 EHR Menu")
    
    tabs = ["Clinical Notes", "Problem List", "Orders"]
    tabs.append("🔴 Lab Results" if gs["new_results_alert"] else "Lab Results")
    tabs.append("Diagnostic Results")
    
    st.session_state.selected_tab = st.sidebar.radio(
        "Navigate tabs:", 
        tabs, 
        index=tabs.index(st.session_state.selected_tab) if st.session_state.selected_tab in tabs else 0,
        label_visibility="collapsed"
    )

st.sidebar.divider()
st.sidebar.markdown("### 📱 Student Access")
app_url = st.sidebar.text_input("Enter Web App URL:", value="https://your-app-url.streamlit.app")
if app_url:
    encoded_url = urllib.parse.quote(app_url)
    st.sidebar.image(f"https://api.qrserver.com/v1/create-qr-code/?size=150x150&data={encoded_url}")
    st.sidebar.caption("Scan to join the simulation")

# --- LOBBY MODE ---
if gs["status"] == "lobby":
    patient = get_patient(gs["case_id"])
    st.title("Patient Selection")
    st.write("### EHR Clinical Simulation")
    if role == "Student (Voter)":
        selected_patient = st.selectbox(
            "Search patients by name:",
            options=list(case_catalog),
            format_func=lambda case_id: patient_labels[case_id],
            index=list(case_catalog).index(gs["case_id"]),
            key="student_patient_selector",
        )
        if st.button("Open Patient Chart", type="primary"):
            reset_case(selected_patient)
            st.rerun()
    st.write(f"Selected patient: **{patient['name']}, {patient['age']} years old**")
    st.write("Scan the QR code on the left to join the session.")
    st.markdown(f"**Current Players Joined: {len(gs['voters'])}**")
    
    if role == "Student (Voter)":
        if st.button("Join Class", type="primary"):
            gs["voters"][st.session_state.user_id] = "joined"
            st.rerun()
        if st.session_state.user_id in gs["voters"]:
            st.success("You are in! Waiting for the instructor to start...")
    elif role == "Instructor (Host)":
        if st.button("🚀 Start Simulation", type="primary"):
            gs["status"] = "active"
            gs["voters"] = {}
            st.rerun()
    st.stop()

# --- ACTIVE SIMULATION ---
patient = active_case.get("patient", {"name": "Susan", "age": 42, "mrn": "MRN84350"})
patient_gender = f" {patient['gender']}" if patient.get("gender") else ""
st.markdown(f"<div class='patient-banner'><div><strong>Patient:</strong> {patient['name']}, {patient['age']}Y{patient_gender}</div><div><strong>MRN:</strong> {patient['mrn']}</div></div>", unsafe_allow_html=True)
st.progress(gs["score"] / gs["max_score"] if gs["score"] > 0 else 0, text=f"Class Competency Score: {gs['score']} / {gs['max_score']}")

current_scene = active_case["scenes"].get(gs["current_scene"], {})
is_quiz_question = "isCorrect" in str(current_scene) or current_scene.get("isDdxScreen")

if role == "Instructor (Host)" and gs["status"] == "active":
    with st.sidebar.expander("Developer Testing", expanded=False):
        dev_mode = st.checkbox("Enable testing tools", key="dev_mode_enabled")
        if dev_mode:
            if st.button(
                "🤖 Simulate 75 Random Votes",
                disabled=gs["revealed"],
                key="simulate_votes",
            ):
                simulate_bot_votes(current_scene)
                st.rerun()

# --- TAB: CLINICAL NOTES ---
if "Clinical Notes" in st.session_state.selected_tab:
    st.markdown(f"<div class='content-title'>{current_scene.get('title', 'Clinical Note')}</div>", unsafe_allow_html=True)
    st.markdown(current_scene.get("narrative", ""), unsafe_allow_html=True)
    
    if gs["patient_history"] or gs["performed_exams"]:
        st.markdown("<div class='findings-box'>", unsafe_allow_html=True)
        if gs["patient_history"]:
            st.markdown("#### 📋 History Findings:")
            for val in gs["patient_history"].values(): st.markdown(f"- {val}")
        if gs["performed_exams"]:
            st.markdown("#### 🩺 Physical Exam Findings:")
            for val in gs["performed_exams"].values(): st.markdown(f"- {val}")
        st.markdown("</div>", unsafe_allow_html=True)

    if current_scene.get("isEnd"):
        st.success("Case Complete! Review key rationales.")
        for key, rationale in active_case.get("rationales", {}).items():
            with st.expander(f"Rationale: {key.replace('_', ' ').title()}"): st.markdown(rationale, unsafe_allow_html=True)
    
    elif role == "Student (Voter)":
        if check_answer_correctness(): st.markdown("<div class='correct-banner'>✅ Correct!</div>", unsafe_allow_html=True)
        if check_if_wrong(): st.markdown("<div class='incorrect-banner'>❌ Incorrect.</div>", unsafe_allow_html=True)
        
        if st.session_state.user_id in gs["voters"] and not gs["revealed"]:
            st.info("✅ Vote locked! Eyes on the projector.")
        elif not gs["revealed"]:
            if "decision" in current_scene:
                choice = st.radio(current_scene['decision'].get('prompt', 'Choose:'), [c["text"] for c in current_scene["decision"]["choices"]])
                if st.button("Submit", type="primary"):
                    gs["votes"][choice] = gs["votes"].get(choice, 0) + 1
                    gs["voters"][st.session_state.user_id] = choice
                    st.rerun()
            elif current_scene.get("isDdxScreen"):
                choice = st.radio("Leading Diagnosis:", current_scene["ddxOptions"])
                if st.button("Submit", type="primary"):
                    gs["votes"][choice] = gs["votes"].get(choice, 0) + 1
                    gs["voters"][st.session_state.user_id] = choice
                    st.rerun()
            elif current_scene.get("isSataScreen"):
                st.write("Select all that apply:")
                selected = [k for k, v in current_scene["sataOptions"].items() if st.checkbox(v["label"])]
                if st.button("Submit", type="primary"):
                    for k in selected: gs["sata_votes"][k] = gs["sata_votes"].get(k, 0) + 1
                    gs["voters"][st.session_state.user_id] = "voted"
                    st.rerun()
            elif current_scene.get("onSubmit"):
                if st.button("Continue to History", type="primary"):
                    advance_scene(current_scene["onSubmit"])
                    st.rerun()

    elif role == "Instructor (Host)":
        st.write(f"🗳️ **{len(gs['voters'])}** Students have voted.")
        
        # 1. KAHOOT-STYLE QUESTIONS
        if is_quiz_question:
            if not gs["revealed"]:
                if st.button("🔒 Lock Votes & Reveal Results", type="primary"):
                    gs["revealed"] = True
                    gs["reveal_score_awarded"] = False
                    winning_choice = max(gs["votes"], key=gs["votes"].get) if gs["votes"] else ""
                    if current_scene.get("isDdxScreen") and winning_choice == current_scene["correctAnswer"]:
                        gs["score"] += 1
                        gs["reveal_score_awarded"] = True
                    elif "decision" in current_scene:
                        for c in current_scene["decision"]["choices"]:
                            if c["text"] == winning_choice and c.get("isCorrect"):
                                gs["score"] += 1
                                gs["reveal_score_awarded"] = True
                    st.rerun()
            else:
                if gs["votes"]: st.bar_chart(pd.DataFrame(list(gs["votes"].items()), columns=["Choice", "Votes"]).set_index("Choice"))
                if st.button("🔓 Unlock Question and Reopen Voting"):
                    if gs["reveal_score_awarded"]:
                        gs["score"] = max(0, gs["score"] - 1)
                    gs["revealed"] = False
                    gs["reveal_score_awarded"] = False
                    st.rerun()
                winning_choice = max(gs["votes"], key=gs["votes"].get) if gs["votes"] else ""
                next_path = current_scene.get("onSubmit")
                if "decision" in current_scene:
                    for c in current_scene["decision"]["choices"]:
                        if c["text"] == winning_choice: next_path = c["path"]
                if st.button(f"Advance to Next Step"):
                    advance_scene(next_path)
                    st.rerun()
                    
        # 2. STANDARD CLINICAL DECISIONS & SATA
        else:
            if current_scene.get("isSataScreen") and gs["sata_votes"]:
                st.bar_chart(pd.DataFrame([{"Option": v["label"], "Votes": gs["sata_votes"].get(k, 0)} for k, v in current_scene["sataOptions"].items()]).set_index("Option"))
            elif gs["votes"]:
                st.bar_chart(pd.DataFrame(list(gs["votes"].items()), columns=["Choice", "Votes"]).set_index("Choice"))

            if st.button("⚖️ Auto-Resolve Class Consensus (>50%)", type="primary"):
                if auto_resolve_scene():
                    st.rerun()
                st.error("No valid votes yet. Please use manual override.")
                
            st.markdown("---")
            st.markdown("#### Manual Override")
            if current_scene.get("isSataScreen"):
                to_reveal = [opt_key for opt_key, opt_data in current_scene["sataOptions"].items() if st.checkbox(f"Reveal: {opt_data['label']}")]
                if st.button("Manually Commit Findings"):
                    target_dict = gs["patient_history"] if "History" in current_scene["title"] else gs["performed_exams"]
                    for key in to_reveal: target_dict[key] = current_scene["sataOptions"][key]["finding"]
                    advance_scene(current_scene["onSubmit"])
                    st.rerun()
            elif "decision" in current_scene:
                for c in current_scene["decision"]["choices"]:
                    if st.button(f"Advance to: {c['text']}"):
                        advance_scene(c["path"])
                        st.rerun()
            elif current_scene.get("onSubmit"):
                if st.button("Continue to History", type="primary"):
                    advance_scene(current_scene["onSubmit"])
                    st.rerun()

# --- TAB: ORDERS ---
elif "Orders" in st.session_state.selected_tab:
    st.markdown("<div class='content-title'>Orders</div>", unsafe_allow_html=True)
    if not gs["orders_unlocked"]: st.warning("⚠️ Orders locked.")
    elif current_scene.get("isOrderScreen"):
        if role == "Student (Voter)":
            if st.session_state.user_id not in gs["voters"]:
                selected = [t["id"] for cat, tests in current_scene["orderSets"].items() for t in tests if st.checkbox(t["label"])]
                if st.button("Submit Lab Orders", type="primary"):
                    for k in selected: gs["sata_votes"][k] = gs["sata_votes"].get(k, 0) + 1
                    gs["voters"][st.session_state.user_id] = "voted"
                    st.rerun()
            else: st.info("Orders submitted!")
        elif role == "Instructor (Host)":
            st.write(f"🗳️ **{len(gs['voters'])}** Students have voted.")
            if st.button("⚖️ Auto-Resolve Orders (>50%)"):
                confirmed = {}
                total_voters = len(gs["voters"]) or 1
                for cat, tests in current_scene["orderSets"].items():
                    for t in tests:
                        if gs["sata_votes"].get(t["id"], 0) / total_voters > 0.5: confirmed[t["id"]] = t["label"]
                if any(k in confirmed for k in ["cbc", "cmp", "renal_us"]): gs["score"] = max(0, gs["score"] - 1)
                gs["placed_orders"] = confirmed
                gs["available_results"] = {o_id: active_case["masterLabData"][o_id] for o_id in confirmed if o_id in active_case["masterLabData"]}
                advance_scene(current_scene["onSubmit"])
                st.rerun()
            
            st.markdown("---")
            st.markdown("#### Manual Override")
            confirmed_orders = {}
            for cat, tests in current_scene["orderSets"].items():
                for test in tests:
                    if st.checkbox(f"Order: {test['label']}", key=f"inst_order_{test['id']}"):
                        confirmed_orders[test["id"]] = test["label"]
            if st.button("Manually Place Orders"):
                gs["placed_orders"] = confirmed_orders
                gs["available_results"] = {o_id: active_case["masterLabData"][o_id] for o_id in confirmed_orders if o_id in active_case["masterLabData"]}
                advance_scene(current_scene["onSubmit"])
                st.rerun()
    else:
        st.write("### Active Orders:")
        for o in gs["placed_orders"].values(): st.markdown(f"- {o}")

# --- TAB: LAB RESULTS ---
elif "Lab Results" in st.session_state.selected_tab:
    gs["new_results_alert"] = False 
    st.markdown("<div class='content-title'>Lab Results</div>", unsafe_allow_html=True)
    if not gs["available_results"]: st.info("No lab results available.")
    else:
        for lab in gs["available_results"].values():
            st.subheader(lab["name"])
            st.table(pd.DataFrame(lab["results"]))

elif "Diagnostic Results" in st.session_state.selected_tab:
    st.markdown("<div class='content-title'>Diagnostic Results</div>", unsafe_allow_html=True)
    if "renal_us" in gs["available_results"]: st.write(gs["available_results"]["renal_us"]["results"][0]["value"])
    else: st.info("No imaging results available.")