"""Regybox Data Exporter — simple web app for exporting your personal data."""

import json
import re
import datetime

import requests
from bs4 import BeautifulSoup
from flask import Flask, render_template, request, jsonify

app = Flask(__name__)

BASE_URL = "https://www.regybox.pt/app/app_nova"
ADMIN_BASE_URL = "https://www.regybox.pt/admin2"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "X-Requested-With": "XMLHttpRequest",
}


def _fix_encoding(r: requests.Response) -> str:
    """Regybox serves UTF-8 content but doesn't declare it. Force UTF-8."""
    r.encoding = "utf-8"
    return r.text


def fetch_boxes() -> list[dict]:
    """Fetch the list of all boxes from Regybox."""
    r = requests.get(
        f"{BASE_URL}/php/login/escolha_clube.php?lang=en",
        headers=HEADERS,
        timeout=15,
    )
    soup = BeautifulSoup(_fix_encoding(r), "html.parser")
    boxes = []
    for div in soup.find_all("div", onclick=lambda x: x and "abre_login" in x):
        box_id = div["onclick"].split("'")[1]
        title_div = div.find("div", class_="item-title")
        name = title_div.text.strip() if title_div else ""
        if name:
            boxes.append({"id": box_id, "name": name})
    return boxes


def login(session: requests.Session, email: str, password: str, box_id: str) -> str:
    """Login and return the user token."""
    session.get(f"{BASE_URL}/login.php?registo=en", headers=HEADERS, timeout=10)

    r = session.get(
        f"{BASE_URL}/php/login/login.php",
        params={"id": box_id, "lang": "en", "tipo": "", "registo": "en"},
        headers=HEADERS,
        timeout=10,
    )
    soup = BeautifulSoup(_fix_encoding(r), "html.parser")

    acs_input = soup.find("input", {"name": "acs"})
    if not acs_input:
        raise RuntimeError("Could not load login form")
    acs = acs_input["value"]

    login_field = None
    password_field = None
    for inp in soup.find_all("input"):
        name = inp.get("name", "")
        if name.startswith("login") and name != "login":
            login_field = name
        if name.startswith("password"):
            password_field = name

    if not login_field or not password_field:
        raise RuntimeError("Could not find login fields")

    r = session.post(
        f"{BASE_URL}/php/login/scripts/verifica_acesso.php",
        params={"id": box_id, "lang": "en", "tipo": "", "registo": "en"},
        data={
            "id_box": box_id,
            "login": "",
            "acs": acs,
            login_field: email,
            password_field: password,
        },
        headers=HEADERS,
        timeout=15,
    )

    if "Access denied" in r.text or "Acesso negado" in r.text:
        raise RuntimeError("Wrong email or password")

    match = re.search(r"z=([^&\"]+)", r.text)
    if not match:
        raise RuntimeError("Login failed")

    token = match.group(1)

    session.get(
        f"{BASE_URL}/set_session.php",
        params={"z": token, "id": box_id, "lang": "en", "tipo": ""},
        headers=HEADERS,
        timeout=10,
    )

    return token


def fetch_profile(session: requests.Session) -> dict:
    """Fetch personal data."""
    r = session.get(
        f"{BASE_URL}/php/configuracoes/dados_pessoais.php",
        headers=HEADERS,
        timeout=10,
    )
    soup = BeautifulSoup(_fix_encoding(r), "html.parser")

    fields = {}
    for inp in soup.find_all("input"):
        name = inp.get("name", "") or inp.get("id", "")
        val = inp.get("value", "")
        if val and name and "hidden" not in name and "but_" not in name and name not in ("estado",):
            fields[name] = val

    for sel in soup.find_all("select"):
        name = sel.get("name", "") or sel.get("id", "")
        selected = sel.find("option", selected=True)
        if selected:
            fields[name] = selected.text.strip()

    return {
        "name": fields.get("nome_abreviado", ""),
        "nickname": fields.get("nickname", ""),
        "email": fields.get("email", ""),
        "dob": fields.get("data_nasc", ""),
        "phone": fields.get("telemovel", ""),
        "emergency_contact": fields.get("tel_emergencia", ""),
        "profession": fields.get("profissao", ""),
        "address": fields.get("morada", ""),
        "city": fields.get("localidade", ""),
        "postal_code": fields.get("codigo_postal", ""),
        "country": fields.get("pais", ""),
        "gender": fields.get("sexo", ""),
        "tshirt_size": fields.get("tshirt", ""),
        "weight_kg": fields.get("peso", ""),
        "height_cm": fields.get("altura", ""),
    }


