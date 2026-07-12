import os
import csv
import sqlite3
import uuid
from datetime import datetime, timezone
from functools import wraps

import pandas as pd
from flask import (
    Flask, render_template, request, flash, redirect,
    url_for, send_from_directory, abort, session, g
)
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = "change-this-in-production"  # required for flash(), session, CSRF
app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 * 1024  # 8 MB upload cap

RESULTS_DIR = os.path.join(app.instance_path, "generated")
DB_PATH = os.path.join(app.instance_path, "housing.db")
os.makedirs(app.instance_path, exist_ok=True)
os.makedirs(RESULTS_DIR, exist_ok=True)

# Optional but recommended for real CSRF protection (SRS 6.3 Security):
#   pip install flask-wtf
#   from flask_wtf import CSRFProtect
#   CSRFProtect(app)
# {{ csrf_token() }} in the templates picks this up automatically once
# CSRFProtect is registered. Without it, the hidden field is a harmless
# no-op — fine for local demos only.

# ---- Schema, derived from the project's real training data ----------
LOCATIONS = [
    "Vijay Nagar", "Palasia", "Bhawarkua", "Rajendra Nagar", "Tilak Nagar",
    "Sudama Nagar", "Scheme 54", "Scheme 78", "MR 10", "Annapurna",
]
FURNISHING = ["furnished", "semi-furnished", "unfurnished"]

# Reference stats from the training set (1,000 Indore-area listings).
DATASET_STATS = {
    "rows": 1000,
    "columns": 10,
    "localities": len(LOCATIONS),
    "price_min": 5_212_000,
    "price_max": 26_592_000,
    "price_mean": 15_761_130,
    "area_mean": 4267,
}

LOCATION_PREMIUM = {
    "Vijay Nagar": 1.08, "Palasia": 1.12, "Bhawarkua": 0.97,
    "Rajendra Nagar": 0.94, "Tilak Nagar": 1.02, "Sudama Nagar": 0.96,
    "Scheme 54": 1.05, "Scheme 78": 1.03, "MR 10": 1.10, "Annapurna": 0.99,
}
FURNISH_BONUS = {"furnished": 900_000, "semi-furnished": 450_000, "unfurnished": 0}


# =====================================================================
# Formatting filters
# =====================================================================
def format_inr(value):
    """Indian digit grouping: 1,57,61,130 instead of 15,761,130."""
    value = int(round(value))
    sign = "-" if value < 0 else ""
    s = str(abs(value))
    if len(s) <= 3:
        return sign + s
    head, tail = s[:-3], s[-3:]
    parts = []
    while len(head) > 2:
        parts.insert(0, head[-2:])
        head = head[:-2]
    if head:
        parts.insert(0, head)
    return sign + ",".join(parts + [tail])


def to_lakh_crore(value):
    value = float(value)
    if value >= 1_00_00_000:
        return f"{value / 1_00_00_000:.2f} Cr"
    return f"{value / 1_00_000:.1f} L"


app.jinja_env.filters["inr"] = format_inr
app.jinja_env.filters["lakh_crore"] = to_lakh_crore


# =====================================================================
# Database (SRS 4.4 Data Acquisition/Integrity/Retention/Disposal,
# 4.1 Logical Data Model: House / User(Admin) entities)
# =====================================================================
def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(exception=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    db = sqlite3.connect(DB_PATH)
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS houses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            area INTEGER NOT NULL,
            bedrooms INTEGER NOT NULL,
            bathrooms INTEGER NOT NULL,
            stories INTEGER NOT NULL,
            parking INTEGER NOT NULL,
            mainroad TEXT NOT NULL,
            near_market TEXT NOT NULL,
            furnishingstatus TEXT NOT NULL,
            location TEXT NOT NULL,
            price INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS admins (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            admin_username TEXT NOT NULL,
            action TEXT NOT NULL,
            details TEXT
        );

        CREATE TABLE IF NOT EXISTS prediction_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            source TEXT NOT NULL,           -- 'single' or 'batch'
            area INTEGER, bedrooms INTEGER, bathrooms INTEGER,
            stories INTEGER, parking INTEGER, mainroad TEXT,
            near_market TEXT, furnishingstatus TEXT, location TEXT,
            predicted_price INTEGER,
            row_count INTEGER DEFAULT 1     -- >1 for batch uploads
        );
        """
    )
    db.commit()

    # Seed a default admin (SRS 2.2 Admin user class / 6.3 Security).
    if db.execute("SELECT COUNT(*) FROM admins").fetchone()[0] == 0:
        db.execute(
            "INSERT INTO admins (username, password_hash) VALUES (?, ?)",
            ("admin", generate_password_hash("ChangeMe123")),
        )
        db.commit()

    # Seed houses from the bundled sample so the admin has real records
    # to manage (SRS 4.4.1 Data Acquisition).
    if db.execute("SELECT COUNT(*) FROM houses").fetchone()[0] == 0:
        sample_path = os.path.join(app.root_path, "static", "data", "sample_data.csv")
        if os.path.isfile(sample_path):
            with open(sample_path, newline="", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                rows = []
                for r in reader:
                    key = {k.lower().replace(" ", "_"): v for k, v in r.items()}
                    rows.append((
                        int(key["area"]), int(key["bedrooms"]), int(key["bathrooms"]),
                        int(key["stories"]), int(key["parking"]), key["mainroad"],
                        key.get("near_market", "no"), key["furnishingstatus"],
                        key["location"], int(key["price"]),
                    ))
            db.executemany(
                """INSERT INTO houses
                   (area, bedrooms, bathrooms, stories, parking, mainroad,
                    near_market, furnishingstatus, location, price)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                rows,
            )
            db.commit()
    db.close()


