from datetime import date
import pandas as pd
import streamlit as st

from db import (
    delete_review,
    get_configured_supabase_url,
    get_db_engine,
    get_latest_review,
    get_patient,
    get_patient_reviews,
    get_patients_list,
    register_patient,
    save_review,
    sync_local_to_supabase,
    update_patient,
)
from utils import (
    build_letter_templates,
    calculate_age,
    compare_medications,
    format_display_date,
    format_meds_as_bullets,
    generate_clinical_pdf,
    generate_letter_pdf,
    parse_date_to_date_obj,
)

st.set_page_config(page_title="DaVita Nephrology Clinic", layout="wide")


# --- AUTHENTICATION & ROLE LOCKOUT ---
def check_authentication() -> str | None:
    """Return 'doctor', 'nurse', or None (renders login screen)."""
    auth_cfg = st.secrets.get("auth", {})
    doc_pw = str(auth_cfg.get("doctor_password", "")).strip()
    nurse_pw = str(auth_cfg.get("nurse_password", "")).strip()
    nurse_enabled = bool(auth_cfg.get("nurse_access_enabled", True))

    # Fallback if [auth] is not yet configured in secrets.toml
    if not doc_pw and not nurse_pw:
        return "doctor"

    current_role = st.session_state.get("user_role")

    # Enforce instant lockout if nurse access was disabled mid-session
    if current_role == "nurse" and not nurse_enabled:
        st.session_state.pop("user_role", None)
        current_role = None

    if current_role in ("doctor", "nurse"):
        return current_role

    st.title("🏥 DaVita Dialysis Clinical Portal")
    st.markdown("Please enter your access password to continue.")

    with st.form("login_form"):
        entered_pw = st.text_input("Access Password", type="password")
        submitted = st.form_submit_button("Log In", type="primary")

        if submitted:
            if doc_pw and entered_pw == doc_pw:
                st.session_state["user_role"] = "doctor"
                st.rerun()
            elif nurse_pw and entered_pw == nurse_pw:
                if nurse_enabled:
                    st.session_state["user_role"] = "nurse"
                    st.rerun()
                else:
                    st.error("🚫 Nurse portal access is currently locked by the Nephrologist.")
            else:
                st.error("❌ Incorrect password.")
    return None


role = check_authentication()
if not role:
    st.stop()

is_doctor = role == "doctor"

st.title("Patient Clinical Review Dashboard")

# --- SIDEBAR: ROLE STATUS, DB CONNECTION, SYNC & PATIENT PICKER ---
with st.sidebar:
    if is_doctor:
        st.success("🩺 Logged in as: **Doctor (Full Access)**")
    else:
        st.info("👩‍⚕️ Logged in as: **Nurse (View & Print Only)**")

    if st.button("🚪 Log Out"):
        st.session_state.pop("user_role", None)
        st.rerun()

    st.divider()

    has_supabase = get_configured_supabase_url() is not None
    use_local = False
    if has_supabase and is_doctor:
        use_local = st.toggle("Use Local SQLite (Offline Rounds)", value=True)

    engine, backend_label = get_db_engine(use_local_fallback=use_local)
    if "Supabase" in backend_label:
        st.caption(f"☁️ Connected: **{backend_label}**")
    else:
        st.caption(f"💾 Connected: **{backend_label}**")

    if is_doctor and has_supabase:
        if st.button("☁️ Sync Local DB to Supabase", use_container_width=True):
            with st.spinner("Syncing local clinic.db to Supabase..."):
                ok, msg = sync_local_to_supabase()
                if ok:
                    st.success(msg)
                else:
                    st.error(msg)

    st.divider()
    st.header("Find Patient")
    patients_df = get_patients_list(engine)

    if not patients_df.empty:
        ic_to_label = {
            str(row["ic_number"]): f"{row['ic_number']} - {row['name']}"
            for _, row in patients_df.iterrows()
        }
        ic_options = [""] + list(ic_to_label.keys())
        selected_existing_ic = st.selectbox(
            "Select Existing Patient",
            options=ic_options,
            format_func=lambda x: ic_to_label.get(x, "") if x else "",
        )
    else:
        selected_existing_ic = ""
        st.info("No patients registered yet.")

    new_search_ic = ""
    if is_doctor:
        new_search_ic = st.text_input("Or Enter New I/C Number").strip()

