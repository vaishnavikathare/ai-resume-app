import streamlit as st
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime
import os
import json
import google.generativeai as genai
import re
import fitz
import docx
import io
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from reportlab.lib.units import inch
import tempfile
from textwrap import wrap

# Google Sheets setup
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
creds = ServiceAccountCredentials.from_json_keyfile_name("service_account.json", scope)
client = gspread.authorize(creds)
sheet = client.open("AI Resume Users").sheet1  # Signup sheet
gc = gspread.service_account(filename='credentials.json')
sh = gc.open("AI Resume Analyzer Data")
worksheet = sh.sheet1  # Resume analysis sheet

# Gemini setup
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

# --- SIGNUP / LOGIN ---
if "logged_in" not in st.session_state:
    st.session_state.logged_in = False

if not st.session_state.logged_in:
    st.title("Signup / Login")

    option = st.radio("Choose Action", ["Sign Up", "Log In"])

    if option == "Sign Up":
        name = st.text_input("Full Name")
        email = st.text_input("Email")
        password = st.text_input("Password", type="password")

        if st.button("Sign Up"):
            all_emails = [row[1] for row in sheet.get_all_values()[1:]]  # Skip header
            if email in all_emails:
                st.error("❌ Email already registered. Please log in instead.")
            else:
                today = datetime.today().strftime('%Y-%m-%d')
                sheet.append_row([name, email, password, today])
                st.success("✅ You have signed up successfully! Please log in.")

    elif option == "Log In":
        email = st.text_input("Email")
        password = st.text_input("Password", type="password")

        if st.button("Log In"):
            users = sheet.get_all_records()
            for row in users:
                if row["Email"] == email and row["Password"] == password:
                    st.session_state.logged_in = True
                    st.session_state.user_name = row["Full Name"]
                    st.session_state.user_email = row["Email"]
                    st.success(f"Welcome, {row['Full Name']}!")
                    st.rerun()
            else:
                st.error("❌ Invalid email or password.")

else:
    st.sidebar.write(f"👋 Hello, {st.session_state.user_name}")
    if st.sidebar.button("Log Out"):
        st.session_state.clear()
        st.rerun()

    st.title("🧠 AI Resume Analyzer with Gemini")

    def extract_text_from_pdf(uploaded_file):
        pdf = fitz.open(stream=uploaded_file.read(), filetype="pdf")
        text = ""
        for page in pdf:
            text += page.get_text()
        return text

    def extract_text_from_docx(uploaded_file):
        doc = docx.Document(io.BytesIO(uploaded_file.read()))
        return "\n".join([para.text for para in doc.paragraphs])

    def create_pdf_report(score, missing_skills, suggestions):
        tmp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
        c = canvas.Canvas(tmp_file.name, pagesize=letter)
        width, height = letter
        margin = 1 * inch
        y = height - margin

        c.setFont("Helvetica-Bold", 16)
        c.drawString(margin, y, "AI Resume Analysis Report")
        y -= 40

        c.setFont("Helvetica", 12)
        c.drawString(margin, y, f"Job Fit Score: {score}%")
        y -= 30

        c.drawString(margin, y, "Missing Skills:")
        y -= 20

        text_obj = c.beginText(margin + 20, y)
        text_obj.setFont("Helvetica", 12)
        for skill in missing_skills:
            text_obj.textLine(f"- {skill}")
        c.drawText(text_obj)

        y = text_obj.getY() - 20
        c.drawString(margin, y, "Suggestions:")
        y -= 20

        text_obj = c.beginText(margin + 20, y)
        text_obj.setFont("Helvetica", 12)
        for line in wrap(suggestions, 80):
            text_obj.textLine(line)
        c.drawText(text_obj)

        c.showPage()
        c.save()
        return tmp_file.name

    uploaded_file = st.file_uploader("Upload your Resume (PDF or DOCX)", type=["pdf", "docx"])
    if uploaded_file is not None:
        if uploaded_file.type == "application/pdf":
            resume_text = extract_text_from_pdf(uploaded_file)
        elif uploaded_file.type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
            resume_text = extract_text_from_docx(uploaded_file)
        else:
            st.error("Unsupported file type")
            resume_text = ""
    else:
        resume_text = ""

    col1, col2 = st.columns(2)
    with col1:
        resume_text = st.text_area("Resume Text here", value=resume_text, height=300)
    with col2:
        job_description = st.text_area("Job Description here", height=300)

    if st.button("Analyze and Save"):
        if not resume_text or not job_description:
            st.error("Please enter both Resume Text and Job Description.")
        else:
            with st.spinner("Analyzing resume with Gemini AI..."):
                prompt = f"""
You are an expert career coach and recruiter.

Carefully compare the RESUME with the JOB DESCRIPTION. Provide detailed and specific analysis.

Output ONLY in JSON format:
{{
  "job_fit_score": <integer 0-100>,
  "missing_skills": [<list of missing skills>],
  "suggestions": "<clear, actionable advice>"
}}

RESUME:
{resume_text}

JOB DESCRIPTION:
{job_description}
"""
                try:
                    model = genai.GenerativeModel("gemini-1.5-flash")
                    response = model.generate_content(prompt)
                    ai_output = response.text.strip()

                    json_match = re.search(r"\{[\s\S]*\}", ai_output)
                    if json_match:
                        ai_output = json_match.group(0)

                    result = json.loads(ai_output)
                    st.metric("Job Fit Score", f"{result['job_fit_score']}%")
                    with st.expander("📌 Missing Skills"):
                        for skill in result["missing_skills"]:
                            st.write(f"- {skill}")

                    with st.expander("💡 Suggestions"):
                        st.write(result["suggestions"])

                    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    worksheet.append_row([
                        timestamp,
                        resume_text,
                        job_description,
                        result["job_fit_score"],
                        ", ".join(result["missing_skills"]),
                        result["suggestions"]
                    ])

                    pdf_path = create_pdf_report(result['job_fit_score'], result['missing_skills'], result['suggestions'])
                    with open(pdf_path, "rb") as pdf_file:
                        PDFbyte = pdf_file.read()

                    st.download_button(
                        label="📥 Download Analysis as PDF",
                        data=PDFbyte,
                        file_name="resume_analysis_report.pdf",
                        mime="application/pdf",
                    )

                except json.JSONDecodeError:
                    st.error("⚠️ Could not parse AI output as JSON.")
                except Exception as e:
                    st.error(f"❌ Gemini API error: {e}")
