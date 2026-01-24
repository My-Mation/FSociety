from app import create_app
from app.db import ensure_db_schema
from app.services.batch_processor import start_worker
import os
from dotenv import load_dotenv

load_dotenv()

app = create_app()

if __name__ == "__main__":
    print(">>> MACHINE SOUND CALIBRATION & DETECTION SYSTEM <<<")
    ensure_db_schema()

    @app.route("/debug")
    def debug_route():
        return "DEBUG ROUTE ORCHESTRATION OK", 200

    start_worker()
    
    # Run server
    app.run(host="0.0.0.0", port=5000, debug=False)
