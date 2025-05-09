import os
import json
import requests
import pdfplumber
import fitz  # PyMuPDF for fallback text extraction
from flask import Flask, render_template, request, redirect, url_for, session, flash
from bson.objectid import ObjectId
from pymongo import MongoClient
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Initialize Flask app
app = Flask(__name__)
app.secret_key = os.urandom(24)  # Secure session key

# Groq API Key and URL
API_KEY = os.getenv("GROQ_API_KEY")
URL = "https://api.groq.com/openai/v1/chat/completions"

# MongoDB connection
MONGO_URI = os.getenv("MONGO_URI")
client = MongoClient(MONGO_URI)
db = client["ats_db"]
collection = db["resumes"]

# Admin credentials from environment variables
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "password")

# Groq API headers
headers = {
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type": "application/json"
}

# Resume parsing model
MODEL = "llama-3.3-70b-versatile"  # Replace with your actual model name

# Function to extract text from PDF using pdfplumber
def extract_text_from_pdf(pdf_path):
    text = ""
    try:
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    text += page_text
                else:
                    print(f"Warning: No text found on page {page.page_number}")
    except Exception as e:
        print(f"Error with pdfplumber: {e}")
        text = extract_text_with_pymupdf(pdf_path)  # Fallback to PyMuPDF
    return text

# Fallback function: extract text from PDF using PyMuPDF
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

# Function to parse resume with Groq API
def parse_resume(text):
    system_prompt = "You are an expert resume parser. Extract structured data from resumes."

    user_prompt = f"""
    Here is a resume:

    {text}

    Extract the following:
    - Full Name
    - Contact Info (email, phone if available)
    - Skills
    - Education
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
        response.raise_for_status()  # Raise an error for bad responses

        # Print the raw response for debugging
        print("API Response:", response.text)

        # Check if response is valid JSON
        if not response.text.strip():
            return "❌ Error: Empty response from API"

        # Extract the JSON content from the response
        response_json = response.json()

        # Print the JSON response to check its structure
        print("Parsed JSON:", response_json)

        # Access the parsed data inside the 'choices' key
        parsed_data = response_json.get('choices', [{}])[0].get('message', {}).get('content', '')

        # Check if parsed_data contains the expected JSON block
        if parsed_data:
            # Look for the JSON part within the response
            try:
                start_index = parsed_data.find('```json') + len('```json')
                end_index = parsed_data.find('```', start_index)
                json_data_str = parsed_data[start_index:end_index].strip()

                if json_data_str:
                    # Parse the cleaned-up JSON string
                    parsed_json = json.loads(json_data_str)
                    return parsed_json
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



# Route for admin login
@app.route("/admin", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]

        # Validate admin credentials
        if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
            session["logged_in"] = True  # Correct session key here
            return redirect(url_for("dashboard"))
        else:
            return "Invalid credentials, please try again.", 401

    return render_template("admin_login.html")

# Route for admin logout
@app.route("/logout", methods=["GET", "POST"])
def logout():
    # Remove the admin session to log out
    session.pop("logged_in", None)  # Use the correct session key
    flash("You have been logged out.")  # Flash message (string)
    return redirect(url_for("admin_login"))  # Redirect to the login page after logout


# Dashboard route for viewing all resumes
@app.route("/resumes")
def dashboard():
    if not session.get("logged_in"):
        return redirect(url_for("admin_login"))  # If not logged in, redirect to login page

    # Fetch all resumes from MongoDB
    resumes = list(collection.find())
    return render_template("dashboard.html", resumes=resumes)

# Dashboard route for deleting resumes
@app.route("/delete/<resume_id>", methods=["POST"])
def delete_resume(resume_id):
    try:
        collection.delete_one({"_id": ObjectId(resume_id)})
        flash("✅ Resume deleted successfully.")
    except Exception as e:
        flash(f"❌ Error deleting resume: {e}")
    return redirect(url_for("dashboard"))

# Route for viewing a specific resume
@app.route("/resumes/<resume_id>")
def view_resume(resume_id):
    try:
        resume = collection.find_one({"_id": ObjectId(resume_id)})
        if not resume:
            return "Resume not found", 404
        return render_template("result.html", parsed_result=resume)
    except Exception as e:
        return f"Error: {e}", 500    

# Route for uploading resume and getting parsed result 
@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        if "file" not in request.files:
            return "No file part", 400
        file = request.files["file"]
        if file.filename == "":
            return "No selected file", 400

        # Validate file type (PDF only)
        if not file.filename.lower().endswith(".pdf"):
            return "Invalid file type. Please upload a PDF file.", 400
        
        # Save the file to the uploads folder
        file_path = os.path.join("uploads", file.filename)
        file.save(file_path)

        # Extract text from the uploaded resume
        resume_text = extract_text_from_pdf(file_path)

        if resume_text.strip() == "":
            return "Failed to extract text from the resume.", 400
        else:
            # Send resume text to Groq for parsing
            parsed_result = parse_resume(resume_text)
            if isinstance(parsed_result, dict):
                # Save parsed data to MongoDB
                try:
                    collection.insert_one(parsed_result)
                except Exception as e:
                    print(f"⚠️ Failed to save to MongoDB: {e}")
                return render_template("result.html", parsed_result=parsed_result)

            else:
                return render_template("result.html", parsed_result=parsed_result)  # Display the error message

    return render_template("index.html")

if __name__ == "__main__":
    # Ensure the uploads directory exists
    os.makedirs("uploads", exist_ok=True)
    app.run(debug=True)
