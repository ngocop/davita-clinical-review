import inspect
import os
from datetime import date, datetime
from fpdf import FPDF


def clean_for_pdf(text) -> str:
    """Normalize common Unicode punctuation/symbols into latin-1 safe text for FPDF."""
    if text is None:
        return ""
    s = str(text)
    if s.lower() == "nan":
        return ""
    replacements = {
        "\u201c": '"',
        "\u201d": '"',
        "\u2018": "'",
        "\u2019": "'",
        "\u2013": "-",
        "\u2014": "-",
        "\u2022": "-",
        "\u2265": ">=",
        "\u2264": "<=",
        "\u00b0": " deg",
        "\u00b5": "u",
        "\u03bc": "u",
        "\u2192": "->",
        "\u2190": "<-",
        "\u00a0": " ",
    }
    for bad, good in replacements.items():
        s = s.replace(bad, good)
    return s.encode("latin-1", "replace").decode("latin-1")


def pdf_to_bytes(pdf: FPDF) -> bytes:
    """Return PDF content as bytes without writing files to disk."""
    sig = inspect.signature(pdf.output)
    name_param = sig.parameters.get("name")
    if "dest" in sig.parameters and name_param is not None and name_param.default == "":
        raw = pdf.output("", "S")
    else:
        raw = pdf.output()

    if isinstance(raw, str):
        return raw.encode("latin-1")
    return bytes(raw)


def calculate_age(id_string: str) -> str:
    """Calculate age from a 12-digit Malaysian IC number, or return MRN fallback."""
    clean_id = str(id_string).replace(" ", "").replace("-", "")
    if len(clean_id) == 12 and clean_id.isdigit():
        yy = int(clean_id[:2])
        mm = int(clean_id[2:4])
        dd = int(clean_id[4:6])
        today = date.today()
        current_yy = today.year % 100
        birth_year = 1900 + yy if yy > current_yy else 2000 + yy
        age = today.year - birth_year
        try:
            dob = date(birth_year, mm, dd)
            if (today.month, today.day) < (dob.month, dob.day):
                age -= 1
        except ValueError:
            pass
        return f"{age} years old"
    return "Age N/A (MRN)"


def parse_date_to_date_obj(value) -> date | None:
    """Parse stored review_date strings (ISO or legacy formats) into a date object."""
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if not value:
        return None

    s = str(value).strip()
    s_norm = s.replace("Oktober", "October").replace("Okt", "Oct")
    for fmt in (
        "%Y-%m-%d",
        "%d-%b-%Y",
        "%d-%B-%Y",
        "%d-%m-%Y",
        "%d-%m-%y",
        "%d %B %Y",
        "%d %b %Y",
        "%d/%m/%Y",
    ):
        try:
            return datetime.strptime(s_norm, fmt).date()
        except ValueError:
            continue
    return None


def format_display_date(value) -> str:
    """Format a stored review_date consistently for display."""
    parsed = parse_date_to_date_obj(value)
    if parsed:
        return parsed.strftime("%d-%b-%Y")
    return str(value) if value else ""


def _safe_field(mapping: dict, key: str, default: str = "") -> str:
    val = mapping.get(key, default)
    if val is None:
        return default
    s = str(val)
    return default if s.lower() == "nan" else s