def log_audit(action, details=""):
    db = get_db()
    db.execute(
        "INSERT INTO audit_log (created_at, admin_username, action, details) VALUES (?,?,?,?)",
        (datetime.now(timezone.utc).isoformat(timespec="seconds"),
         session.get("admin_username", "unknown"), action, details),
    )
    db.commit()


def log_prediction(source, form_like, predicted_price, row_count=1):
    db = get_db()
    db.execute(
        """INSERT INTO prediction_log
           (created_at, source, area, bedrooms, bathrooms, stories, parking,
            mainroad, near_market, furnishingstatus, location, predicted_price, row_count)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            datetime.now(timezone.utc).isoformat(timespec="seconds"), source,
            form_like.get("area"), form_like.get("bedrooms"), form_like.get("bathrooms"),
            form_like.get("stories"), form_like.get("parking"), form_like.get("mainroad"),
            form_like.get("near_market"), form_like.get("furnishingstatus"),
            form_like.get("location"), int(predicted_price), row_count,
        ),
    )
    db.commit()


# =====================================================================
# Admin auth (SRS 2.2 Admin user class, 3.2 Feature 2, 6.3 Security)
# =====================================================================
def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("admin_username"):
            flash("Please sign in to access the admin area.", "error")
            return redirect(url_for("admin_login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        db = get_db()
        row = db.execute("SELECT * FROM admins WHERE username = ?", (username,)).fetchone()
        if row and check_password_hash(row["password_hash"], password):
            session.clear()
            session["admin_username"] = username
            flash("Signed in.", "success")
            return redirect(request.args.get("next") or url_for("admin_dashboard"))
        flash("Invalid username or password.", "error")
    return render_template("admin_login.html")


@app.route("/admin/logout")
def admin_logout():
    session.clear()
    flash("Signed out.", "success")
    return redirect(url_for("admin_login"))


@app.route("/admin")
@admin_required
def admin_dashboard():
    db = get_db()
    houses = db.execute("SELECT * FROM houses ORDER BY id DESC LIMIT 100").fetchall()
    total = db.execute("SELECT COUNT(*) FROM houses").fetchone()[0]
    return render_template("admin_dashboard.html", houses=houses, total=total)


def _house_form_values(form):
    """Cast + validate a house form. Raises ValueError with a friendly
    message on bad input (SRS 3.1.3 / 6.4 Safety: graceful validation)."""
    try:
        return {
            "area": int(form["area"]),
            "bedrooms": int(form["bedrooms"]),
            "bathrooms": int(form["bathrooms"]),
            "stories": int(form["stories"]),
            "parking": int(form["parking"]),
            "mainroad": form["mainroad"].lower(),
            "near_market": form["near_market"].lower(),
            "furnishingstatus": form["furnishingstatus"],
            "location": form["location"],
            "price": int(form["price"]),
        }
    except (KeyError, ValueError):
        raise ValueError("All fields are required and numeric fields must be whole numbers.")


@app.route("/admin/houses/add", methods=["GET", "POST"])
@admin_required
def admin_house_add():
    if request.method == "POST":
        try:
            values = _house_form_values(request.form)
        except ValueError as e:
            flash(str(e), "error")
            return render_template("admin_house_form.html", house=request.form,
                                    locations=LOCATIONS, furnishing=FURNISHING, mode="add")
        db = get_db()
        db.execute(
            """INSERT INTO houses (area, bedrooms, bathrooms, stories, parking,
               mainroad, near_market, furnishingstatus, location, price)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            tuple(values.values()),
        )
        db.commit()
        log_audit("add_house", f"{values['location']}, {values['area']} sqft, ₹{values['price']}")
        flash("Property added.", "success")
        return redirect(url_for("admin_dashboard"))
    return render_template("admin_house_form.html", house=None,
                            locations=LOCATIONS, furnishing=FURNISHING, mode="add")


