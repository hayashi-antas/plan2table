"""App configuration: GCP credentials, Vertex AI client, and Jinja2 templates."""

from __future__ import annotations

import atexit
import os
import tempfile

from fastapi.templating import Jinja2Templates
from google import genai

# Environment
project_id = os.getenv("GOOGLE_CLOUD_PROJECT")
location = os.getenv("VERTEX_LOCATION", "global")
MODEL_NAME = os.getenv("VERTEX_MODEL_NAME", "gemini-3.1-pro-preview")

# One service account key for both Vertex AI and Vision API (GCP_SERVICE_ACCOUNT_KEY preferred)
_gcp_service_account_json = (
    os.getenv("GCP_SERVICE_ACCOUNT_KEY")
    or os.getenv("VERTEX_SERVICE_ACCOUNT_KEY")
    or os.getenv("VISION_SERVICE_ACCOUNT_KEY")
    or ""
)
vertex_service_account_json = _gcp_service_account_json
vision_service_account_json = _gcp_service_account_json

# Credential temp file handling (secure, 0o600, cleanup on exit)
_cred_temp_paths: list[str] = []


def _cleanup_cred_temp_files() -> None:
    for p in _cred_temp_paths:
        try:
            if os.path.exists(p):
                os.unlink(p)
        except OSError:
            pass
    _cred_temp_paths.clear()


if vision_service_account_json:
    fd, cred_file_path = tempfile.mkstemp(suffix=".json", prefix="gcp_credentials_")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(vision_service_account_json)
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = cred_file_path
        _cred_temp_paths.append(cred_file_path)
        atexit.register(_cleanup_cred_temp_files)
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            os.unlink(cred_file_path)
        except OSError:
            pass
        raise

genai_client: genai.Client | None = None
try:
    if project_id:
        genai_client = genai.Client(
            vertexai=True, project=project_id, location=location
        )
    else:
        genai_client = genai.Client(vertexai=True, location=location)
except Exception as exc:
    print(f"Failed to initialize Vertex AI client: {exc}")

templates = Jinja2Templates(directory="templates")
