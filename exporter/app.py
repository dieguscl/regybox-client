"""Regybox Data Exporter — simple web app for exporting your personal data."""

import json
import re
import datetime

import requests
from bs4 import BeautifulSoup
from flask import Flask, render_template, request, jsonify

app = Flask(__name__)

BASE_URL = "https://www.regybox.pt/app/app_nova"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "X-Requested-With": "XMLHttpRequest",
}


def fetch_boxes() -> list[dict]:
    """Fetch the list of all boxes from Regybox."""
    r = requests.get(
        f"{BASE_URL}/php/login/escolha_clube.php?lang=en",
        headers=HEADERS,
        timeout=15,
    )
    soup = BeautifulSoup(r.text, "html.parser")
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
    soup = BeautifulSoup(r.text, "html.parser")

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
    soup = BeautifulSoup(r.text, "html.parser")

    fields = {}
    for inp in soup.find_all("input"):
        name = inp.get("name", "") or inp.get("id", "")
        val = inp.get("value", "")
        if val and name and "hidden" not in name and "but_" not in name and not name.startswith("e"):
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
    soup = BeautifulSoup(r.text, "html.parser")
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
    soup = BeautifulSoup(r.text, "html.parser")

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

    # Monthly graph data
    match = re.search(r"valores=([^&]+)", r.text)
    if match:
        vals = match.group(1).split(",")
        match_labels = re.search(r"datas=([^&]+)", r.text)
        if match_labels:
            months = [m.strip("'") for m in match_labels.group(1).split(",")]
            stats["monthly"] = dict(zip(months, [int(v) for v in vals]))

    return stats


def fetch_records(session: requests.Session) -> list[dict]:
    """Fetch personal records."""
    r = session.get(
        f"{BASE_URL}/php/recordes/recordes.php",
        headers=HEADERS,
        timeout=10,
    )
    soup = BeautifulSoup(r.text, "html.parser")

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

    soup = BeautifulSoup(r.text, "html.parser")
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


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