@app.route("/admin/houses/<int:house_id>/edit", methods=["GET", "POST"])
@admin_required
def admin_house_edit(house_id):
    db = get_db()
    existing = db.execute("SELECT * FROM houses WHERE id = ?", (house_id,)).fetchone()
    if not existing:
        abort(404)
    if request.method == "POST":
        try:
            values = _house_form_values(request.form)
        except ValueError as e:
            flash(str(e), "error")
            return render_template("admin_house_form.html", house=request.form,
                                    locations=LOCATIONS, furnishing=FURNISHING,
                                    mode="edit", house_id=house_id)
        db.execute(
            """UPDATE houses SET area=?, bedrooms=?, bathrooms=?, stories=?, parking=?,
               mainroad=?, near_market=?, furnishingstatus=?, location=?, price=?
               WHERE id=?""",
            tuple(values.values()) + (house_id,),
        )
        db.commit()
        log_audit("edit_house", f"id={house_id}")
        flash("Property updated.", "success")
        return redirect(url_for("admin_dashboard"))
    return render_template("admin_house_form.html", house=existing,
                            locations=LOCATIONS, furnishing=FURNISHING,
                            mode="edit", house_id=house_id)


@app.route("/admin/houses/<int:house_id>/delete", methods=["POST"])
@admin_required
def admin_house_delete(house_id):
    db = get_db()
    db.execute("DELETE FROM houses WHERE id = ?", (house_id,))
    db.commit()
    log_audit("delete_house", f"id={house_id}")
    flash("Property deleted.", "success")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/retrain", methods=["POST"])
@admin_required
def admin_retrain():
    """Simulated retraining (SRS 3.2): recompute location premiums from
    the current houses table. Replace with a real training job — e.g.
    scikit-learn Pipeline.fit() persisted via joblib — when ready."""
    db = get_db()
    rows = db.execute("SELECT location, price FROM houses").fetchall()
    if not rows:
        flash("No records to train on.", "error")
        return redirect(url_for("admin_dashboard"))

    overall_avg = sum(r["price"] for r in rows) / len(rows)
    by_loc = {}
    for r in rows:
        by_loc.setdefault(r["location"], []).append(r["price"])

    for loc, prices in by_loc.items():
        LOCATION_PREMIUM[loc] = round((sum(prices) / len(prices)) / overall_avg, 3)

    log_audit("retrain_model", f"{len(rows)} records, {len(by_loc)} localities")
    flash(f"Model retrained on {len(rows)} records across {len(by_loc)} localities.", "success")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/reports/performance")
@admin_required
def report_performance():
    db = get_db()
    rows = db.execute("SELECT * FROM houses").fetchall()
    errors, rows_out = [], []
    for r in rows:
        predicted = predict_price(
            area=r["area"], bedrooms=r["bedrooms"], bathrooms=r["bathrooms"],
            stories=r["stories"], parking=r["parking"], mainroad=r["mainroad"],
            near_market=r["near_market"], furnishingstatus=r["furnishingstatus"],
            location=r["location"],
        )
        err = predicted - r["price"]
        errors.append(err)
        rows_out.append({"id": r["id"], "location": r["location"], "actual": r["price"],
                          "predicted": predicted, "error": err})

    n = len(errors) or 1
    mae = sum(abs(e) for e in errors) / n
    rmse = (sum(e * e for e in errors) / n) ** 0.5
    mape = sum(abs(e) / rows[i]["price"] for i, e in enumerate(errors)) / n * 100 if rows else 0
    mean_actual = sum(r["price"] for r in rows) / n if rows else 0
    ss_tot = sum((r["price"] - mean_actual) ** 2 for r in rows) or 1
    ss_res = sum(e * e for e in errors)
    r2 = 1 - (ss_res / ss_tot)

    metrics = {"count": len(rows), "mae": mae, "rmse": rmse, "mape": mape, "r2": r2}
    return render_template("admin_report_performance.html", metrics=metrics,
                            rows=sorted(rows_out, key=lambda x: abs(x["error"]), reverse=True)[:25])


@app.route("/admin/reports/updates")
@admin_required
def report_updates():
    db = get_db()
    entries = db.execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT 200").fetchall()
    return render_template("admin_report_updates.html", entries=entries)


