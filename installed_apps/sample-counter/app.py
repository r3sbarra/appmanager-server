from flask import Flask, redirect, render_template_string, session, url_for

app = Flask(__name__)
app.secret_key = "sample-counter-secret-key-32-bytes"

TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>Sample Counter App</title>
    <style>
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #0f172a; color: white; display: flex; justify-content: center; align-items: center; min-height: 100vh; margin: 0; }
        .card { background: #1e293b; padding: 2.5rem; border-radius: 16px; border: 1px solid #334155; text-align: center; max-width: 420px; width: 100%; box-shadow: 0 20px 25px -5px rgba(0,0,0,0.5); }
        h1 { font-size: 1.5rem; margin-top: 0; }
        p { color: #94a3b8; font-size: 0.9rem; line-height: 1.5; }
        .count { font-size: 3.5rem; font-weight: 800; color: #818cf8; margin: 1.5rem 0; }
        .btn { padding: 0.75rem 1.5rem; border-radius: 8px; background: #6366f1; color: white; text-decoration: none; border: none; cursor: pointer; font-size: 1rem; font-weight: 600; display: inline-block; transition: background 0.2s; }
        .btn:hover { background: #4f46e5; }
    </style>
</head>
<body>
    <div class="card">
        <h1>Counter Sub-App</h1>
        <p>This is a standalone Flask sub-app running dynamically inside the AppManager WSGI container!</p>
        <div class="count">{{ count }}</div>
        <form method="POST" action="increment">
            <button type="submit" class="btn">Increment Counter</button>
        </form>
        <br><br>
        <a href="/" style="color: #94a3b8; font-size: 0.85rem; text-decoration: none;">&larr; Back to AppManager Dashboard</a>
    </div>
</body>
</html>
"""


@app.route("/")
def index():
    count = session.get("count", 0)
    return render_template_string(TEMPLATE, count=count)


@app.route("/increment", methods=["POST"])
def increment():
    session["count"] = session.get("count", 0) + 1
    return redirect(url_for("index"))


@app.route("/health")
def health():
    return {"status": "healthy", "app": "sample-counter"}
