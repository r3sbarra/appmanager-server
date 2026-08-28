# Deployment & Scheduled Tasks

This guide details how to deploy AppManager to production WSGI environments, with special focus on **PythonAnywhere** and standard Linux servers (Gunicorn/Nginx).

---

## PythonAnywhere Deployment

AppManager was engineered with PythonAnywhere's unique constraints in mind:
- **Shared Python Runtime**: Scoped module namespaces prevent sub-app collision.
- **Scheduled Tasks**: Built-in CLI commands integrate seamlessly with PythonAnywhere's task scheduler.

### 1. Setup Virtual Environment and Code on PythonAnywhere

In a PythonAnywhere Bash Console:

```bash
cd ~
git clone https://github.com/appmanager/appmanager.git
cd appmanager
mkvirtualenv --python=/usr/bin/python3.12 appmanager-venv
pip install -e .
cp .env.example .env
```

Edit your `.env` file to set `SECRET_KEY` and production database paths.

### 2. Configure the Web Tab

1. Go to the **Web** tab in PythonAnywhere.
2. Under **Virtualenv**, set the path to `/home/<your-username>/.virtualenvs/appmanager-venv`.
3. Open the **WSGI configuration file** (`/var/www/<your-username>_pythonanywhere_com_wsgi.py`) and configure:

```python
import os
import sys

# Path to AppManager project directory
project_home = "/home/<your-username>/appmanager"
if project_home not in sys.path:
    sys.path.insert(0, project_home)

# Load environment variables
from dotenv import load_dotenv

load_dotenv(os.path.join(project_home, ".env"))

# Import the WSGI dispatchable application
from appmanager import create_dispatchable_app

application = create_dispatchable_app()
```

4. Click **Reload <your-username>.pythonanywhere.com**.

### 3. Setup PythonAnywhere Scheduled Tasks

To enable automated background tasks, health evaluations, and session cleanup:

1. Open the **Tasks** tab.
2. Add a new **Scheduled Task** running hourly or daily:
   ```bash
   /home/<your-username>/.virtualenvs/appmanager-venv/bin/appmanager run-scheduled-tasks
   ```

---

## Production Deployment with Gunicorn / Nginx

For standard VPS (Ubuntu, Debian) or containerized environments:

### Gunicorn Command

```bash
gunicorn "appmanager:create_dispatchable_app()" \
    --workers 4 \
    --bind 0.0.0.0:8000 \
    --access-logfile - \
    --error-logfile -
```

### System Cron (`crontab -e`)

```cron
# Run scheduled tasks and health checks every hour
0 * * * * cd /var/www/appmanager && /var/www/appmanager/venv/bin/appmanager run-scheduled-tasks >> /var/log/appmanager-cron.log 2>&1
```
