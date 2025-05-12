import os
import json
import requests
import pdfplumber
import fitz  # PyMuPDF for fallback text extraction
from flask import Flask, render_template, request, redirect, url_for, session, flash
from bson.objectid import ObjectId
from pymongo import MongoClient
from dotenv import load_dotenv
from werkzeug.utils import secure_filename  # ✅ Add this import

# Load environment variables from .env file
load_dotenv()

# Initialize Flask app
app = Flask(__name__)
app.secret_key = os.urandom(24)

# Groq API
API_KEY = os.getenv("GROQ_API_KEY")
URL = "https://api.groq.com/openai/v1/chat/completions"

# MongoDB
MONGO_URI = os.getenv("MONGO_URI")
print(f"Connecting to MongoDB at {MONGO_URI}")
client = MongoClient(MONGO_URI)
db = client["ats_db"]
collection = db["resumes"]

# Admin credentials
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "password")

# Groq headers
headers = {
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type": "application/json"
}

# Model
MODEL = "llama-3.3-70b-versatile"

def extract_text_from_pdf(pdf_path):
    text = ""
    try:
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    text += page_text
    except Exception as e:
        print(f"Error with pdfplumber: {e}")
        text = extract_text_with_pymupdf(pdf_path)
    return text

def extract_text_with_pymupdf(pdf_path):
    text = ""
    try:
        doc = fitz.open(pdf_path)
        for page_num in range(doc.page_count):
            page = doc.load_page(page_num)
            text += page.get_text()
    except Exception as e:
        print(f"Error with PyMuPDF: {e}")
    return text

def parse_resume(text):
    system_prompt = "You are an expert resume parser. Extract structured data from resumes."
    user_prompt = f"""Here is a resume:

{text}

Extract the following:
- Full Name
- Contact Info:
    - Email
    - Phone
    - LinkedIn
    - GitHub
    - Personal Website
- Skills:
    - Technical Skills
    - Personal Skills
    - Programming Languages
    - Database
    - Tools
    - Languages
- Education (Degree, Field of Study, University, Duration)
- Work Experience (Company, Role, Duration)
- Certifications (if any)

Return the result in JSON format.
"""

    data = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        "temperature": 0.3,
        "max_tokens": 1200
    }

    try:
        response = requests.post(URL, headers=headers, json=data)
        response.raise_for_status()
        print("API Response:", response.text)
        response_json = response.json()
        parsed_data = response_json.get('choices', [{}])[0].get('message', {}).get('content', '')

        if parsed_data:
            try:
                start_index = parsed_data.find('```json') + len('```json')
                end_index = parsed_data.find('```', start_index)
                json_data_str = parsed_data[start_index:end_index].strip()
                if json_data_str:
                    return json.loads(json_data_str)
                else:
                    return "❌ Error: No valid JSON block found in the response."
            except (json.JSONDecodeError, ValueError) as e:
                return f"❌ Error: Failed to parse JSON from API response: {e}"
        else:
            return "❌ Error: No parsed data found in response"
    except requests.exceptions.RequestException as e:
        return f"❌ Error: {e}"
    except json.decoder.JSONDecodeError as e:
        return f"❌ Error: Invalid JSON response"

def sanitize_result(data):
    data = data or {}
    data['education'] = data.get('Education', [])
    data['workExperience'] = data.get('Work Experience', [])
    data['certifications'] = data.get('Certifications', [])
    data['Contact Info'] = data.get('Contact Info', {})
    data['Skills'] = data.get('Skills', {})
    return data

@app.route('/result', methods=['POST'])
def result():
    file = request.files.get('resume')
    if not file or file.filename == '':
        return render_template('result.html', parsed_result="No resume uploaded.")
    parsed_result = get_parsed_result(file)
    parsed_result = sanitize_result(parsed_result)
    return render_template('result.html', parsed_result=parsed_result)

@app.route("/admin", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]
        if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
            session["logged_in"] = True
            return redirect(url_for("dashboard"))
        else:
            return "Invalid credentials, please try again.", 401
    return render_template("admin_login.html")

@app.route("/logout", methods=["GET", "POST"])
def logout():
    session.pop("logged_in", None)
    flash("You have been logged out.")
    return redirect(url_for("admin_login"))

@app.route("/resumes")
def dashboard():
    if not session.get("logged_in"):
        return redirect(url_for("admin_login"))
    resumes = list(collection.find())
    return render_template("dashboard.html", resumes=resumes)

@app.route("/delete/<resume_id>", methods=["POST"])
def delete_resume(resume_id):
    try:
        collection.delete_one({"_id": ObjectId(resume_id)})
        flash("✅ Resume deleted successfully.")
    except Exception as e:
        flash(f"❌ Error deleting resume: {e}")
    return redirect(url_for("dashboard"))

@app.route("/resumes/<resume_id>")
def view_resume(resume_id):
    try:
        resume = collection.find_one({"_id": ObjectId(resume_id)})
        if not resume:
            return "Resume not found", 404
        sanitized_resume = sanitize_result(resume)
        return render_template("result.html", parsed_result=sanitized_resume)
    except Exception as e:
        return f"Error: {e}", 500    

@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        if "file" not in request.files:
            return "No file part", 400
        file = request.files["file"]
        if file.filename == "":
            return "No selected file", 400
        if not file.filename.lower().endswith(".pdf"):
            return "Invalid file type. Please upload a PDF file.", 400

        # ✅ Ensure uploads folder exists
        os.makedirs("uploads", exist_ok=True)

        # ✅ Sanitize filename to prevent issues with special characters
        safe_filename = secure_filename(file.filename)
        file_path = os.path.join("uploads", safe_filename)
        file.save(file_path)

        resume_text = extract_text_from_pdf(file_path)
        if resume_text.strip() == "":
            return "Failed to extract text from the resume.", 400
        else:
            parsed_result = parse_resume(resume_text)
            if isinstance(parsed_result, dict):
                try:
                    collection.insert_one(parsed_result)
                except Exception as e:
                    print(f"⚠️ Failed to save to MongoDB: {e}")
                return render_template("result.html", parsed_result=parsed_result)
            else:
                return render_template("result.html", parsed_result=parsed_result)
    return render_template("index.html")

if __name__ == "__main__":
    os.makedirs("uploads", exist_ok=True)
    app.run(debug=True)
