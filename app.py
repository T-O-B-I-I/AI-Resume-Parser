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

# Add the remaining routes like `admin_login`, `dashboard`, etc., here (as in your original code)

if __name__ == "__main__":
    # Ensure the uploads directory exists
    os.makedirs("uploads", exist_ok=True)
    # Run the app using Gunicorn (for production)
    #app.run(debug=True)  # or use Gunicorn for deployment in production
    pass
