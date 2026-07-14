import os
import csv
import sqlite3
import uuid
import logging
from datetime import datetime, timezone
from functools import wraps
from typing import Dict, Any, Optional, Tuple, List

import pandas as pd
from flask import (
    Flask, render_template, request, flash, redirect,
    url_for, send_from_directory, abort, session, g
)
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from markupsafe import escape

# Configuration
class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY') or os.urandom(24).hex()
    MAX_CONTENT_LENGTH = 8 * 1024 * 1024  # 8 MB
    ALLOWED_EXTENSIONS = {'csv', 'xlsx', 'xls'}
    
    # Prediction parameters
    BASE_PRICE = 2_000_000
    MIN_PRICE = 500_000
    AREA_PRICE_PER_SQFT = 2800
    BHK_PRICE = 350_000
    FLOOR_PRICE = 50_000
    BALCONY_PRICE = 150_000
    AGE_DEPRECIATION = 25_000
    PARKING_BONUS = 200_000
    MARKET_BONUS = 300_000
    ROAD_BONUS = 200_000
    
    # Database
    DB_PATH = os.path.join(os.path.dirname(__file__), 'instance', 'housing.db')
    RESULTS_DIR = os.path.join(os.path.dirname(__file__), 'instance', 'generated')

# Initialize Flask app
app = Flask(__name__)
app.config.from_object(Config)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Ensure directories exist
os.makedirs(os.path.dirname(Config.DB_PATH), exist_ok=True)
os.makedirs(Config.RESULTS_DIR, exist_ok=True)

# =====================================================================
# Data loading with caching
# =====================================================================
from functools import lru_cache

@lru_cache(maxsize=1)
def load_excel_data():
    """Load unique values from Excel with caching"""
    try:
        df = pd.read_excel('HousePricePredictionindore.xlsx')
        furnishing_values = ['Furnished', 'Semi-Furnished', 'Unfurnished']
        
        return {
            'locations': sorted(df['Location'].unique()),
            'bhk_options': sorted(df['BHK'].unique()),
            'floor_options': sorted(df['Floor'].unique()),
            'balcony_options': sorted(df['Balconies'].unique()),
            'furnishing_options': furnishing_values
        }
    except FileNotFoundError as e:
        logger.warning(f"Excel file not found: {e}")
        return {
            'locations': [
                "Vijay Nagar", "Palasia", "Bhawarkua", "Rajendra Nagar", 
                "Tilak Nagar", "Sudama Nagar", "Scheme 54", "Scheme 78", 
                "MR 10", "Annapurna", "Pipliyahana", "Mhow Road"
            ],
            'bhk_options': [1, 2, 3, 4, 5],
            'floor_options': [0, 1, 2, 3, 4],
            'balcony_options': [0, 1, 2, 3, 4],
            'furnishing_options': ['Furnished', 'Semi-Furnished', 'Unfurnished']
        }

EXCEL_DATA = load_excel_data()
LOCATIONS = EXCEL_DATA['locations']
FURNISHING = EXCEL_DATA['furnishing_options']
BHK_OPTIONS = EXCEL_DATA['bhk_options']
FLOOR_OPTIONS = EXCEL_DATA['floor_options']
BALCONY_OPTIONS = EXCEL_DATA['balcony_options']

# Reference stats
try:
    df = pd.read_excel('HousePricePredictionindore.xlsx')
    DATASET_STATS = {
        "rows": len(df),
        "columns": len(df.columns),
        "localities": len(df['Location'].unique()),
        "price_min": df['Price'].min() * 100000,  # Assuming Price is in lakhs
        "price_max": df['Price'].max() * 100000,
        "price_mean": df['Price'].mean() * 100000,
        "area_mean": df['Area'].mean(),
    }
except:
    DATASET_STATS = {
        "rows": 1000,
        "columns": 10,
        "localities": len(LOCATIONS),
        "price_min": 500_000,
        "price_max": 5_000_000,
        "price_mean": 1_500_000,
        "area_mean": 2500,
    }