@app.route("/admin/reports/predictions")
@admin_required
def report_predictions():
    db = get_db()
    entries = db.execute("SELECT * FROM prediction_log ORDER BY id DESC LIMIT 200").fetchall()
    totals = db.execute(
        "SELECT COUNT(*) AS n, COALESCE(SUM(row_count),0) AS total_rows FROM prediction_log"
    ).fetchone()
    return render_template("admin_report_predictions.html", entries=entries, totals=totals)


@app.route("/admin/reports/predictions/clear", methods=["POST"])
@admin_required
def report_predictions_clear():
    """Data disposal (SRS 4.4.4): purge logged prediction requests."""
    db = get_db()
    db.execute("DELETE FROM prediction_log")
    db.commit()
    log_audit("clear_prediction_log", "cleared input summary report")
    flash("Prediction log cleared.", "success")
    return redirect(url_for("report_predictions"))


# =====================================================================
# Public pricing model
# =====================================================================
def predict_price(area, bedrooms, bathrooms, stories, parking,
                   mainroad, near_market, furnishingstatus, location):
    """Toy pricing model standing in for a trained regressor — replace
    this with model.predict(...) once you load a real .pkl/.joblib file."""
    base = 2_000_000
    price = (
        base
        + float(area) * 2800
        + float(bedrooms) * 350_000
        + float(bathrooms) * 500_000
        + float(stories) * 300_000
        + float(parking) * 250_000
        + (700_000 if str(mainroad).lower() == "yes" else 0)
        + (500_000 if str(near_market).lower() == "yes" else 0)
        + FURNISH_BONUS.get(furnishingstatus, 0)
    )
    price *= LOCATION_PREMIUM.get(location, 1.0)
    return price


def validate_house_fields(data):
    """Per-field validation with friendly messages (SRS 3.1.3, 6.4 Safety).
    `data` is any mapping with .get(). Returns (clean_dict, error_message)."""
    errors = []
    clean = {}

    def positive_int(key, label, min_value=0, max_value=None):
        raw = data.get(key)
        try:
            val = int(float(raw))
        except (TypeError, ValueError):
            errors.append(f"{label} must be a whole number.")
            return None
        if val < min_value or (max_value is not None and val > max_value):
            errors.append(f"{label} must be between {min_value} and {max_value}.")
            return None
        return val

    clean["area"] = positive_int("area", "Area", min_value=100, max_value=20000)
    clean["bedrooms"] = positive_int("bedrooms", "Bedrooms", min_value=1, max_value=15)
    clean["bathrooms"] = positive_int("bathrooms", "Bathrooms", min_value=1, max_value=15)
    clean["stories"] = positive_int("stories", "Stories", min_value=1, max_value=10)
    clean["parking"] = positive_int("parking", "Parking spots", min_value=0, max_value=10)

    mainroad = str(data.get("mainroad", "")).lower()
    if mainroad not in ("yes", "no"):
        errors.append("Main road must be Yes or No.")
    clean["mainroad"] = mainroad

    near_market = str(data.get("near_market", "no")).lower()
    if near_market not in ("yes", "no"):
        errors.append("Near market must be Yes or No.")
    clean["near_market"] = near_market

    furnishingstatus = data.get("furnishingstatus")
    if furnishingstatus not in FURNISHING:
        errors.append("Furnishing must be furnished, semi-furnished, or unfurnished.")
    clean["furnishingstatus"] = furnishingstatus

    location = data.get("location")
    if location not in LOCATIONS:
        errors.append("Locality must be one of the known localities.")
    clean["location"] = location

    return clean, ("; ".join(errors) if errors else None)


def build_prediction(form):
    price = predict_price(**form)
    ranked = sorted([
        ("Area (sq. ft.)", float(form["area"]) * 2800, "area"),
        ("Location", price * (LOCATION_PREMIUM.get(form["location"], 1.0) - 1.0), "loc"),
        ("Furnishing", FURNISH_BONUS.get(form["furnishingstatus"], 0), "furn"),
        ("Stories", float(form["stories"]) * 300_000, "story"),
    ], key=lambda r: abs(r[1]), reverse=True)
    max_impact = max(abs(r[1]) for r in ranked) or 1
    top_features = [
        {
            "name": name,
            "impact": ("+" if val >= 0 else "-") + format_inr(abs(val)),
            "weight": max(10, min(100, int(abs(val) / max_impact * 100))),
        }
        for name, val, _ in ranked[:4]
    ]
    return {
        "price": price,
        "low": price * 0.91,
        "high": price * 1.09,
        "confidence": 90,
        "top_features": top_features,
    }