active_ic = new_search_ic if new_search_ic else selected_existing_ic

if active_ic:
    patient_data = get_patient(engine, active_ic)

    if not patient_data.empty:
        p_row = patient_data.fillna("").iloc[0]
        patient_name = str(p_row["name"])
        patient_age = calculate_age(active_ic)
        st.markdown(
            f"<h3 style='color: #2E86C1;'>👤 Patient: {patient_name} | I/C: {active_ic} | {patient_age}</h3>",
            unsafe_allow_html=True,
        )

        # --- DEMOGRAPHICS HEADER ---
        col1, col2, col3, col4, col5 = st.columns(5)
        with col1:
            st.write("**Name**")
            st.write(p_row["name"])
        with col2:
            st.write("**HD Since**")
            st.write(p_row["hd_since"])
        with col3:
            st.write("**HD @ DaVita**")
            st.write(p_row["hd_davita"])
        with col4:
            st.write("**Sponsor**")
            st.write(p_row["sponsor"])
        with col5:
            st.write("**Follow-up Hosp**")
            st.write(p_row["follow_up_hosp"])

        if is_doctor:
            with st.expander("✏️ Edit Patient Demographics"):
                with st.form(f"edit_patient_{active_ic}"):
                    ec1, ec2, ec3 = st.columns(3)
                    with ec1:
                        edit_name = st.text_input("Full Name", value=str(p_row["name"]))
                        edit_hd_since = st.text_input("HD Since (Date)", value=str(p_row["hd_since"]))
                    with ec2:
                        edit_hd_davita = st.text_input("HD @ DaVita (Date)", value=str(p_row["hd_davita"]))
                        edit_sponsor = st.text_input("Sponsor", value=str(p_row["sponsor"]))
                    with ec3:
                        edit_hosp = st.text_input("Follow-up Hospital", value=str(p_row["follow_up_hosp"]))
                    if st.form_submit_button("Update Patient Details"):
                        if not edit_name.strip():
                            st.error("Patient Name cannot be empty.")
                        else:
                            update_patient(
                                engine,
                                active_ic,
                                {
                                    "name": edit_name.strip(),
                                    "hd_since": edit_hd_since.strip(),
                                    "hd_davita": edit_hd_davita.strip(),
                                    "sponsor": edit_sponsor.strip(),
                                    "follow_up_hosp": edit_hosp.strip(),
                                },
                            )
                            st.success("Patient demographics updated.")
                            st.rerun()

        st.divider()

        # --- FETCH REVIEWS ---
        all_patient_reviews = get_patient_reviews(engine, active_ic, ascending=False)

        review_records = []
        date_counts = {}
        if not all_patient_reviews.empty:
            filled_reviews = all_patient_reviews.fillna("")
            for _, row in filled_reviews.iterrows():
                disp = format_display_date(row["review_date"]) or "Undated"
                date_counts[disp] = date_counts.get(disp, 0) + 1
            for _, row in filled_reviews.iterrows():
                rec = row.to_dict()
                disp = format_display_date(rec["review_date"]) or "Undated"
                label = f"{disp} (# {rec['review_id']})" if date_counts[disp] > 1 else disp
                review_records.append((int(rec["review_id"]), label, rec))

        id_to_label = {rid: lbl for rid, lbl, _ in review_records}
        id_to_record = {rid: rec for rid, _, rec in review_records}

        # --- PDF & LETTER DOWNLOAD SECTION ---
        if review_records:
            st.write("**🖨️ Print Clinical Sheet**")
            col_pdf_sel, col_pdf_btn = st.columns([3, 1])
            with col_pdf_sel:
                selected_print_id = st.selectbox(
                    "Select Review Date to Print",
                    options=[rid for rid, _, _ in review_records],
                    format_func=lambda rid: id_to_label[rid],
                )
            selected_pdf_review = id_to_record[selected_print_id]
            pdf_filename, pdf_bytes = generate_clinical_pdf(patient_data, selected_pdf_review)
            with col_pdf_btn:
                st.write("")
                st.write("")
                st.download_button(
                    label=f"⬇️ Download PDF ({id_to_label[selected_print_id]})",
                    data=pdf_bytes,
                    file_name=pdf_filename,
                    mime="application/pdf",
                    type="primary",
                )

        if is_doctor:
            with st.expander("✉️ Clinical Letter Generator (DaVita Header & Signature)"):
                latest_rev_dict = review_records[0][2] if review_records else {}
                prev_rev_dict = review_records[1][2] if len(review_records) > 1 else {}

                latest_meds_str = str(latest_rev_dict.get("medications_list", ""))
                prev_meds_str = str(prev_rev_dict.get("medications_list", ""))
                added_meds, stopped_meds, _ = compare_medications(prev_meds_str, latest_meds_str)

                meds_bullets = format_meds_as_bullets(latest_meds_str)
                templates = build_letter_templates(
                    meds_bullets=meds_bullets,
                    added_meds=added_meds,
                    stopped_meds=stopped_meds,
                )

                l_col1, l_col2 = st.columns(2)
                with l_col1:
                    letter_recipient = st.text_area(
                        "Recipient (e.g., To the Pharmacist / MO)",
                        value="",
                        height=75,
                        key=f"ltr_rec_{active_ic}",
                    )
                with l_col2:
                    selected_template = st.selectbox(
                        "Select Letter Category",
                        options=list(templates.keys()),
                        key=f"ltr_tpl_{active_ic}",
                    )

                letter_body = st.text_area(
                    "Edit your letter body below:",
                    value=templates[selected_template],
                    height=320,
                    key=f"ltr_body_{active_ic}_{selected_template}",
                )

                l_fname, l_bytes = generate_letter_pdf(
                    recipient=letter_recipient,
                    p_name=patient_name,
                    p_ic=active_ic,
                    content=letter_body,
                    header_path="header.png",
                )
                st.download_button(
                    label="📄 Generate & Download Letter PDF",
                    data=l_bytes,
                    file_name=l_fname,
                    mime="application/pdf",
                    type="primary",
                    key=f"ltr_dl_{active_ic}_{selected_template}",
                )

        st.divider()

        # --- COMPREHENSIVE CLINICAL FORM (DOCTOR ONLY) ---
        if is_doctor:
            st.subheader("Clinical Review Entry")

            action_options = ["NEW"] + [rid for rid, _, _ in review_records]
            selected_action = st.selectbox(
                "Select a past review to edit, or start a new one:",
                options=action_options,
                format_func=lambda opt: "📝 Create New Review (Today)" if opt == "NEW" else f"✏️ Edit {id_to_label[opt]}",
            )

            prev = {}
            target_id = None
            reference_review = None

            if selected_action == "NEW":
                latest_review = get_latest_review(engine, active_ic)
                if not latest_review.empty:
                    reference_review = latest_review.fillna("").iloc[0].to_dict()
                    prev = dict(reference_review)
                    for field in [
                        "bp_pre_post",
                        "idwg",
                        "uf",
                        "venous_pressure",
                        "kt_v",
                        "urr",
                        "hb",
                        "ferritin",
                        "tsats",
                        "c_ca",
                        "phosphate",
                        "alp",
                        "ipth",
                        "predialysis_cr",
                        "albumin",
                        "chol",
                        "alt_ggt",
                        "fbs_hba1c",
                        "highlights",
                    ]:
                        prev[field] = ""
                default_review_date = date.today()
            else:
                target_id = int(selected_action)
                prev = dict(id_to_record[target_id])
                parsed = parse_date_to_date_obj(prev.get("review_date", ""))
                default_review_date = parsed if parsed else date.today()

                # Find the review immediately preceding the one being edited for comparison
                ordered_ids = [rid for rid, _, _ in review_records]
                curr_idx = ordered_ids.index(target_id)
                if curr_idx + 1 < len(ordered_ids):
                    reference_review = id_to_record[ordered_ids[curr_idx + 1]]

                col_del1, col_del2 = st.columns([3, 2])
                with col_del2:
                    confirm_delete = st.checkbox("Confirm delete this review", key=f"confirm_del_{target_id}")
                    if st.button("🗑️ Delete this Record", type="primary", disabled=not confirm_delete):
                        delete_review(engine, target_id)
                        st.success("Review deleted.")
                        st.rerun()

            form_key = f"review_form_{active_ic}_{selected_action}"
            with st.form(form_key):
                review_date_picker = st.date_input("Review Date", value=default_review_date, format="DD/MM/YYYY")

                st.markdown("<h4 style='color: #D35400;'>🚨 Reminders / Agenda for Clinic Day</h4>", unsafe_allow_html=True)
                highlights = st.text_area(
                    "Issues to discuss with patient (Leave blank if none)",
                    value=str(prev.get("highlights", "")),
                    height=80,
                )
                st.divider()

                st.markdown("<h4 style='color: #2E86C1;'>1. History & Access 🏥</h4>", unsafe_allow_html=True)
                col_h1, col_h2 = st.columns(2)
                with col_h1:
                    active_diagnoses = st.text_area("Active Diagnoses (List all)", value=str(prev.get("active_diagnoses", "")))
                    admission_hx = st.text_area("Recent Admissions (Include Dates)", value=str(prev.get("admission_hx", "")))
                with col_h2:
                    transplant_status = st.text_input("Transplant Status", value=str(prev.get("transplant_status", "")))
                    access_details = st.text_area("Access History (Types & Dates)", value=str(prev.get("access_details", "")))

                st.markdown("<h4 style='color: #E74C3C;'>2. Dialysis Prescription 🩸</h4>", unsafe_allow_html=True)
                col_p1, col_p2, col_p3, col_p4 = st.columns(4)
                with col_p1:
                    blood_pump = st.text_input("Blood Pump", value=str(prev.get("blood_pump", "")))
                    dialysate_flow = st.text_input("Dialysate Flow", value=str(prev.get("dialysate_flow", "")))
                    frequency = st.text_input("Frequency (e.g., 3x/wk)", value=str(prev.get("frequency", "")))
                    duration = st.text_input("Duration", value=str(prev.get("duration", "")))
                with col_p2:
                    dialyzer = st.text_input("Dialyzer", value=str(prev.get("dialyzer", "")))
                    hep_loading = st.text_input("Heparin Loading", value=str(prev.get("hep_loading", "")))
                    hep_maintenance = st.text_input("Heparin Maintenance", value=str(prev.get("hep_maintenance", "")))
                    dialysate_ca = st.text_input("Dialysate Ca", value=str(prev.get("dialysate_ca", "")))
                with col_p3:
                    dry_weight = st.text_input("Dry Weight (kg)", value=str(prev.get("dry_weight", "")))
                    idwg = st.text_input("IDWG (kg)", value=str(prev.get("idwg", "")))
                    uf = st.text_input("UF (L)", value=str(prev.get("uf", "")))
                    bp_pre_post = st.text_input("BP Pre/Post HD", value=str(prev.get("bp_pre_post", "")))
                with col_p4:
                    venous_pressure = st.text_input("Venous Pressure", value=str(prev.get("venous_pressure", "")))
                    kt_v = st.text_input("Kt/V", value=str(prev.get("kt_v", "")))
                    urr = st.text_input("URR (%)", value=str(prev.get("urr", "")))

                st.markdown("<h4 style='color: #27AE60;'>3. Anaemia & CKD-MBD 🧪</h4>", unsafe_allow_html=True)
                col_l1, col_l2, col_l3, col_l4 = st.columns(4)
                with col_l1:
                    hb = st.text_input("Hb", value=str(prev.get("hb", "")))
                    ferritin = st.text_input("Ferritin", value=str(prev.get("ferritin", "")))
                with col_l2:
                    tsats = st.text_input("TSATs (%)", value=str(prev.get("tsats", "")))
                    iv_erysaa = st.text_input("IV Erysaa", value=str(prev.get("iv_erysaa", "")))
                with col_l3:
                    c_ca = st.text_input("C.ca", value=str(prev.get("c_ca", "")))
                    phosphate = st.text_input("Phosphate", value=str(prev.get("phosphate", "")))
                with col_l4:
                    alp = st.text_input("ALP", value=str(prev.get("alp", "")))
                    ipth = st.text_input("iPTH", value=str(prev.get("ipth", "")))
                parathyroidectomy = st.text_input(
                    "Parathyroidectomy",
                    value=str(prev.get("parathyroidectomy", "")) or "Nil",
                )

                st.markdown("<h4 style='color: #F39C12;'>4. Nutrition & Infection 🍎</h4>", unsafe_allow_html=True)
                col_n1, col_n2, col_n3 = st.columns(3)
                with col_n1:
                    predialysis_cr = st.text_input("Predialysis Cr", value=str(prev.get("predialysis_cr", "")))
                    albumin = st.text_input("Albumin", value=str(prev.get("albumin", "")))
                with col_n2:
                    chol = st.text_input("Cholesterol", value=str(prev.get("chol", "")))
                    anti_hbs = st.text_input("Anti HBS", value=str(prev.get("anti_hbs", "")))
                with col_n3:
                    alt_ggt = st.text_input("ALT/GGT", value=str(prev.get("alt_ggt", "")))
                    fbs_hba1c = st.text_input("FBS / HbA1C", value=str(prev.get("fbs_hba1c", "")))

                st.markdown("<h4 style='color: #8E44AD;'>5. Medication Review & Management Plan 📋</h4>", unsafe_allow_html=True)

                # --- SIDE-BY-SIDE PREVIOUS VISIT REFERENCE BANNER ---
                if reference_review:
                    ref_date_disp = format_display_date(reference_review.get("review_date", ""))
                    with st.container(border=True):
                        st.markdown(f"**🔎 Reference from Previous Visit ({ref_date_disp}) — Check & Reconcile Below:**")
                        ref_c1, ref_c2 = st.columns(2)
                        with ref_c1:
                            st.caption("Previous Medications List")
                            prev_med_items = [
                                m.strip()
                                for m in str(reference_review.get("medications_list", "")).replace("\n", ";").split(";")
                                if m.strip()
                            ]
                            if prev_med_items:
                                st.markdown("\n".join(f"- {m}" for m in prev_med_items))
                            else:
                                st.write("_None recorded_")
                        with ref_c2:
                            st.caption("Previous Clinical Notes & Plan (Check for pending med changes)")
                            st.info(str(reference_review.get("clinical_notes", "")) or "_No previous plan recorded_")

                med_col, plan_col = st.columns(2)
                with med_col:
                    medications_list = st.text_area(
                        "Updated Active Medications (Separate with ; )",
                        value=str(prev.get("medications_list", "")),
                        height=140,
                    )
                with plan_col:
                    clinical_notes = st.text_area(
                        "Today's Clinical Notes & Plan",
                        value=str(prev.get("clinical_notes", "")),
                        height=140,
                    )

                submit_review = st.form_submit_button("Save Clinical Review", type="primary")

                if submit_review:
                    stored_date_str = review_date_picker.strftime("%Y-%m-%d")
                    form_payload = {
                        "review_date": stored_date_str,
                        "active_diagnoses": active_diagnoses,
                        "admission_hx": admission_hx,
                        "transplant_status": transplant_status,
                        "access_details": access_details,
                        "blood_pump": blood_pump,
                        "dialysate_flow": dialysate_flow,
                        "frequency": frequency,
                        "duration": duration,
                        "dialyzer": dialyzer,
                        "hep_loading": hep_loading,
                        "hep_maintenance": hep_maintenance,
                        "dry_weight": dry_weight,
                        "dialysate_ca": dialysate_ca,
                        "bp_pre_post": bp_pre_post,
                        "idwg": idwg,
                        "uf": uf,
                        "venous_pressure": venous_pressure,
                        "kt_v": kt_v,
                        "urr": urr,
                        "hb": hb,
                        "ferritin": ferritin,
                        "tsats": tsats,
                        "iv_erysaa": iv_erysaa,
                        "c_ca": c_ca,
                        "phosphate": phosphate,
                        "alp": alp,
                        "ipth": ipth,
                        "parathyroidectomy": parathyroidectomy,
                        "predialysis_cr": predialysis_cr,
                        "albumin": albumin,
                        "chol": chol,
                        "anti_hbs": anti_hbs,
                        "alt_ggt": alt_ggt,
                        "fbs_hba1c": fbs_hba1c,
                        "highlights": highlights,
                        "medications_list": medications_list,
                        "clinical_notes": clinical_notes,
                    }
                    action_result, _ = save_review(engine, active_ic, form_payload, target_review_id=target_id)
                    st.success(f"Review for {format_display_date(stored_date_str)} {action_result} successfully!")
                    st.rerun()

            st.divider()

        # --- DISPLAY HISTORY & MEDICATION RECONCILIATION LOG (DOCTOR & NURSE) ---
        st.subheader("Patient History, Medication Changes & Trends")

        history_data = get_patient_reviews(engine, active_ic, ascending=True)

        if not history_data.empty:
            tab1, tab2, tab3 = st.tabs(["💊 Medication Timeline & Notes", "📊 Lab Trends", "🗄️ Full Data Table"])

            with tab1:
                st.markdown("**Chronological Reviews & Automatic Medication Changes**")
                history_desc = get_patient_reviews(engine, active_ic, ascending=False).fillna("")
                rows_list = [row.to_dict() for _, row in history_desc.iterrows()]

                for idx, row in enumerate(rows_list):
                    disp_d = format_display_date(row["review_date"])
                    prior_row = rows_list[idx + 1] if idx + 1 < len(rows_list) else None

                    with st.expander(
                        f"📅 {disp_d} | Dry Wt: {row['dry_weight']} | BP: {row['bp_pre_post']} | UF: {row['uf']}",
                        expanded=(idx == 0),
                    ):
                        if prior_row:
                            added, stopped, _ = compare_medications(
                                prior_row.get("medications_list", ""),
                                row.get("medications_list", ""),
                            )
                            if added or stopped:
                                diff_parts = []
                                if added:
                                    diff_parts.append(f"**➕ Added/Updated:** {', '.join(added)}")
                                if stopped:
                                    diff_parts.append(f"**➖ Stopped/Changed:** {', '.join(stopped)}")
                                st.warning(" | ".join(diff_parts))

                        col_a, col_b = st.columns(2)
                        with col_a:
                            if row.get("highlights"):
                                st.write(f"**Agenda/Reminders:** {row['highlights']}")
                            st.write(f"**Notes & Plan:** {row['clinical_notes']}")
                            st.write(f"**Active Medications:** {row['medications_list']}")
                        with col_b:
                            st.write(f"**Anaemia:** Hb {row['hb']} | Ferritin {row['ferritin']} | TSAT {row['tsats']}%")
                            st.write(f"**Bone:** C.ca {row['c_ca']} | PO4 {row['phosphate']} | iPTH {row['ipth']}")
                            st.write(f"**Adequacy:** Kt/V {row['kt_v']} | URR {row['urr']}%")

            with tab2:
                st.markdown("**Hemoglobin (Hb) & Kt/V Trends**")
                chart_data = history_data[["review_date", "hb", "kt_v"]].copy()
                chart_data["display_date"] = chart_data["review_date"].apply(format_display_date)
                chart_data["hb"] = pd.to_numeric(chart_data["hb"], errors="coerce")
                chart_data["kt_v"] = pd.to_numeric(chart_data["kt_v"], errors="coerce")
                chart_data = chart_data.set_index("display_date")[["hb", "kt_v"]].dropna(how="all")
                if not chart_data.empty:
                    st.line_chart(chart_data)
                else:
                    st.info("No numeric Hb or Kt/V values recorded yet.")

            with tab3:
                st.markdown("**Complete Raw Data**")
                history_desc = get_patient_reviews(engine, active_ic, ascending=False)
                display_data = history_desc.drop(columns=["review_id", "ic_number"], errors="ignore")
                st.dataframe(display_data, use_container_width=True)
        else:
            st.info("No past reviews found for this patient.")

    else:
        if is_doctor:
            st.warning("Patient not found. Register a new patient below.")
            with st.form("register_patient"):
                new_ic = st.text_input("I/C Number", value=active_ic)
                new_name = st.text_input("Full Name")
                new_hd = st.text_input("HD Since (Date)")
                new_davita = st.text_input("HD @ DaVita (Date)")
                new_sponsor = st.text_input("Sponsor")
                new_hosp = st.text_input("Follow-up Hospital")

                submit_new = st.form_submit_button("Register Patient", type="primary")

                if submit_new:
                    clean_new_ic = new_ic.strip()
                    clean_new_name = new_name.strip()
                    if not clean_new_ic or not clean_new_name:
                        st.error("Both I/C Number and Full Name are required.")
                    elif not get_patient(engine, clean_new_ic).empty:
                        st.error("A patient with this I/C Number already exists.")
                    else:
                        register_patient(
                            engine,
                            {
                                "ic_number": clean_new_ic,
                                "name": clean_new_name,
                                "hd_since": new_hd.strip(),
                                "hd_davita": new_davita.strip(),
                                "sponsor": new_sponsor.strip(),
                                "follow_up_hosp": new_hosp.strip(),
                            },
                        )
                        st.success("Patient registered! Clear the 'Or Enter New I/C Number' box and select them from the dropdown.")
        else:
            st.warning("Patient not found.")