# Location premium mapping
LOCATION_PREMIUM = {loc: 1.0 for loc in LOCATIONS}
default_premiums = {
    "Vijay Nagar": 1.15, "Palasia": 1.20, "Bengali Square": 1.25,
    "Super Corridor": 1.30, "Scheme No. 54": 1.18, "Annapurna Road": 1.10,
    "Ring Road": 1.05, "MR-10": 1.12, "AB Road": 1.08
}
for loc, premium in default_premiums.items():
    if loc in LOCATION_PREMIUM:
        LOCATION_PREMIUM[loc] = premium

# Furnishing bonus mapping
FURNISH_BONUS = {
    "Furnished": 900_000,
    "Semi-Furnished": 450_000,
    "Unfurnished": 0
}

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
# Database setup
# =====================================================================
def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(Config.DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
        g.db.execute("PRAGMA journal_mode = WAL")
    return g.db

@app.teardown_appcontext
def close_db(exception=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()

def init_db():
    """Initialize database with schema and indexes"""
    db = sqlite3.connect(Config.DB_PATH)
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
            price INTEGER NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS admins (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_login TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            admin_username TEXT NOT NULL,
            action TEXT NOT NULL,
            details TEXT,
            ip_address TEXT,
            user_agent TEXT
        );

        CREATE TABLE IF NOT EXISTS prediction_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            source TEXT NOT NULL,
            area INTEGER, 
            property_age INTEGER,
            bhk INTEGER,
            floor INTEGER,
            balconies INTEGER,
            parking TEXT,
            near_market TEXT,
            furnishing TEXT,
            location TEXT,
            main_road TEXT,
            predicted_price INTEGER,
            row_count INTEGER DEFAULT 1,
            session_id TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_houses_location ON houses(location);
        CREATE INDEX IF NOT EXISTS idx_houses_price ON houses(price);
        CREATE INDEX IF NOT EXISTS idx_houses_area ON houses(area);
        CREATE INDEX IF NOT EXISTS idx_prediction_log_created ON prediction_log(created_at);
        CREATE INDEX IF NOT EXISTS idx_prediction_log_location ON prediction_log(location);
        CREATE INDEX IF NOT EXISTS idx_audit_log_created ON audit_log(created_at);
        CREATE INDEX IF NOT EXISTS idx_audit_log_admin ON audit_log(admin_username);
        """
    )
    db.commit()

    # Seed default admin
    if db.execute("SELECT COUNT(*) FROM admins").fetchone()[0] == 0:
        db.execute(
            "INSERT INTO admins (username, password_hash) VALUES (?, ?)",
            ("admin", generate_password_hash(os.environ.get('DEFAULT_ADMIN_PASSWORD', 'ChangeMe123'))),
        )
        db.commit()

    # Seed sample data if empty
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

# =====================================================================
# Logging functions
# =====================================================================
def log_audit(action, details="", ip_address=None, user_agent=None):
    db = get_db()
    db.execute(
        """INSERT INTO audit_log 
           (admin_username, action, details, ip_address, user_agent) 
           VALUES (?,?,?,?,?)""",
        (session.get("admin_username", "unknown"), action, details,
         ip_address or request.remote_addr,
         user_agent or request.headers.get('User-Agent', '')),
    )
    db.commit()

def log_prediction(source, form_like, predicted_price, row_count=1):
    db = get_db()
    db.execute(
        """INSERT INTO prediction_log
           (source, area, property_age, bhk, floor, balconies,
            parking, near_market, furnishing, location, main_road, 
            predicted_price, row_count, session_id)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            source,
            form_like.get("area"), form_like.get("property_age"),
            form_like.get("bhk"), form_like.get("floor"), form_like.get("balconies"),
            form_like.get("parking"), form_like.get("near_market"),
            form_like.get("furnishing"), form_like.get("location"),
            form_like.get("main_road"), int(predicted_price), row_count,
            session.get('session_id', 'unknown'),
        ),
    )
    db.commit()

