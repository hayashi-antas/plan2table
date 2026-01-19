---
title: Plan2table
emoji: 🌖
colorFrom: purple
colorTo: blue
sdk: docker
app_port: 7860
pinned: false
license: mit
---

# Plan2Table

A minimalist, high-performance web application that uses Google Cloud Vertex AI (Gemini 1.5 Flash) to extract room information from architectural drawings (PDF) into a structured table.

## Features
- **Upload & Analyze**: Drag and drop architectural PDFs.
- **AI Extraction**: Uses Gemini 1.5 Flash to identify Room Name, Area, Floor Finish, and Ceiling Height.
- **Instant Result**: Returns a clean HTML table ready for copy-pasting.

## Setup
This Space is built with **FastAPI** and **Docker**.

### Environment Variables
To run this Space, you must configure the following in the Space Settings:
- `GOOGLE_CLOUD_PROJECT`: Your Google Cloud Project ID.
- `GCP_SERVICE_ACCOUNT_KEY`: (Secret) The **content** of your Service Account JSON key file. Paste the entire JSON string here.

The application automatically detects `GCP_SERVICE_ACCOUNT_KEY`, saves it to a temporary file, and authenticates the Google Cloud client.