def fetch_account(session: requests.Session) -> dict:
    """Fetch account/plan info."""
    r = session.get(
        f"{BASE_URL}/php/configuracoes/sua_conta.php",
        headers=HEADERS,
        timeout=10,
    )
    soup = BeautifulSoup(_fix_encoding(r), "html.parser")
    text = soup.get_text(separator="|", strip=True)

    plan = {}
    for pair in [
        ("payment", "Pagamento|", "|"),
        ("price", "Valor|", "|"),
        ("validity", "Validade|", "|"),
        ("frequency", "Frequência|", "|"),
    ]:
        try:
            start = text.index(pair[1]) + len(pair[1])
            end = text.index(pair[2], start)
            plan[pair[0]] = text[start:end].strip()
        except ValueError:
            pass

    # Allowed class types
    try:
        marker = "permite marcar|"
        start = text.index(marker) + len(marker)
        end = text.index("Eliminar", start)
        classes_str = text[start:end]
        plan["allowed_classes"] = [c.strip() for c in classes_str.split("|") if c.strip()]
    except ValueError:
        plan["allowed_classes"] = []

    return plan


def fetch_attendance(session: requests.Session) -> dict:
    """Fetch attendance stats from history page."""
    r = session.get(
        f"{BASE_URL}/php/aulas/historico_aulas.php",
        headers=HEADERS,
        timeout=10,
    )
    text = _fix_encoding(r)
    soup = BeautifulSoup(text, "html.parser")

    stats = {}
    labels = {"Inscri": "total_signups", "Presen": "total_attended", "Falta": "total_absences"}
    # Stats are in large font divs following letra_small label divs
    small_divs = soup.find_all("div", class_="letra_small")
    for div in small_divs:
        label = div.text.strip()
        for pt_prefix, key in labels.items():
            if pt_prefix in label:
                val_div = div.find_next_sibling("div")
                if not val_div:
                    val_div = div.find_next("div", style=lambda s: s and "font-size" in s)
                if val_div:
                    try:
                        stats[key] = int(val_div.text.strip())
                    except ValueError:
                        pass

    # Monthly graph data — convert month abbreviations to YYYY-MM keys
    PT_MONTHS = {
        "Jan": 1, "Fev": 2, "Mar": 3, "Abr": 4, "Mai": 5, "Jun": 6,
        "Jul": 7, "Ago": 8, "Set": 9, "Out": 10, "Nov": 11, "Dez": 12,
    }
    match = re.search(r"valores=([^&]+)", text)
    if match:
        vals = match.group(1).split(",")
        match_labels = re.search(r"datas=([^&]+)", text)
        if match_labels:
            month_abbrs = [m.strip("'") for m in match_labels.group(1).split(",")]
            # The chart shows the last 12 months ending at the current month
            now = datetime.date.today()
            monthly = {}
            for i, (abbr, val) in enumerate(zip(month_abbrs, vals)):
                month_num = PT_MONTHS.get(abbr, i + 1)
                # Walk backwards: last entry = current month, first = 11 months ago
                months_ago = len(month_abbrs) - 1 - i
                d = now.replace(day=1) - datetime.timedelta(days=months_ago * 28)
                # Snap to the correct month
                d = d.replace(day=1)
                while d.month != month_num:
                    d -= datetime.timedelta(days=28)
                    d = d.replace(day=1)
                key = f"{d.year}-{d.month:02d}"
                monthly[key] = int(val)
            stats["monthly"] = monthly

    return stats


def fetch_records(session: requests.Session) -> list[dict]:
    """Fetch personal records."""
    r = session.get(
        f"{BASE_URL}/php/recordes/recordes.php",
        headers=HEADERS,
        timeout=10,
    )
    soup = BeautifulSoup(_fix_encoding(r), "html.parser")

    records = []
    for item in soup.find_all(class_="item-inner"):
        title = item.find(class_="item-title")
        after = item.find(class_="item-after")
        if title:
            name = title.get_text(strip=True)
            value = after.get_text(strip=True) if after else ""
            if name and value:
                try:
                    records.append({"movement": name, "value": float(value), "unit": "kg"})
                except ValueError:
                    records.append({"movement": name, "value": value, "unit": ""})

    return records