# =====================================================================
# Admin authentication
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
            db.execute(
                "UPDATE admins SET last_login = CURRENT_TIMESTAMP WHERE username = ?",
                (username,)
            )
            db.commit()
            log_audit("login", f"User {username} logged in")
            flash("Signed in successfully.", "success")
            return redirect(request.args.get("next") or url_for("admin_dashboard"))
        log_audit("failed_login", f"Failed login attempt for {username}")
        flash("Invalid username or password.", "error")
    return render_template("admin_login.html")

@app.route("/admin/logout")
def admin_logout():
    if session.get("admin_username"):
        log_audit("logout", f"User {session['admin_username']} logged out")
    session.clear()
    flash("Signed out successfully.", "success")
    return redirect(url_for("admin_login"))

# =====================================================================
# Prediction functions
# =====================================================================
def calculate_price(area: float, bhk: int, floor: int, balconies: int, 
                   property_age: int, parking: str, near_market: str, 
                   main_road: str, furnishing: str, location: str) -> float:
    """Unified price calculation with component breakdown"""
    components = {
        'base': Config.BASE_PRICE,
        'area': float(area) * Config.AREA_PRICE_PER_SQFT,
        'bhk': float(bhk) * Config.BHK_PRICE,
        'floor': float(floor) * Config.FLOOR_PRICE,
        'balconies': float(balconies) * Config.BALCONY_PRICE,
        'age_depreciation': -float(property_age) * Config.AGE_DEPRECIATION,
        'parking': Config.PARKING_BONUS if str(parking).lower() == "yes" else 0,
        'furnishing': FURNISH_BONUS.get(furnishing, 0),
        'market': Config.MARKET_BONUS if str(near_market).lower() == "yes" else 0,
        'road': Config.ROAD_BONUS if str(main_road).lower() == "yes" else 0,
    }
    
    total = sum(components.values()) * LOCATION_PREMIUM.get(location, 1.0)
    return max(total, Config.MIN_PRICE)

def validate_house_fields(data: Dict) -> Tuple[Dict, Optional[str]]:
    """Per-field validation with detailed error messages"""
    errors = []
    clean = {}
    
    def get_field(key):
        value = data.get(key)
        if value is None:
            return None
        return str(value).strip() if isinstance(value, str) else value
    
    def positive_int(key, label, min_value=0, max_value=None):
        raw = get_field(key)
        if raw is None or raw == '':
            errors.append(f"{label} is required.")
            return None
        try:
            val = int(float(raw))
        except (TypeError, ValueError):
            errors.append(f"{label} must be a whole number.")
            return None
        if val < min_value or (max_value is not None and val > max_value):
            errors.append(f"{label} must be between {min_value} and {max_value}.")
            return None
        return val
    
    # Validate numeric fields
    clean["area"] = positive_int("area", "Area", min_value=100, max_value=20000)
    clean["property_age"] = positive_int("property_age", "Property Age", min_value=0, max_value=50)
    clean["bhk"] = positive_int("bhk", "BHK", min_value=1, max_value=10)
    clean["floor"] = positive_int("floor", "Floor", min_value=0, max_value=10)
    clean["balconies"] = positive_int("balconies", "Balconies", min_value=0, max_value=10)
    
    # Validate location
    location = get_field("location")
    if location not in LOCATIONS:
        errors.append(f"Locality must be one of the known localities.")
    clean["location"] = location
    
    # Validate boolean fields
    parking = str(get_field("parking") or "").lower()
    if parking not in ("yes", "no"):
        errors.append("Parking must be Yes or No.")
    clean["parking"] = parking
    
    near_market = str(get_field("near_market") or "").lower()
    if near_market not in ("yes", "no"):
        errors.append("Near market must be Yes or No.")
    clean["near_market"] = near_market
    
    main_road = str(get_field("main_road") or "").lower()
    if main_road not in ("yes", "no"):
        errors.append("Main road must be Yes or No.")
    clean["main_road"] = main_road
    
    # Validate furnishing
    furnishing = get_field("furnishing")
    if furnishing:
        furnishing = furnishing.strip()
        furnishing_map = {
            "non-furnished": "Unfurnished",
            "unfurnished": "Unfurnished",
            "semi furnished": "Semi-Furnished",
            "semi-furnished": "Semi-Furnished",
            "furnished": "Furnished"
        }
        furnishing = furnishing_map.get(furnishing.lower(), furnishing)
    
    if furnishing not in FURNISHING:
        errors.append(f"Furnishing must be one of: {', '.join(FURNISHING)}.")
    clean["furnishing"] = furnishing
    
    return clean, ("; ".join(errors) if errors else None)

