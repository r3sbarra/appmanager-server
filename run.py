import os

from werkzeug.serving import run_simple

from appmanager import create_dispatchable_app

application = create_dispatchable_app()

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    print("\n=======================================================")
    print(f" AppManager Server starting at http://localhost:{port}")
    print("=======================================================\n")
    run_simple("0.0.0.0", port, application, use_reloader=False, use_debugger=False, threaded=True)
