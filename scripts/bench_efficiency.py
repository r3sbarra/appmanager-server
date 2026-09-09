"""Benchmark: appmanager efficiency fixes.

Measures actual DB query counts for the dashboard health/role-count logic and
the permissions POST handler, comparing the NEW batched implementation against
the OLD per-item implementation (reconstructed for comparison).

Run:  ./venv/bin/python scripts/bench_efficiency.py
"""

import os
import sys
import time

from sqlalchemy import event

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("APPMANAGER_DATABASE_URI", "sqlite:///:memory:")
os.environ.setdefault("APPMANAGER_SECRET_KEY", "bench-secret")

from datetime import datetime, timedelta, timezone

from appmanager import create_app
from appmanager.database import db
from appmanager.models import AppHealthLog, InstalledApp, Role, User, UserAppPermission


def make_app():
    app = create_app()
    with app.app_context():
        db.drop_all()
        db.create_all()
    return app


def seed(app, n_apps=20, n_users=10, logs_per_app=15):
    # Assumes an app context is already open (caller holds it).
    for i in range(n_apps):
        db.session.add(
            InstalledApp(
                name=f"app{i}",
                slug=f"app{i}",
                source_type="git",
                is_active=True,
            )
        )
    db.session.flush()
    apps = InstalledApp.query.all()
    for i in range(n_users):
        db.session.add(User(email=f"u{i}@x.com", name=f"U{i}", role="user"))
    db.session.flush()
    users = User.query.all()
    # health logs
    for a in apps:
        for k in range(logs_per_app):
            db.session.add(
                AppHealthLog(
                    app_id=a.id,
                    status="healthy",
                    checked_at=datetime.now(timezone.utc) - timedelta(minutes=k),
                )
            )
    # some perms
    for u in users[:5]:
        for a in apps[:5]:
            db.session.add(UserAppPermission(user_id=u.id, app_id=a.id, can_access=True))
    db.session.commit()
    # Re-query fresh instances bound to the current session (commit expires them).
    return InstalledApp.query.all(), User.query.all()


def count_queries(fn):
    """Run fn inside an app context, counting SQLAlchemy queries."""
    app = create_app()
    queries = {"n": 0}
    with app.app_context():

        def before(conn, cursor, statement, parameters, context, executemany):
            queries["n"] += 1

        event.listen(db.engine, "before_cursor_execute", before)
        try:
            fn()
        finally:
            event.remove(db.engine, "before_cursor_execute", before)
    return queries["n"]


# --- NEW implementations (mirror the fixed code) ---
def new_dashboard_health(apps):
    health_map, health_history = {}, {}
    app_ids = [a.id for a in apps]
    if app_ids:
        logs = (
            AppHealthLog.query.filter(AppHealthLog.app_id.in_(app_ids))
            .order_by(AppHealthLog.checked_at.desc())
            .all()
        )
        for log in logs:
            if log.app_id not in health_map:
                health_map[log.app_id] = log
            hist = health_history.setdefault(log.app_id, [])
            if len(hist) < 12:
                hist.append(log)
        health_history = {aid: list(reversed(h)) for aid, h in health_history.items()}
    return health_map, health_history


def new_role_counts(roles):
    from appmanager.database import db

    return dict(db.session.query(User.role, db.func.count(User.id)).group_by(User.role).all())


def new_permissions_post(users, apps, form):
    existing = {
        (p.user_id, p.app_id): p
        for p in UserAppPermission.query.filter(
            UserAppPermission.user_id.in_([u.id for u in users]),
            UserAppPermission.app_id.in_([a.id for a in apps]),
        ).all()
    }
    for u in users:
        for a in apps:
            key = f"perm_{u.id}_{a.id}"
            has_access = form.get(key) == "1"
            perm = existing.get((u.id, a.id))
            if not perm:
                perm = UserAppPermission(user_id=u.id, app_id=a.id, can_access=has_access)
                db.session.add(perm)
            else:
                perm.can_access = has_access
    db.session.commit()


# --- OLD implementations (reconstructed for comparison) ---
def old_dashboard_health(apps):
    health_map, health_history = {}, {}
    for a in apps:
        latest = (
            AppHealthLog.query.filter_by(app_id=a.id)
            .order_by(AppHealthLog.checked_at.desc())
            .first()
        )
        health_map[a.id] = latest
        history = (
            AppHealthLog.query.filter_by(app_id=a.id)
            .order_by(AppHealthLog.checked_at.desc())
            .limit(12)
            .all()
        )
        health_history[a.id] = list(reversed(history))
    return health_map, health_history


def old_role_counts(roles):
    return {r.slug: User.query.filter_by(role=r.slug).count() for r in roles}


def old_permissions_post(users, apps, form):
    for u in users:
        for a in apps:
            key = f"perm_{u.id}_{a.id}"
            has_access = form.get(key) == "1"
            perm = UserAppPermission.query.filter_by(user_id=u.id, app_id=a.id).first()
            if not perm:
                perm = UserAppPermission(user_id=u.id, app_id=a.id, can_access=has_access)
                db.session.add(perm)
            else:
                perm.can_access = has_access
    db.session.commit()


def main():
    app = make_app()
    with app.app_context():
        apps, users = seed(app, n_apps=20, n_users=10, logs_per_app=15)
        if not Role.query.count():
            db.session.add(Role(name="Admin", slug="admin", is_system=True))
            db.session.add(Role(name="User", slug="user", is_system=True))
            db.session.commit()
        roles = Role.query.all()

        form = {f"perm_{u.id}_{a.id}": "1" for u in users for a in apps}

        print(f"Seed: {len(apps)} apps, {len(users)} users, {len(roles)} roles\n")

        # Dashboard health
        n_old = count_queries(lambda: old_dashboard_health(apps))
        n_new = count_queries(lambda: new_dashboard_health(apps))
        print(
            f"[Dashboard health]  OLD: {n_old} queries | NEW: {n_new} queries | "
            f"{n_old / n_new:.1f}x fewer"
        )

        # Role counts
        n_old = count_queries(lambda: old_role_counts(roles))
        n_new = count_queries(lambda: new_role_counts(roles))
        print(
            f"[Role counts]       OLD: {n_old} queries | NEW: {n_new} queries | "
            f"{n_old / max(n_new, 1):.1f}x fewer"
        )

        # Permissions POST
        n_old = count_queries(lambda: old_permissions_post(users, apps, form))
        n_new = count_queries(lambda: new_permissions_post(users, apps, form))
        print(
            f"[Permissions POST]  OLD: {n_old} queries | NEW: {n_new} queries | "
            f"{n_old / max(n_new, 1):.1f}x fewer"
        )

        # Timing (wall-clock) for permissions POST
        def timeit(fn, iters=5):
            t0 = time.perf_counter()
            for _ in range(iters):
                fn()
            return (time.perf_counter() - t0) / iters

        t_old = timeit(lambda: old_permissions_post(users, apps, form))
        t_new = timeit(lambda: new_permissions_post(users, apps, form))
        print(
            f"\n[Permissions POST wall-clock] OLD: {t_old * 1000:.2f} ms | "
            f"NEW: {t_new * 1000:.2f} ms | {t_old / t_new:.1f}x faster"
        )


if __name__ == "__main__":
    main()