def build_prediction(form: Dict) -> Dict:
    """Build prediction with feature importance analysis"""
    price = calculate_price(**form)
    
    # Calculate feature impacts for explanation
    impacts = [
        ("Area", float(form["area"]) * Config.AREA_PRICE_PER_SQFT, "area"),
        ("Location", price * (LOCATION_PREMIUM.get(form["location"], 1.0) - 1.0), "loc"),
        ("BHK", float(form["bhk"]) * Config.BHK_PRICE, "bhk"),
        ("Furnishing", FURNISH_BONUS.get(form["furnishing"], 0), "furn"),
        ("Property Age", -float(form["property_age"]) * Config.AGE_DEPRECIATION, "age"),
        ("Balconies", float(form["balconies"]) * Config.BALCONY_PRICE, "bal"),
        ("Parking", Config.PARKING_BONUS if str(form["parking"]).lower() == "yes" else 0, "park"),
        ("Near Market", Config.MARKET_BONUS if str(form["near_market"]).lower() == "yes" else 0, "market"),
        ("Main Road", Config.ROAD_BONUS if str(form["main_road"]).lower() == "yes" else 0, "road"),
    ]
    
    # Sort by absolute impact
    ranked = sorted(impacts, key=lambda r: abs(r[1]), reverse=True)
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
# Public Routes
# =====================================================================
@app.route("/")
def index():
    return render_template("index.html", stats=DATASET_STATS)

@app.route("/predict", methods=["GET", "POST"])
def predict():
    prediction = None
    errors = {}
    
    if request.method == "POST":
        clean, error = validate_house_fields(request.form)
        if error:
            flash(error, "error")
            for field in ['area', 'property_age', 'bhk', 'floor', 'balconies', 
                         'location', 'parking', 'near_market', 'main_road', 'furnishing']:
                if field in request.form and not request.form[field]:
                    errors[field] = "This field is required"
        else:
            prediction = build_prediction(clean)
            log_prediction("single", clean, prediction["price"])
            flash("Prediction completed successfully!", "success")

    return render_template("predict.html", 
                         prediction=prediction,
                         locations=LOCATIONS,
                         bhk_options=BHK_OPTIONS,
                         floor_options=FLOOR_OPTIONS,
                         balcony_options=BALCONY_OPTIONS,
                         furnishing_options=FURNISHING,
                         errors=errors,
                         form_data=request.form if request.method == "POST" else None)

def allowed_file(filename):
    """Check if file has allowed extension"""
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in Config.ALLOWED_EXTENSIONS

REQUIRED_COLUMNS = {"Location", "Area", "BHK", "Floor", "Balconies", 
                    "Property Age", "Parking", "Near Market", "Main Road", "Furnishing"}

