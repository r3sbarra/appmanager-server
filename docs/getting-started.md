# Getting Started

This guide walks you through installing AppManager, configuring your environment, seeding initial sample applications, and launching your multi-tenant portal.

---

## Prerequisites

- **Python 3.10+** (Tested on 3.10, 3.11, and 3.12)
- `pip` package manager
- Optional: `git` (for installing sub-apps from Git repositories)

---

## Installation

### Via pip

Install the latest release directly from PyPI:

```bash
pip install appmanager-server
```

### From Source (Development Mode)

If you are contributing to AppManager or running from source:

```bash
git clone https://github.com/r3sbarra/appmanager-server.git
cd appmanager-server
python3 -m venv venv
source venv/bin/activate
pip install -e ".[dev,docs]"
```

---

## Project Initialization

To bootstrap a new AppManager project directory:

```bash
appmanager init
```

This command creates:
1. An `installed_apps/` directory for hosting sub-apps.
2. A `.env` file with default development configuration.

---

## Seeding the Database

AppManager comes with built-in reference sub-apps (`sample-counter`, `template-app`, and `extension-flairs`). Seed your SQLite database with these initial records:

```bash
appmanager seed
```

Output:
```text
[SEED] Registered sample-counter app in database.
[SEED] Registered template-app in database.
[SEED] Registered extension-flairs in database.
[SEED] Database seeding complete.
```

---

## Starting the Server

Run the development WSGI server:

```bash
appmanager run
```

You can specify a custom host or port:

```bash
appmanager run --host 0.0.0.0 --port 8080
```

Open your browser and navigate to:
- **Host Dashboard**: [http://localhost:5000/dashboard](http://localhost:5000/dashboard)
- **Admin Portal**: [http://localhost:5000/admin](http://localhost:5000/admin)
- **Sample Counter Sub-App**: [http://localhost:5000/apps/sample-counter/](http://localhost:5000/apps/sample-counter/)
- **Template Sub-App**: [http://localhost:5000/apps/template-app/](http://localhost:5000/apps/template-app/)

---

## First Time Login & Admin Access

AppManager supports passwordless **Magic Link** authentication as well as Google OAuth.

1. Navigate to `/auth/login`.
2. Enter your email address (e.g. `admin@example.com`).
3. If SMTP is not configured, the Magic Link login URL is printed directly to your console/terminal.
4. Click the Magic Link to sign in. The first user created can be assigned the `admin` role via the database or admin interface.
