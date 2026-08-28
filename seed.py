from appmanager import create_app
from appmanager.database import db
from appmanager.models import InstalledApp


def seed_database():
    app = create_app()
    with app.app_context():
        # Check if sample-counter is registered
        counter_app = InstalledApp.query.filter_by(slug="sample-counter").first()
        if not counter_app:
            counter_app = InstalledApp(
                name="Counter Sub-App",
                slug="sample-counter",
                description="Interactive Flask counter demonstration app",
                source_type="zip",
                source_url="sample-counter.zip",
                entry_point="app:app",
                is_active=True,
            )
            db.session.add(counter_app)
            db.session.commit()
            print("[SEED] Registered sample-counter app in database.")

        template_app = InstalledApp.query.filter_by(slug="template-app").first()
        if not template_app:
            template_app = InstalledApp(
                name="Template Reference App",
                slug="template-app",
                description="Standardized Flask template application demonstrating health check, bridge telemetry, and scheduled cron task integration.",
                source_type="zip",
                source_url="template-app.zip",
                entry_point="app:app",
                is_active=True,
            )
            db.session.add(template_app)
            db.session.commit()
            print("[SEED] Registered template-app in database.")

        extension_app = InstalledApp.query.filter_by(slug="extension-flairs").first()
        if not extension_app:
            extension_app = InstalledApp(
                name="User Flairs Extension",
                slug="extension-flairs",
                description="Adds customizable user titles, flairs, and badges to member profiles and admin views.",
                source_type="zip",
                source_url="extension-flairs.zip",
                entry_point="extension:extension",
                app_type="extension",
                target_app="appmanager",
                has_web_ui=False,
                is_active=True,
            )
            db.session.add(extension_app)
            db.session.commit()
            print("[SEED] Registered extension-flairs in database.")
        else:
            extension_app.app_type = "extension"
            extension_app.target_app = "appmanager"
            extension_app.has_web_ui = False
            db.session.commit()
            print("[SEED] Updated extension-flairs DB record (has_web_ui=False).")


if __name__ == "__main__":
    seed_database()