@app.route("/predict/batch", methods=["POST"])
def predict_batch():
    file = request.files.get("dataset_file")
    if not file or file.filename == "":
        flash("Choose a CSV or Excel file to upload.", "error")
        return redirect(url_for("predict"))

    if not allowed_file(file.filename):
        flash("Unsupported file type. Upload a .csv, .xlsx, or .xls file.", "error")
        return redirect(url_for("predict"))

    filename = secure_filename(file.filename)
    
    try:
        if filename.endswith('.csv'):
            df = pd.read_csv(file)
        else:
            df = pd.read_excel(file)
    except Exception as e:
        logger.error(f"Error reading file: {e}")
        flash(f"Couldn't read that file: {str(e)}", "error")
        return redirect(url_for("predict"))

    if df.empty:
        flash("That file has no rows to predict on.", "error")
        return redirect(url_for("predict"))

    df.columns = [c.strip() for c in df.columns]
    missing = REQUIRED_COLUMNS - set(df.columns)
    if missing:
        flash(f"Missing required column(s): {', '.join(sorted(missing))}.", "error")
        return redirect(url_for("predict"))

    bad_rows = []

    def process_row(row):
        clean, error = validate_house_fields({
            "area": row["Area"],
            "property_age": row["Property Age"],
            "location": row["Location"],
            "bhk": row["BHK"],
            "floor": row["Floor"],
            "balconies": row["Balconies"],
            "parking": row["Parking"],
            "furnishing": row["Furnishing"],
            "near_market": row["Near Market"],
            "main_road": row["Main Road"],
        })
        if error:
            bad_rows.append({"row": row.to_dict(), "error": error})
            return None
        return calculate_price(**clean)

    df["predicted_price"] = df.apply(process_row, axis=1)
    valid = df.dropna(subset=["predicted_price"])

    if valid.empty:
        flash("None of the rows had valid data to predict on. Check the sample format.", "error")
        return redirect(url_for("predict"))

    if bad_rows:
        flash(f"{len(bad_rows)} row(s) were skipped due to invalid values.", "warning")

    result_id = uuid.uuid4().hex[:10]
    result_path = os.path.join(Config.RESULTS_DIR, f"{result_id}.csv")
    valid.to_csv(result_path, index=False)

    log_prediction(
        "batch",
        {"area": None, "property_age": None, "location": None, "bhk": None, 
         "floor": None, "balconies": None, "parking": None, 
         "furnishing": None, "near_market": None, "main_road": None},
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
        filename=filename,
        download_id=result_id,
        bad_rows=bad_rows[:10] if bad_rows else []
    )

@app.route("/download/<result_id>")
def download_result(result_id):
    safe_name = f"{result_id}.csv"
    file_path = os.path.join(Config.RESULTS_DIR, safe_name)
    if not os.path.isfile(file_path):
        abort(404)
    return send_from_directory(
        Config.RESULTS_DIR, 
        safe_name, 
        as_attachment=True,
        download_name="house_price_predictions.csv"
    )

@app.route("/dataset")
def dataset():
    return render_template("dataset.html", stats=DATASET_STATS, locations=LOCATIONS)

# =====================================================================
# Admin Routes
# =====================================================================
@app.route("/admin")
@admin_required
def admin_dashboard():
    page = request.args.get('page', 1, type=int)
    per_page = 20
    offset = (page - 1) * per_page
    
    db = get_db()
    total = db.execute("SELECT COUNT(*) FROM houses").fetchone()[0]
    houses = db.execute(
        """SELECT * FROM houses 
           ORDER BY id DESC 
           LIMIT ? OFFSET ?""",
        (per_page, offset)
    ).fetchall()
    
    return render_template(
        "admin_dashboard.html", 
        houses=houses, 
        total=total,
        page=page,
        total_pages=(total + per_page - 1) // per_page
    )