def fetch_classes(session: requests.Session, token: str, date: datetime.date) -> list[dict]:
    """Fetch classes for a given date."""
    ts = int(datetime.datetime(date.year, date.month, date.day, 12, 0).timestamp()) * 1000

    r = session.get(
        f"{BASE_URL}/php/aulas/aulas.php",
        params={
            "valor1": ts, "type": "", "source": "mes",
            "scroll": "s", "box": "", "plano": "0", "z": token,
        },
        headers=HEADERS,
        timeout=15,
    )

    soup = BeautifulSoup(_fix_encoding(r), "html.parser")
    classes = []
    for div in soup.find_all("div", class_="filtro0"):
        try:
            name_div = div.find("div", attrs={"align": "left", "class": "col-50"})
            time_div = div.find("div", attrs={"align": "left", "class": "col"})
            cap_div = div.find("div", attrs={"align": "center", "class": "col"})

            name = name_div.text.strip() if name_div else ""
            time_str = time_div.text.strip() if time_div else ""
            cap_text = cap_div.text.strip() if cap_div else "0 de 0"
            cap_parts = cap_text.split()

            is_enrolled = bool(div.find("div", attrs={"class": "ok_color"})) or bool(
                div.find("button", class_=lambda c: c and "color-red" in c)
            )

            classes.append({
                "name": name,
                "time": time_str,
                "capacity": f"{cap_parts[0]}/{cap_parts[-1]}",
                "enrolled": is_enrolled,
            })
        except Exception:
            continue

    return classes


def admin_login(session: requests.Session, email: str, password: str, box_id: str) -> None:
    """Authenticate through the admin panel login form."""
    # Load the admin login page to get form structure
    r = session.get(
        f"{ADMIN_BASE_URL}/modulos/login/login.php",
        headers=HEADERS,
        timeout=15,
    )
    soup = BeautifulSoup(_fix_encoding(r), "html.parser")

    # Find the obfuscated form fields (db_xyz, email_xyz pattern)
    db_field = None
    email_field = None
    for inp in soup.find_all("input"):
        name = inp.get("name", "")
        if name.startswith("db_"):
            db_field = name
        elif name.startswith("email_"):
            email_field = name

    for sel in soup.find_all("select"):
        name = sel.get("name", "")
        if name.startswith("db_"):
            db_field = name

    if not email_field:
        raise RuntimeError("Could not find admin login fields")

    # Build login payload
    payload = {
        "lingua": "en",
        email_field: email,
        "password": password,
    }
    if db_field:
        payload[db_field] = box_id

    r = session.post(
        f"{ADMIN_BASE_URL}/modulos/login/login.php",
        data=payload,
        headers=HEADERS,
        timeout=15,
        allow_redirects=True,
    )

    text = _fix_encoding(r)
    if "login.php" in r.url and ("erro" in text.lower() or "error" in text.lower()):
        raise RuntimeError("Admin login failed — wrong credentials or unauthorized")

    # Verify we got past login by checking for typical admin page elements
    if "login" in r.url.lower() and "modulos" not in text.lower():
        raise RuntimeError("Admin login failed — could not access admin panel")


def _safe_parse(html: str, parser_fn, label: str) -> dict:
    """Try to parse HTML; on failure, return raw HTML for debugging."""
    try:
        return {"data": parser_fn(html), "raw_html": None}
    except Exception as e:
        return {"data": None, "raw_html": html, "parse_error": f"{label}: {str(e)}"}


def fetch_admin_members(session: requests.Session) -> dict:
    """Scrape the members list from the admin panel."""
    r = session.get(
        f"{ADMIN_BASE_URL}/modulos/membros/membros.php",
        headers=HEADERS,
        timeout=20,
    )
    html = _fix_encoding(r)

    def parse(html):
        soup = BeautifulSoup(html, "html.parser")
        members = []
        # Look for table rows with member data
        for table in soup.find_all("table"):
            rows = table.find_all("tr")
            if len(rows) < 2:
                continue
            headers = [th.get_text(strip=True) for th in rows[0].find_all(["th", "td"])]
            for row in rows[1:]:
                cells = [td.get_text(strip=True) for td in row.find_all("td")]
                if cells:
                    entry = dict(zip(headers, cells)) if headers else {"fields": cells}
                    members.append(entry)
        if not members:
            # Fallback: look for list-style member items
            for item in soup.find_all(class_=re.compile(r"member|membro|item|row", re.I)):
                text = item.get_text(strip=True)
                if text:
                    members.append({"text": text})
        if not members:
            raise ValueError("No member data found in page")
        return members

    return _safe_parse(html, parse, "members")


