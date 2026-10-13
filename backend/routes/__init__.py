"""Route modules for the Flask app.

``load_into`` executes each module into the app module's globals and
registers a blueprint that does not prefix endpoint names. Views keep the
function names they had in ``app.py``, so ``url_for("login_page")`` and
tests that patch helpers on the app module still resolve. Importing a
route module by itself does not register routes.
"""

from __future__ import annotations

from pathlib import Path

from flask import Blueprint
from flask.sansio.blueprints import BlueprintSetupState

# Order is registration order. Werkzeug matches by specificity, and each
# file keeps the original source order of its own routes.
MODULES = ("auth", "admin", "pages", "market", "account", "trading")


class _UnprefixedSetupState(BlueprintSetupState):
    """Flask prefixes endpoints with the blueprint name. These views moved
    out of app.py without a URL change, so the endpoint stays the function name."""

    def add_url_rule(self, rule, endpoint=None, view_func=None, **options):
        if self.url_prefix is not None:
            if rule:
                rule = "/".join((self.url_prefix.rstrip("/"), rule.lstrip("/")))
            else:
                rule = self.url_prefix
        options.setdefault("subdomain", self.subdomain)
        if endpoint is None:
            if view_func is None:
                raise AssertionError("expected a view function when endpoint is omitted")
            endpoint = view_func.__name__
        defaults = self.url_defaults
        if "defaults" in options:
            defaults = dict(defaults, **options.pop("defaults"))
        self.app.add_url_rule(rule, endpoint, view_func, defaults=defaults, **options)


class SameEndpointBlueprint(Blueprint):
    def make_setup_state(self, app, options, first_registration=False):
        return _UnprefixedSetupState(self, app, options, first_registration)


def load_into(namespace: dict) -> None:
    root = Path(__file__).resolve().parent
    flask_app = namespace["app"]
    for name in MODULES:
        blueprint = SameEndpointBlueprint(name, __name__)
        namespace["_route_bp"] = blueprint
        path = root / f"{name}.py"
        code = compile(path.read_text(encoding="utf-8"), str(path), "exec")
        exec(code, namespace)  # noqa: S102 - views must share app.py's globals
        flask_app.register_blueprint(blueprint)
    namespace.pop("_route_bp", None)