def generate_clinical_pdf(patient_df, review: dict) -> tuple[str, bytes]:
    """Build the 2-column clinical review sheet PDF in memory and return (filename, bytes)."""
    p_row = patient_df.iloc[0].to_dict() if hasattr(patient_df, "iloc") else dict(patient_df)
    r = {k: _safe_field(review, k) for k in review}

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    # --- Top Section ---
    pdf.set_font("Arial", style="B", size=10)
    pdf.cell(20, 5, txt="Name", ln=0)
    pdf.set_font("Arial", size=10)
    pdf.cell(90, 5, txt=clean_for_pdf(p_row.get("name", "")), ln=0)

    pdf.set_font("Arial", style="B", size=10)
    pdf.cell(30, 5, txt="Date", ln=0)
    pdf.set_font("Arial", size=10)
    display_date = format_display_date(r.get("review_date", ""))
    pdf.cell(50, 5, txt=clean_for_pdf(display_date), ln=1)

    pdf.set_font("Arial", style="B", size=10)
    pdf.cell(20, 5, txt="I/C", ln=0)
    pdf.set_font("Arial", size=10)
    pdf.cell(90, 5, txt=clean_for_pdf(p_row.get("ic_number", "")), ln=0)

    pdf.set_font("Arial", style="B", size=10)
    pdf.cell(30, 5, txt="HD since", ln=0)
    pdf.set_font("Arial", size=10)
    pdf.cell(50, 5, txt=clean_for_pdf(p_row.get("hd_since", "")), ln=1)

    pdf.set_x(120)
    pdf.set_font("Arial", style="B", size=10)
    pdf.cell(30, 5, txt="HD @ DaVita", ln=0)
    pdf.set_font("Arial", size=10)
    pdf.cell(50, 5, txt=clean_for_pdf(p_row.get("hd_davita", "")), ln=1)

    pdf.set_x(120)
    pdf.set_font("Arial", style="B", size=10)
    pdf.cell(30, 5, txt="Sponsor", ln=0)
    pdf.set_font("Arial", size=10)
    pdf.cell(50, 5, txt=clean_for_pdf(p_row.get("sponsor", "")), ln=1)

    pdf.set_x(120)
    pdf.set_font("Arial", style="B", size=10)
    pdf.cell(30, 5, txt="Follow up Hosp", ln=0)
    pdf.set_font("Arial", size=10)
    pdf.cell(50, 5, txt=clean_for_pdf(p_row.get("follow_up_hosp", "")), ln=1)

    right_header_bottom = pdf.get_y()

    pdf.set_y(30)
    pdf.set_font("Arial", size=10)
    pdf.multi_cell(100, 5, txt=clean_for_pdf(r.get("active_diagnoses", "")).replace(";", "\n"))
    left_header_bottom = pdf.get_y()

    start_y = max(right_header_bottom, left_header_bottom) + 5
    start_page = pdf.page_no()

    # ========== LEFT COLUMN ==========
    pdf.set_y(start_y)

    def draw_left_row(label: str, val: str):
        pdf.set_font("Arial", style="B", size=9)
        pdf.cell(50, 5, txt=clean_for_pdf(label), ln=0)
        pdf.set_font("Arial", size=9)
        pdf.cell(50, 5, txt=clean_for_pdf(val), ln=1)

    pdf.set_font("Arial", style="BU", size=10)
    pdf.cell(100, 6, txt="Dialysis Prescription", ln=1)
    draw_left_row("Blood pump", r.get("blood_pump", ""))
    draw_left_row("Dialysate flow", r.get("dialysate_flow", ""))
    draw_left_row("Frequency", r.get("frequency", ""))
    draw_left_row("Duration", r.get("duration", ""))
    draw_left_row("Dialyzer", r.get("dialyzer", ""))

    hep_load = r.get("hep_loading", "")
    hep_maint = r.get("hep_maintenance", "")
    hep_combined = f"{hep_load} / {hep_maint}".strip(" /") if (hep_load or hep_maint) else ""
    draw_left_row("Hep loading/maintenance", hep_combined)
    draw_left_row("Dry weight", r.get("dry_weight", ""))
    draw_left_row("Dialysate Ca", r.get("dialysate_ca", ""))
    draw_left_row("BP pre/post HD", r.get("bp_pre_post", ""))
    draw_left_row("IDWG", r.get("idwg", ""))
    draw_left_row("UF", r.get("uf", ""))
    draw_left_row("Venous pressure", r.get("venous_pressure", ""))

    pdf.ln(4)
    pdf.set_font("Arial", style="BU", size=10)
    pdf.cell(100, 6, txt="Anaemia Management", ln=1)
    draw_left_row("Hb", r.get("hb", ""))
    draw_left_row("Ferritin", r.get("ferritin", ""))
    draw_left_row("TSATs", r.get("tsats", ""))
    draw_left_row("IV Erysaa", r.get("iv_erysaa", ""))

    pdf.ln(4)
    pdf.set_font("Arial", style="BU", size=10)
    pdf.cell(100, 6, txt="CKD-MBD Management", ln=1)
    draw_left_row("C.ca", r.get("c_ca", ""))
    draw_left_row("Phosphate", r.get("phosphate", ""))
    draw_left_row("ALP", r.get("alp", ""))
    draw_left_row("iPTH (pmol/L)", r.get("ipth", ""))
    draw_left_row("Parathyroidectomy", r.get("parathyroidectomy", "") or "Nil")

    pdf.ln(4)
    pdf.set_font("Arial", style="BU", size=10)
    pdf.cell(100, 6, txt="Nutritional Status", ln=1)
    draw_left_row("Predialysis Cr", r.get("predialysis_cr", ""))
    draw_left_row("Albumin", r.get("albumin", ""))
    draw_left_row("Chol", r.get("chol", ""))

    pdf.ln(4)
    pdf.set_font("Arial", style="BU", size=10)
    pdf.cell(100, 6, txt="Infection Surveillance", ln=1)
    draw_left_row("Anti HBS", r.get("anti_hbs", ""))
    draw_left_row("ALT/GGT", r.get("alt_ggt", ""))
    pdf.cell(100, 2, txt="", ln=1)
    draw_left_row("FBS / HbA1C", r.get("fbs_hba1c", ""))

    left_end_page = pdf.page_no()
    left_end_y = pdf.get_y()

    # ========== RIGHT COLUMN ==========
    if pdf.page_no() != start_page:
        pdf.page = start_page
    pdf.set_y(start_y)

    def set_right():
        pdf.set_x(110)

    def draw_right_block(title: str, content: str):
        set_right()
        pdf.set_font("Arial", style="BU", size=10)
        pdf.cell(90, 5, txt=clean_for_pdf(title), ln=1)
        set_right()
        pdf.set_font("Arial", size=9)
        pdf.multi_cell(90, 5, txt=clean_for_pdf(content).replace(";", "\n"))
        pdf.ln(3)

    draw_right_block("Admission hx", r.get("admission_hx", ""))
    draw_right_block("Transplant", r.get("transplant_status", ""))
    draw_right_block("Access", r.get("access_details", ""))

    set_right()
    pdf.set_font("Arial", style="BU", size=10)
    pdf.cell(90, 5, txt="Adequacy", ln=1)
    set_right()
    pdf.set_font("Arial", style="B", size=9)
    pdf.cell(40, 5, txt="Kt/V", ln=0)
    pdf.set_font("Arial", size=9)
    pdf.cell(50, 5, txt=clean_for_pdf(r.get("kt_v", "")), ln=1)
    set_right()
    pdf.set_font("Arial", style="B", size=9)
    pdf.cell(40, 5, txt="URR", ln=0)
    pdf.set_font("Arial", size=9)
    pdf.cell(50, 5, txt=clean_for_pdf(r.get("urr", "")), ln=1)
    pdf.ln(3)

    draw_right_block("Medications", r.get("medications_list", ""))
    draw_right_block("Clinical notes", r.get("clinical_notes", ""))

    right_end_page = pdf.page_no()
    right_end_y = pdf.get_y()

    # ========== SIGNATURE BLOCK ==========
    if left_end_page > right_end_page:
        pdf.page = left_end_page
        sig_y = left_end_y
    elif right_end_page > left_end_page:
        sig_y = right_end_y
    else:
        sig_y = max(left_end_y, right_end_y)

    if sig_y > 245:
        pdf.add_page()
        sig_y = 20

    pdf.set_y(sig_y + 12)
    pdf.set_x(120)
    pdf.set_font("Arial", style="B", size=9)
    pdf.cell(70, 5, txt="Visiting Nephrologist", ln=1)
    pdf.set_x(120)
    pdf.cell(70, 15, txt="_______________________", ln=1)

    safe_ic = clean_for_pdf(p_row.get("ic_number", "Patient")).replace("/", "-").replace(" ", "")
    safe_date = clean_for_pdf(display_date).replace("/", "-")
    filename = f"Review_{safe_ic}_{safe_date}.pdf"
    return filename, pdf_to_bytes(pdf)


