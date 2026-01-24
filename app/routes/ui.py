from flask import Blueprint, send_file
import os

ui_bp = Blueprint('ui', __name__)

# Base directory for the app (app/)
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

@ui_bp.route("/")
def index():
    try:
        path = os.path.join(BASE_DIR, "templates", "index.html")
        if not os.path.exists(path):
            return f"DEBUG: File NOT found at {path} | BASE_DIR: {BASE_DIR}", 404
        return send_file(path)
    except Exception as e:
        import traceback
        return f"ERROR: {str(e)}\n{traceback.format_exc()}", 500

@ui_bp.route("/esp32")
def esp32_dashboard():
    """Serve ESP32 sensor monitoring dashboard"""
    return send_file(os.path.join(BASE_DIR, "templates", "esp32_dashboard.html"))

@ui_bp.route("/esp32_style.css")
def esp32_style():
    """Serve ESP32 dashboard CSS"""
    return send_file(os.path.join(BASE_DIR, "static", "css", "esp32_style.css"))

@ui_bp.route("/esp32_app.js")
def esp32_app():
    """Serve ESP32 dashboard JavaScript"""
    return send_file(os.path.join(BASE_DIR, "static", "js", "esp32_app.js"))

@ui_bp.route("/session-preview-page")
def session_preview_page():
    """Serve the session preview debug page"""
    return send_file(os.path.join(BASE_DIR, "templates", "session_preview.html"))

@ui_bp.route("/gemini-analysis")
def gemini_analysis_page():
    """Serve the Gemini analysis page"""
    return send_file(os.path.join(BASE_DIR, "templates", "gemini_analysis.html"))