def _house_form_values(form):
    """Extract and validate house form values"""
    errors = []
    result = {}
    
    required_fields = ["area", "bedrooms", "bathrooms", "stories", 
                      "parking", "mainroad", "near_market", 
                      "furnishingstatus", "location", "price"]
    
    for field in required_fields:
        if field not in form or not form[field]:
            errors.append(f"{field} is required")
    
    if errors:
        raise ValueError("; ".join(errors))
    
    try:
        result = {
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
    except (KeyError, ValueError) as e:
        raise ValueError(f"Invalid data: {str(e)}")
    
    return result

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
        flash("Property added successfully.", "success")
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
        flash("Property updated successfully.", "success")
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
    flash("Property deleted successfully.", "success")
    return redirect(url_for("admin_dashboard"))

@app.route("/admin/retrain", methods=["POST"])
@admin_required
def admin_retrain():
    """Retrain location premiums from the current houses table"""
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
    if not rows:
        flash("No data available for performance report.", "error")
        return redirect(url_for("admin_dashboard"))
    
    errors, rows_out = [], []
    for r in rows:
        # Use the unified prediction function
        predicted = calculate_price(
            area=r["area"], 
            bhk=r["bedrooms"],  # bedrooms maps to BHK
            floor=0,  # default floor
            balconies=0,  # default balconies
            property_age=0,  # default age
            parking="yes" if r["parking"] > 0 else "no",
            near_market=r["near_market"],
            main_road=r["mainroad"],
            furnishing=r["furnishingstatus"],
            location=r["location"],
        )
        err = predicted - r["price"]
        errors.append(err)
        rows_out.append({
            "id": r["id"], 
            "location": r["location"], 
            "actual": r["price"],
            "predicted": predicted, 
            "error": err
        })

    n = len(errors) or 1
    mae = sum(abs(e) for e in errors) / n
    rmse = (sum(e * e for e in errors) / n) ** 0.5
    mape = sum(abs(e) / rows[i]["price"] for i, e in enumerate(errors)) / n * 100 if rows else 0
    mean_actual = sum(r["price"] for r in rows) / n if rows else 0
    ss_tot = sum((r["price"] - mean_actual) ** 2 for r in rows) or 1
    ss_res = sum(e * e for e in errors)
    r2 = 1 - (ss_res / ss_tot)

    metrics = {"count": len(rows), "mae": mae, "rmse": rmse, "mape": mape, "r2": r2}
    return render_template("admin_report_performance.html", 
                          metrics=metrics,
                          rows=sorted(rows_out, key=lambda x: abs(x["error"]), reverse=True)[:25])

@app.route("/admin/reports/updates")
@admin_required
def report_updates():
    page = request.args.get('page', 1, type=int)
    per_page = 50
    offset = (page - 1) * per_page
    
    db = get_db()
    total = db.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
    entries = db.execute(
        """SELECT * FROM audit_log 
           ORDER BY id DESC 
           LIMIT ? OFFSET ?""",
        (per_page, offset)
    ).fetchall()
    
    return render_template(
        "admin_report_updates.html", 
        entries=entries,
        page=page,
        total_pages=(total + per_page - 1) // per_page,
        total=total
    )

@app.route("/admin/reports/predictions")
@admin_required
def report_predictions():
    db = get_db()
    entries = db.execute(
        """SELECT * FROM prediction_log 
           ORDER BY id DESC 
           LIMIT 200"""
    ).fetchall()
    totals = db.execute(
        "SELECT COUNT(*) AS n, COALESCE(SUM(row_count),0) AS total_rows FROM prediction_log"
    ).fetchone()
    return render_template("admin_report_predictions.html", entries=entries, totals=totals)

@app.route("/admin/reports/predictions/clear", methods=["POST"])
@admin_required
def report_predictions_clear():
    db = get_db()
    db.execute("DELETE FROM prediction_log")
    db.commit()
    log_audit("clear_prediction_log", "cleared input summary report")
    flash("Prediction log cleared.", "success")
    return redirect(url_for("report_predictions"))

# =====================================================================
# Error handlers
# =====================================================================
@app.errorhandler(404)
def page_not_found(e):
    return render_template("404.html"), 404

@app.errorhandler(500)
def internal_server_error(e):
    logger.error(f"Internal server error: {e}")
    return render_template("500.html"), 500

# =====================================================================
# Run the app
# =====================================================================

# Initialize database on startup
init_db()

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)