def generate_letter_pdf(
    recipient: str,
    p_name: str,
    p_ic: str,
    content: str,
    header_path: str = "header.png",
) -> tuple[str, bytes]:
    """Generate a clinical letter PDF in memory and return (filename, bytes)."""
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    if os.path.exists(header_path):
        pdf.image(header_path, x=10, y=10, w=45)
        pdf.ln(30)
    else:
        pdf.set_font("Arial", style="I", size=10)
        pdf.cell(0, 10, txt="[Header image 'header.png' not found]", ln=1)
        pdf.ln(10)

    pdf.set_font("Arial", size=11)
    current_date = datetime.now().strftime("%d %B %Y")
    pdf.cell(0, 6, txt=f"Date: {current_date}", ln=1)
    pdf.ln(4)

    if recipient:
        pdf.set_font("Arial", size=11)
        pdf.multi_cell(0, 6, txt=clean_for_pdf(recipient))
        pdf.ln(4)

    if p_name or p_ic:
        pdf.set_font("Arial", style="B", size=11)
        re_text = "Re: "
        if p_name:
            re_text += clean_for_pdf(p_name)
        if p_ic:
            re_text += f"  (I/C: {clean_for_pdf(p_ic)})"
        pdf.cell(0, 6, txt=re_text, ln=1)
        pdf.line(10, pdf.get_y(), 200, pdf.get_y())
        pdf.ln(6)

    pdf.set_font("Arial", size=11)
    pdf.multi_cell(0, 6, txt=clean_for_pdf(content))

    safe_ic = clean_for_pdf(p_ic).replace("-", "").replace("/", "").replace(" ", "") if p_ic else "NoIC"
    filename = f"Letter_{safe_ic}_{datetime.now().strftime('%Y%m%d')}.pdf"
    return filename, pdf_to_bytes(pdf)


