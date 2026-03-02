# Regybox Client

A Python client and data-export tool for [Regybox](https://www.regybox.pt), the platform used by many CrossFit and fitness boxes in Portugal for class scheduling, enrollment, and member management.

## What's included

### `regybox.py` — API client library

A standalone `RegyboxClient` class that handles the full Regybox web flow:

- **Login** — authenticates through Regybox's anti-bot mechanism (dynamic form fields, honeypot inputs, anti-CSRF tokens) and establishes a session.
- **List classes** — fetches the schedule for any date, returning structured data: class name, time, capacity, enrollment status, and action URLs.
- **Enroll / Cancel** — enroll in or cancel a class by its URL, returning the server's confirmation message.
- **Class details** — retrieves who is enrolled in a specific class.

Credentials are read from environment variables (`REGYBOX_EMAIL`, `REGYBOX_PASSWORD`, `REGYBOX_BOX_ID`) or passed directly to the constructor.

### `main.py` — Interactive CLI

A terminal interface for browsing and managing your classes:

```
$ python main.py              # today's classes
$ python main.py tomorrow     # tomorrow's classes
$ python main.py 2026-03-15   # specific date
```

Once loaded, an interactive menu lets you:
- **[e]nroll** in a class by number
- **[c]ancel** an enrollment
- **[d]etails** — see who's signed up
- **[n]ext / [p]rev day** — navigate the schedule
- **[q]uit**

### `exporter/` — Web-based data exporter

A Flask web app that lets any Regybox user export their personal data as a JSON file (GDPR data portability). No account or setup required — users enter their credentials in the browser and get a download.

**Exported data includes:**
- Profile (name, email, phone, address, body metrics)
- Account & plan info (payment method, price, validity, allowed class types)
- Attendance stats (sign-ups, attendances, absences, monthly breakdown)
- Personal records (movements and weights)
- Upcoming classes for the next 7 days

Credentials are sent directly to Regybox and are never stored.

Run it with:

```bash
pip install flask
python exporter/app.py
# Open http://localhost:5000
```

## Setup

```bash
# Clone the repo
git clone https://github.com/dieguscl/regybox-client.git
cd regybox-client

# Install dependencies
pip install -r requirements.txt

# Configure credentials for the CLI
cp .env.example .env
# Edit .env with your Regybox email, password, and box ID
```

### Environment variables

| Variable | Description |
|---|---|
| `REGYBOX_EMAIL` | Your Regybox login email |
| `REGYBOX_PASSWORD` | Your Regybox password |
| `REGYBOX_BOX_ID` | Your box's numeric ID (e.g. `168`) |

### Dependencies

- `requests` — HTTP client
- `beautifulsoup4` — HTML parsing
- `python-dotenv` — `.env` file loading
- `flask` — web server (exporter only)

## Using the client as a library

```python
from regybox import RegyboxClient
import datetime

client = RegyboxClient(email="you@example.com", password="pass", box_id="168")
client.login()

# Get today's classes
classes = client.get_classes()

# Get classes for a specific date
classes = client.get_classes(datetime.date(2026, 3, 15))

# Enroll in a class
for c in classes:
    if c["is_open"] and "CrossFit" in c["name"]:
        result = client.enroll(c["enroll_url"])
        print(result)
        break

# See who's in a class
details = client.get_class_details(class_id="12345", date="2026-03-15")
print(details["enrolled_people"])
```

## License

Apache 2.0 — see [LICENSE](LICENSE).
