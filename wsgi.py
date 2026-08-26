"""WSGI entry point. Used by `python wsgi.py` locally and by gunicorn on Heroku."""
import os

from brain_app import create_app

app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=True)