def compare_medications(prev_meds_str: str, curr_meds_str: str) -> tuple[list[str], list[str], list[str]]:
    """
    Split semicolon/newline-separated medication lists and return:
    (added_or_modified, stopped_or_changed_from, unchanged).
    """
    def _parse(raw: str) -> list[str]:
        if not raw or str(raw).lower() == "nan":
            return []
        normalized = str(raw).replace("\n", ";")
        return [item.strip() for item in normalized.split(";") if item.strip()]

    prev_list = _parse(prev_meds_str)
    curr_list = _parse(curr_meds_str)

    prev_map = {m.lower(): m for m in prev_list}
    curr_map = {m.lower(): m for m in curr_list}

    added = [curr_map[k] for k in curr_map if k not in prev_map]
    stopped = [prev_map[k] for k in prev_map if k not in curr_map]
    unchanged = [curr_map[k] for k in curr_map if k in prev_map]
    return added, stopped, unchanged


def format_meds_as_bullets(meds_raw: str) -> str:
    """Format a semicolon-separated string of medications into a bulleted list string."""
    if not meds_raw or not str(meds_raw).strip() or str(meds_raw).lower() == "nan":
        return "- \n- "
    lines = [line.strip() for line in str(meds_raw).replace(";", "\n").splitlines() if line.strip()]
    return "\n".join(line if line.startswith("-") else f"- {line}" for line in lines)


def build_letter_templates(
    meds_bullets: str = "- \n- ",
    added_meds: list[str] | None = None,
    stopped_meds: list[str] | None = None,
) -> dict[str, str]:
    """Return dictionary of letter templates with dynamic medication content."""
    adjustments_lines = []
    for item in added_meds or []:
        adjustments_lines.append(f"- {item}: Started / Adjusted")
    for item in stopped_meds or []:
        adjustments_lines.append(f"- {item}: Stopped / Changed")

    adjustments_block = (
        "\n".join(adjustments_lines)
        if adjustments_lines
        else "- [Medication A]: Adjusted to [Dose]\n- [Medication B]: Stopped"
    )

    return {
        "Medication Adjustment": f"""Dear Colleague,

This letter is to inform you of recent adjustments to the patient's medication that we have done during the latest review.

Recent adjustments made:
{adjustments_block}

Current / updated medication list:
{meds_bullets}

Kindly provide the patient with the new medication based on this list. Please do not hesitate to contact us if further clarification is required.

Yours sincerely,


Dr Muhammad Yusuf bin Abu Shamsi
Consultant Nephrologist
""",
        "Providing Medications": f"""Dear MO/FMS colleague,

This letter serves to confirm the current list of nephrology medications required for ongoing continuation of care.

Current medications provided:
{meds_bullets}

Please facilitate the dispensing and continuation of these medications as part of the standard management plan.

Yours sincerely,


Dr Muhammad Yusuf bin Abu Shamsi
Consultant Nephrologist
""",
        "General Referral / Other": """To whom it may concern,

[Insert general clinical notes or referral details here]

Yours sincerely,


Dr Muhammad Yusuf bin Abu Shamsi
Consultant Nephrologist
""",
    }