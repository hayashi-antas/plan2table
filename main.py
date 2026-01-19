import os
import vertexai
from vertexai.generative_models import GenerativeModel, Part
from fastapi import FastAPI, File, UploadFile, Request
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse
import uvicorn

app = FastAPI()
templates = Jinja2Templates(directory="templates")

# Initialize Vertex AI
# We use the environment variable GOOGLE_CLOUD_PROJECT as requested.
project_id = os.getenv("GOOGLE_CLOUD_PROJECT")
location = "us-central1" # Defaulting to us-central1

# Handle Credentials for Hugging Face Spaces
# If GCP_SERVICE_ACCOUNT_KEY env var exists (JSON content), write it to a file
service_account_json = os.getenv("GCP_SERVICE_ACCOUNT_KEY")
if service_account_json:
    cred_file_path = "gcp_credentials.json"
    with open(cred_file_path, "w") as f:
        f.write(service_account_json)
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = cred_file_path
    print(f"Credentials saved to {cred_file_path}")

if project_id:
    vertexai.init(project=project_id, location=location)

@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.post("/upload", response_class=HTMLResponse)
async def handle_upload(file: UploadFile = File(...)):
    if not file.filename.lower().endswith('.pdf'):
        return """
        <div class="p-4 bg-red-900/50 border border-red-500 text-red-200 rounded-lg">
            <strong>Error:</strong> Please upload a valid PDF file.
        </div>
        """
    
    try:
        # Read file content
        file_bytes = await file.read()
        
        # Prepare the request for Vertex AI
        pdf_part = Part.from_data(data=file_bytes, mime_type="application/pdf")
        
        prompt_text = """You are an expert architect. Analyze the attached PDF drawing. Extract all room information including: 室名 (Room Name), 面積/帖数 (Area), 床仕上 (Floor Finish), and 天井高/CH (Ceiling Height). 
     OUTPUT REQUIREMENT:
     - Language: Japanese (日本語).
     - Format: ONLY a clean HTML <table> with Tailwind CSS classes.
     - No markdown code blocks (like ```html), no preamble, no conversational text. Just the <table> tag."""

        # Using gemini-1.5-flash as requested
        model = GenerativeModel("gemini-1.5-flash")
        
        # Generate content
        response = model.generate_content(
            [pdf_part, prompt_text],
            generation_config={
                "temperature": 0.2,
                "max_output_tokens": 2048,
            }
        )
        
        return response.text

    except Exception as e:
        # Log the error for debugging (on the server console)
        print(f"Error processing upload: {e}")
        return f"""
        <div class="p-4 bg-red-900/50 border border-red-500 text-red-200 rounded-lg">
            <strong>Error Processing Request:</strong><br>
            {str(e)}
        </div>
        """

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
