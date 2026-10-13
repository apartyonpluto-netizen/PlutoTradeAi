# Loaded by routes.load_into into the Flask app module. Not imported directly.

@_route_bp.route("/service-worker.js")
def service_worker():
    # Served from the root (not /static/) so its default scope covers the
    # whole app - a service worker's scope is limited to the directory it's
    # served from unless explicitly widened.
    return send_from_directory(app.static_folder, "service-worker.js", mimetype="application/javascript")

@_route_bp.route("/login", methods=["GET", "POST"])
def login_page():
    if request.method == "GET":
        if session.get("user_id") and get_user_by_id(session["user_id"]):
            return redirect(url_for("dashboard_page"))
        return render_template("login.html", error="", next_path=request.args.get("next", ""))

    username = request.form.get("username", "")
    password = request.form.get("password", "")
    next_path = login_throttle.safe_next_path(request.form.get("next", "") or "") or ""
    # Render's proxy appends the connecting address LAST; earlier entries can be
    # supplied by the client, so they are not trusted for throttling.
    client_address = (request.headers.get("X-Forwarded-For") or request.remote_addr or "").split(",")[-1].strip()
    wait = login_throttle.blocked_for(username, client_address)
    if wait:
        return render_template("login.html", error=f"Too many failed sign-in attempts. Try again in {max(1, wait // 60)} minute(s).",
                               next_path=next_path), 429
    user = authenticate_user(username, password)
    if not user:
        login_throttle.record_failure(username, client_address)
        return render_template("login.html", error="Incorrect username or password.", next_path=next_path), 401
    login_throttle.record_success(username)
    if not user.get("approved", True):
        return render_template("login.html", error="Your account is still pending admin approval.", next_path=next_path), 403
    if user.get("suspended", False):
        return render_template("login.html", error="This account has been suspended.", next_path=next_path), 403

    session["user_id"] = user["id"]
    session.permanent = True
    target = next_path or url_for("dashboard_page")
    return redirect(target)

@_route_bp.route("/register", methods=["GET", "POST"])
def register_page():
    # Registration stays open for the very first account regardless of the
    # setting (there'd be no admin yet to have configured it, and no admin
    # to approve anyone in), and only blocks signups after that.
    registration_open = get_global_settings()["registration_open"] or not list_all_user_ids()
    if request.method == "GET":
        if session.get("user_id") and get_user_by_id(session["user_id"]):
            return redirect(url_for("dashboard_page"))
        if not registration_open:
            return render_template("register.html", error="Registration is currently closed."), 403
        return render_template("register.html", error="")

    if not registration_open:
        return render_template("register.html", error="Registration is currently closed."), 403

    username = request.form.get("username", "")
    password = request.form.get("password", "")
    confirm_password = request.form.get("confirm_password", "")
    if password != confirm_password:
        return render_template("register.html", error="Passwords do not match."), 400
    try:
        user = register_user(username, password)
    except ValueError as error:
        return render_template("register.html", error=str(error)), 400

    if not user.get("approved", True):
        return render_template(
            "login.html",
            error="Account created. An admin needs to approve it before you can sign in.",
            next_path="",
        )

    session["user_id"] = user["id"]
    session.permanent = True
    return redirect(url_for("dashboard_page"))

@_route_bp.route("/forgot-password", methods=["GET", "POST"])
def forgot_password_page():
    if request.method == "GET":
        return render_template("forgot_password.html", error="", success="")

    username = request.form.get("username", "")
    password = request.form.get("password", "")
    confirm_password = request.form.get("confirm_password", "")
    if password != confirm_password:
        return render_template("forgot_password.html", error="Passwords do not match.", success=""), 400
    try:
        reset_password(username, password)
    except ValueError as error:
        return render_template("forgot_password.html", error=str(error), success=""), 400

    return render_template("forgot_password.html", error="", success="Password updated. You can sign in now.")

@_route_bp.route("/healthz")
def healthz():
    """Unauthenticated liveness check for the hosting platform's health probe -
    intentionally does nothing but confirm the process is up and can render a
    response, no auth/DB/broker calls that could make a slow dependency look
    like a dead app."""
    return {"status": "ok"}, 200

@_route_bp.route("/logout", methods=["POST"])
def logout_page():
    session.pop("user_id", None)
    return redirect(url_for("login_page"))