def fetch_admin_plans(session: requests.Session) -> dict:
    """Scrape plan/pricing configuration from the admin panel."""
    r = session.get(
        f"{ADMIN_BASE_URL}/modulos/planos/planos.php",
        headers=HEADERS,
        timeout=20,
    )
    html = _fix_encoding(r)

    def parse(html):
        soup = BeautifulSoup(html, "html.parser")
        plans = []
        for table in soup.find_all("table"):
            rows = table.find_all("tr")
            if len(rows) < 2:
                continue
            headers = [th.get_text(strip=True) for th in rows[0].find_all(["th", "td"])]
            for row in rows[1:]:
                cells = [td.get_text(strip=True) for td in row.find_all("td")]
                if cells:
                    entry = dict(zip(headers, cells)) if headers else {"fields": cells}
                    plans.append(entry)
        if not plans:
            # Fallback: look for card/item elements
            for item in soup.find_all(class_=re.compile(r"plan|plano|item|card", re.I)):
                text = item.get_text(strip=True)
                if text:
                    plans.append({"text": text})
        if not plans:
            raise ValueError("No plan data found in page")
        return plans

    return _safe_parse(html, parse, "plans")


def fetch_admin_config(session: requests.Session) -> dict:
    """Scrape box configuration/settings from the admin panel."""
    r = session.get(
        f"{ADMIN_BASE_URL}/modulos/configuracoes/configuracoes.php",
        headers=HEADERS,
        timeout=20,
    )
    html = _fix_encoding(r)

    def parse(html):
        soup = BeautifulSoup(html, "html.parser")
        config = {}
        # Extract form fields (inputs and selects)
        for inp in soup.find_all("input"):
            name = inp.get("name", "") or inp.get("id", "")
            val = inp.get("value", "")
            itype = inp.get("type", "text")
            if name and itype not in ("hidden", "submit", "button"):
                config[name] = val
        for sel in soup.find_all("select"):
            name = sel.get("name", "") or sel.get("id", "")
            selected = sel.find("option", selected=True)
            if name and selected:
                config[name] = selected.get_text(strip=True)
        # Extract text blocks / labeled values
        for label in soup.find_all("label"):
            key = label.get_text(strip=True)
            sibling = label.find_next_sibling()
            if sibling and key:
                val = sibling.get_text(strip=True)
                if val:
                    config[key] = val
        if not config:
            raise ValueError("No configuration data found in page")
        return config

    return _safe_parse(html, parse, "config")


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/boxes")
def api_boxes():
    try:
        boxes = fetch_boxes()
        return jsonify(boxes)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/export", methods=["POST"])
def api_export():
    data = request.json
    email = data.get("email", "")
    password = data.get("password", "")
    box_id = data.get("box_id", "")

    if not email or not password or not box_id:
        return jsonify({"error": "Email, password, and box are required"}), 400

    try:
        session = requests.Session()
        token = login(session, email, password, box_id)

        profile = fetch_profile(session)
        account = fetch_account(session)
        attendance = fetch_attendance(session)
        records = fetch_records(session)

        # Fetch this week's classes to show current enrollments
        today = datetime.date.today()
        week_classes = {}
        for i in range(7):
            d = today + datetime.timedelta(days=i)
            day_classes = fetch_classes(session, token, d)
            if day_classes:
                week_classes[d.isoformat()] = day_classes

        export = {
            "exported_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "source": "regybox",
            "profile": profile,
            "account": account,
            "attendance": attendance,
            "records": records,
            "upcoming_classes": week_classes,
        }

        return jsonify(export)

    except RuntimeError as e:
        return jsonify({"error": str(e)}), 401
    except Exception as e:
        return jsonify({"error": f"Export failed: {str(e)}"}), 500


@app.route("/api/admin-export", methods=["POST"])
def api_admin_export():
    data = request.json
    email = data.get("email", "")
    password = data.get("password", "")
    box_id = data.get("box_id", "")

    if not email or not password:
        return jsonify({"error": "Email and password are required"}), 400

    try:
        session = requests.Session()
        admin_login(session, email, password, box_id)

        members = fetch_admin_members(session)
        plans = fetch_admin_plans(session)
        config = fetch_admin_config(session)

        # Check if any section fell back to raw HTML
        has_raw = any(
            section.get("raw_html") for section in [members, plans, config]
        )

        export = {
            "exported_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "source": "regybox_admin",
            "members": members,
            "plans": plans,
            "config": config,
        }

        if has_raw:
            export["_note"] = (
                "Some sections could not be parsed and include raw HTML. "
                "Share this export with the developer to improve parsing."
            )

        return jsonify(export)

    except RuntimeError as e:
        return jsonify({"error": str(e)}), 401
    except Exception as e:
        return jsonify({"error": f"Admin export failed: {str(e)}"}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
