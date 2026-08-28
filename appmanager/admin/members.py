"""
Member list/detail query helpers for the admin console.

Keeps the member-management data access out of routes.py so the
pagination/search/aggregation logic is testable in isolation and the
N+1 permission lookups are contained in one place.
"""

from sqlalchemy import func, or_

from appmanager.database import db
from appmanager.models import InstalledApp, User, UserAppPermission

DEFAULT_PER_PAGE = 25
PER_PAGE_CHOICES = (25, 50, 100)


def list_members(q=None, role=None, page=1, per_page=DEFAULT_PER_PAGE, sort="last_login"):
    """
    Server-side paginated + filtered member list.

    Returns a SQLAlchemy Pagination object. Never loads the whole table.
    """
    if per_page not in PER_PAGE_CHOICES:
        per_page = DEFAULT_PER_PAGE

    query = User.query

    if q:
        like = f"%{q.strip()}%"
        query = query.filter(or_(User.name.ilike(like), User.email.ilike(like)))
    if role:
        query = query.filter_by(role=role)

    if sort == "name":
        query = query.order_by(User.name.asc())
    elif sort == "email":
        query = query.order_by(User.email.asc())
    elif sort == "created":
        query = query.order_by(User.created_at.desc())
    elif sort == "logins":
        query = query.order_by(User.login_count.desc())
    else:  # last_login
        query = query.order_by(User.last_login_at.desc().nullslast())

    return query.paginate(page=page, per_page=per_page, error_out=False)


def app_access_counts(user_ids):
    """
    One aggregate query: {user_id: count_of_apps_they_can_access}.
    Replaces the per-user N+1 permission lookups in the member table.
    """
    if not user_ids:
        return {}
    rows = (
        db.session.query(UserAppPermission.user_id, func.count(UserAppPermission.id))
        .filter(UserAppPermission.user_id.in_(user_ids))
        .filter(UserAppPermission.can_access.is_(True))
        .group_by(UserAppPermission.user_id)
        .all()
    )
    return {uid: cnt for uid, cnt in rows}


def total_app_count():
    """Number of installed apps (for the 'x of N apps' readout)."""
    return InstalledApp.query.count()


def member_permissions(user_id):
    """
    Full per-app access list for a single member (used by the edit drawer).
    Returns list of dicts: {app_id, name, slug, can_access, inherited}.
    """
    user = db.session.get(User, user_id)
    if not user:
        return []
    apps = InstalledApp.query.order_by(InstalledApp.name.asc()).all()
    perms = {
        p.app_id: p.can_access for p in UserAppPermission.query.filter_by(user_id=user_id).all()
    }
    inherited = user.is_admin()
    result = []
    for a in apps:
        result.append(
            {
                "app_id": a.id,
                "name": a.name,
                "slug": a.slug,
                "can_access": perms.get(a.id, True),
                "inherited": inherited,
            }
        )
    return result