else:
    st.info("👈 Select a patient from the list to begin.")
    
    # --- MANUAL LETTER GENERATOR FOR UNREGISTERED PATIENTS (DOCTOR ONLY) ---
    if is_doctor:
        with st.expander("✉️ Manual Clinical Letter Generator (Unregistered Patient)"):
            m_col1, m_col2 = st.columns(2)
            with m_col1:
                m_recipient = st.text_area("Recipient (e.g., To the Pharmacist / MO)", height=75, key="m_ltr_rec")
            with m_col2:
                m_name = st.text_input("Patient Name", key="m_ltr_name")
                m_ic = st.text_input("Patient I/C Number", key="m_ltr_ic")

            m_templates = build_letter_templates()
            m_tpl = st.selectbox("Select Letter Category", list(m_templates.keys()), key="m_ltr_tpl")
            m_body = st.text_area("Letter Body", value=m_templates[m_tpl], height=300, key=f"m_ltr_body_{m_tpl}")

            m_fname, m_bytes = generate_letter_pdf(
                recipient=m_recipient,
                p_name=m_name,
                p_ic=m_ic,
                content=m_body,
                header_path="header.png",
            )
            st.download_button(
                label="📄 Generate & Download Manual Letter PDF",
                data=m_bytes,
                file_name=m_fname,
                mime="application/pdf",
                type="primary",
                key="m_ltr_dl",
            )