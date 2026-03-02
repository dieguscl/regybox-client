"""Regybox API client for CrossFit O Covil (box 168).

Handles login with anti-bot dynamic fields, session management,
class listing, enrollment, and cancellation.
"""

import re
import time
import datetime
import os

import requests
from bs4 import BeautifulSoup, Tag
from dotenv import load_dotenv

load_dotenv()

BASE_URL = "https://www.regybox.pt/app/app_nova"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "X-Requested-With": "XMLHttpRequest",
}


class RegyboxClient:
    def __init__(self, email: str = "", password: str = "", box_id: str = ""):
        self.email = email or os.environ["REGYBOX_EMAIL"]
        self.password = password or os.environ["REGYBOX_PASSWORD"]
        self.box_id = box_id or os.environ["REGYBOX_BOX_ID"]
        self.session = requests.Session()
        self.user_token: str | None = None

    def login(self) -> str:
        """Authenticate and establish a session. Returns the user token."""
        # Step 1: Init PHP session
        self.session.get(f"{BASE_URL}/login.php?registo=en", headers=HEADERS, timeout=10)

        # Step 2: Load login form to get anti-bot tokens
        r = self.session.get(
            f"{BASE_URL}/php/login/login.php",
            params={"id": self.box_id, "lang": "en", "tipo": "", "registo": "en"},
            headers=HEADERS,
            timeout=10,
        )
        soup = BeautifulSoup(r.text, "html.parser")

        acs = soup.find("input", {"name": "acs"})["value"]
        login_field = None
        password_field = None
        for inp in soup.find_all("input"):
            name = inp.get("name", "")
            if name.startswith("login") and name != "login":
                login_field = name
            if name.startswith("password"):
                password_field = name

        if not login_field or not password_field:
            raise RuntimeError("Could not find dynamic login fields")

        # Step 3: Submit login
        r = self.session.post(
            f"{BASE_URL}/php/login/scripts/verifica_acesso.php",
            params={"id": self.box_id, "lang": "en", "tipo": "", "registo": "en"},
            data={
                "id_box": self.box_id,
                "login": "",  # honeypot — must be empty
                "acs": acs,
                login_field: self.email,
                password_field: self.password,
            },
            headers=HEADERS,
            timeout=15,
        )

        if "Access denied" in r.text or "Acesso negado" in r.text:
            raise RuntimeError("Login failed: wrong email or password")

        match = re.search(r"z=([^&\"]+)", r.text)
        if not match:
            raise RuntimeError(f"Login failed: unexpected response: {r.text[:200]}")

        self.user_token = match.group(1)

        # Step 4: Establish session cookies
        self.session.get(
            f"{BASE_URL}/set_session.php",
            params={"z": self.user_token, "id": self.box_id, "lang": "en", "tipo": ""},
            headers=HEADERS,
            timeout=10,
        )

        return self.user_token

    def _require_login(self):
        if not self.user_token:
            raise RuntimeError("Not logged in. Call login() first.")

    def get_classes(self, date: datetime.date | None = None) -> list[dict]:
        """Fetch classes for a given date (defaults to today).

        Returns a list of dicts with keys:
            name, details, time, capacity, max_capacity, is_open, is_enrolled,
            is_over, class_id, enroll_url, unenroll_url
        """
        self._require_login()
        if date is None:
            date = datetime.date.today()

        ts = int(datetime.datetime(date.year, date.month, date.day, 12, 0).timestamp()) * 1000

        r = self.session.get(
            f"{BASE_URL}/php/aulas/aulas.php",
            params={
                "valor1": ts,
                "type": "",
                "source": "mes",
                "scroll": "s",
                "box": "",
                "plano": "0",
                "z": self.user_token,
            },
            headers=HEADERS,
            timeout=15,
        )
        r.raise_for_status()

        soup = BeautifulSoup(r.text, "html.parser")
        class_divs = soup.find_all("div", class_="filtro0")

        classes = []
        for div in class_divs:
            try:
                classes.append(self._parse_class(div, date))
            except Exception:
                continue

        return classes

    def _parse_class(self, div: Tag, date: datetime.date) -> dict:
        """Parse a single class div into a dict."""
        # Name
        name_div = div.find("div", attrs={"align": "left", "class": "col-50"})
        name = name_div.text.strip() if name_div else "Unknown"

        # Details
        details_div = div.find("div", attrs={"align": "right", "class": "col-50"})
        details = details_div.text.strip() if details_div else ""

        # Time
        time_div = div.find("div", attrs={"align": "left", "class": "col"})
        time_str = time_div.text.strip() if time_div else ""

        # Capacity
        cap_div = div.find("div", attrs={"align": "center", "class": "col"})
        cap_text = cap_div.text.strip() if cap_div else "0 de 0"
        cap_parts = cap_text.split()
        cur_cap = int(cap_parts[0]) if cap_parts[0].isdigit() else 0
        max_cap = int(cap_parts[-1]) if cap_parts[-1].isdigit() else None

        # Extract real class_id from the visibility/details link
        class_id = None
        vis_link = div.find("a", onclick=lambda x: x and "detalhes_aula" in x)
        if vis_link:
            id_match = re.search(r"valor3=(\d+)", vis_link["onclick"])
            if id_match:
                class_id = id_match.group(1)

        # Enrollment button
        enroll_url = None
        unenroll_url = None
        is_enrolled = False
        is_open = False

        button = div.find("button")
        if button:
            onclick = button.get("onclick", "")
            urls = [part for part in onclick.split("'") if ".php" in part]
            if urls:
                url_part = urls[0]
                if url_part.startswith("../app_nova/"):
                    url_part = url_part.replace("../app_nova/", "")
                full_url = f"{BASE_URL}/{url_part}" if not url_part.startswith("http") else url_part
                if "color-red" in button.get("class", []):
                    is_enrolled = True
                    unenroll_url = full_url
                elif "color-green" in button.get("class", []):
                    is_open = True
                    enroll_url = full_url

        # Check if enrolled via checkmark
        if div.find("div", attrs={"class": "ok_color"}):
            is_enrolled = True

        # Check if class is over
        is_over = False
        state_divs = div.find_all("div", attrs={"align": "right", "class": "col"})
        if state_divs:
            state_text = state_divs[-1].text.strip()
            if "Conclu" in state_text or "Over" in state_text:
                is_over = True

        return {
            "name": name,
            "details": details,
            "time": time_str,
            "capacity": cur_cap,
            "max_capacity": max_cap,
            "is_open": is_open,
            "is_enrolled": is_enrolled,
            "is_over": is_over,
            "class_id": class_id,
            "enroll_url": enroll_url,
            "unenroll_url": unenroll_url,
        }

    def enroll(self, enroll_url: str) -> str:
        """Enroll in a class given its enroll_url. Returns the response message."""
        self._require_login()
        r = self.session.get(enroll_url, headers=HEADERS, timeout=15)
        r.raise_for_status()

        soup = BeautifulSoup(r.text, "html.parser")
        # Look for the toast message in script tags
        for script in soup.find_all("script"):
            text = script.text or ""
            msgs = re.findall(r'parent\.msg_toast_icon\s*\("(.+?)"', text)
            if msgs:
                return msgs[0]
            msgs = re.findall(r'msg_toast\s*\("(.+?)"', text)
            if msgs:
                return msgs[0]

        return r.text.strip()[:200]

    def unenroll(self, unenroll_url: str) -> str:
        """Cancel enrollment from a class. Returns the response message."""
        self._require_login()
        r = self.session.get(unenroll_url, headers=HEADERS, timeout=15)
        r.raise_for_status()

        soup = BeautifulSoup(r.text, "html.parser")
        for script in soup.find_all("script"):
            text = script.text or ""
            msgs = re.findall(r'parent\.msg_toast_icon\s*\("(.+?)"', text)
            if msgs:
                return msgs[0]

        return r.text.strip()[:200]

    def get_class_details(self, class_id: str, date: str) -> dict:
        """Get details for a specific class (enrolled people, etc.)."""
        self._require_login()
        r = self.session.get(
            f"{BASE_URL}/php/aulas/detalhes_aula.php",
            params={
                "valor2": datetime.date.today().year,
                "valor3": class_id,
                "valor4": date,
                "source": "mes",
            },
            headers=HEADERS,
            timeout=10,
        )
        r.raise_for_status()

        soup = BeautifulSoup(r.text, "html.parser")

        date_div = soup.find("div", attrs={"align": "left", "class": "col"})
        time_div = soup.find("div", attrs={"align": "right", "class": "col"})

        # Extract enrolled people from item-title elements, skipping non-person entries
        enrolled = []
        for el in soup.find_all(class_="item-title"):
            name = el.text.strip()
            if name and name not in ("Workout-of-the-day", "Drop IN", ""):
                enrolled.append(name)

        return {
            "date": date_div.text.strip() if date_div else date,
            "time": time_div.text.strip() if time_div else "",
            "enrolled_count": len(enrolled),
            "enrolled_people": enrolled,
        }