# =====================================================================
# Public routes (UI unchanged — only validation/logging added)
# =====================================================================
@app.route("/")
def index():
    return render_template("index.html", stats=DATASET_STATS)


@app.route("/predict", methods=["GET", "POST"])
def predict():
    prediction = None
    if request.method == "POST":
        clean, error = validate_house_fields(request.form)
        if error:
            flash(error, "error")
        else:
            prediction = build_prediction(clean)
            log_prediction("single", clean, prediction["price"])

    return render_template("predict.html", prediction=prediction,
                            locations=LOCATIONS, furnishing=FURNISHING)


ALLOWED_UPLOAD_EXT = {".csv", ".xlsx", ".xls"}
REQUIRED_COLUMNS = {"area", "bedrooms", "bathrooms", "stories", "parking",
                     "mainroad", "furnishingstatus", "location"}


@app.route("/predict/batch", methods=["POST"])
def predict_batch():
    file = request.files.get("dataset_file")
    if not file or file.filename == "":
        flash("Choose a CSV or Excel file to upload.", "error")
        return redirect(url_for("predict"))

    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in ALLOWED_UPLOAD_EXT:
        flash("Unsupported file type. Upload a .csv, .xlsx, or .xls file.", "error")
        return redirect(url_for("predict"))

    try:
        df = pd.read_csv(file) if ext == ".csv" else pd.read_excel(file)
    except Exception:
        flash("Couldn't read that file. Make sure it's a valid CSV/Excel export.", "error")
        return redirect(url_for("predict"))

    if df.empty:
        flash("That file has no rows to predict on.", "error")
        return redirect(url_for("predict"))

    df.columns = [c.strip() for c in df.columns]
    col_map = {c.lower().replace(" ", "_"): c for c in df.columns}
    missing = REQUIRED_COLUMNS - set(col_map.keys())
    if missing:
        flash(f"Missing required column(s): {', '.join(sorted(missing))}.", "error")
        return redirect(url_for("predict"))

    near_market_col = col_map.get("near_market")
    bad_rows = []

    def row_price(row):
        clean, error = validate_house_fields({
            "area": row[col_map["area"]], "bedrooms": row[col_map["bedrooms"]],
            "bathrooms": row[col_map["bathrooms"]], "stories": row[col_map["stories"]],
            "parking": row[col_map["parking"]], "mainroad": row[col_map["mainroad"]],
            "near_market": row[near_market_col] if near_market_col else "no",
            "furnishingstatus": row[col_map["furnishingstatus"]],
            "location": row[col_map["location"]],
        })
        if error:
            bad_rows.append(error)
            return None
        return predict_price(**clean)

    df["predicted_price"] = df.apply(row_price, axis=1)
    valid = df.dropna(subset=["predicted_price"])

    if valid.empty:
        flash("None of the rows had valid data to predict on. Check the sample format.", "error")
        return redirect(url_for("predict"))

    if bad_rows:
        flash(f"{len(bad_rows)} row(s) were skipped due to invalid values.", "error")

    result_id = uuid.uuid4().hex[:10]
    result_path = os.path.join(RESULTS_DIR, f"{result_id}.csv")
    valid.to_csv(result_path, index=False)

    log_prediction(
        "batch",
        {"area": None, "bedrooms": None, "bathrooms": None, "stories": None,
         "parking": None, "mainroad": None, "near_market": None,
         "furnishingstatus": None, "location": None},
        valid["predicted_price"].mean(),
        row_count=len(valid),
    )

    preview = valid.head(25).to_dict(orient="records")
    summary = {
        "rows": len(valid),
        "avg_price": valid["predicted_price"].mean(),
        "min_price": valid["predicted_price"].min(),
        "max_price": valid["predicted_price"].max(),
    }
    return render_template(
        "batch_results.html",
        preview=preview,
        columns=list(valid.columns),
        summary=summary,
        filename=file.filename,
        download_id=result_id,
    )


@app.route("/download/<result_id>")
def download_result(result_id):
    safe_name = f"{result_id}.csv"
    if not os.path.isfile(os.path.join(RESULTS_DIR, safe_name)):
        abort(404)
    return send_from_directory(RESULTS_DIR, safe_name, as_attachment=True,
                                download_name="house_price_predictions.csv")


@app.route("/dataset")
def dataset():
    return render_template("dataset.html", stats=DATASET_STATS, locations=LOCATIONS)


init_db()

if __name__ == "__main__":
    app.run(debug=True)