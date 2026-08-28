from markupsafe import Markup

from appmanager import create_app
from appmanager.hooks import (
    HookRegistry,
    register_slot,
    render_slot,
)


def test_hook_registry_slot_registration_and_priority():
    registry = HookRegistry()

    registry.register_slot("test_slot", lambda: "<div>B</div>", priority=20, app_slug="app_b")
    registry.register_slot("test_slot", lambda: "<div>A</div>", priority=5, app_slug="app_a")
    registry.register_slot("test_slot", lambda: "<div>C</div>", priority=30, app_slug="app_c")

    rendered = registry.render_slot("test_slot")
    assert isinstance(rendered, Markup)
    assert str(rendered) == "<div>A</div><div>B</div><div>C</div>"


def test_hook_registry_slot_error_resilience():
    registry = HookRegistry()

    def faulty_callback():
        raise RuntimeError("Something exploded")

    registry.register_slot("resilient_slot", faulty_callback, priority=1, app_slug="faulty_app")
    registry.register_slot(
        "resilient_slot", lambda: "<span>Working</span>", priority=2, app_slug="good_app"
    )

    rendered = registry.render_slot("resilient_slot")
    assert "<span>Working</span>" in str(rendered)


def test_hook_registry_lifecycle_hooks():
    registry = HookRegistry()
    events = []

    def on_install_a(app_slug):
        events.append(f"A:{app_slug}")
        return "result_a"

    def on_install_b(app_slug):
        events.append(f"B:{app_slug}")
        return "result_b"

    registry.register_hook("on_app_installed", on_install_a, priority=10)
    registry.register_hook("on_app_installed", on_install_b, priority=5)

    results = registry.trigger_hook("on_app_installed", app_slug="my_test_app")
    assert events == ["B:my_test_app", "A:my_test_app"]
    assert results == ["result_b", "result_a"]


def test_jinja_render_slot_global():
    app = create_app(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
            "INSTALLED_APPS_DIR": "installed_apps",
            "SECRET_KEY": "test-secret-key-32-bytes-minimum-length",
        }
    )

    with app.app_context():
        register_slot(
            "user_badge",
            lambda user_id: f"<badge id='{user_id}'>VIP</badge>",
            priority=1,
            app_slug="vip_app",
        )
        rendered = render_slot("user_badge", 42)
        assert "<badge id='42'>VIP</badge>" in str(rendered)
