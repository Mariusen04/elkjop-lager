import os
import time
from datetime import datetime

import requests


DDNS_URL = "https://api.domeneshop.no/v0/dyndns/update"
HOSTNAMES = [
    hostname.strip()
    for hostname in os.environ.get(
        "DDNS_HOSTNAME",
        "lager.ankervold.no",
    ).split(",")
    if hostname.strip()
]
TOKEN = os.environ.get("DOMENESHOP_API_TOKEN", "").strip()
SECRET = os.environ.get("DOMENESHOP_API_SECRET", "").strip()
INTERVAL_SECONDS = max(int(os.environ.get("DDNS_INTERVAL_SECONDS", "300")), 60)


def log(message):
    timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
    print(f"{timestamp} {message}", flush=True)


def update_dns(hostname):
    response = requests.get(
        DDNS_URL,
        params={"hostname": hostname},
        auth=(TOKEN, SECRET),
        timeout=30,
    )

    if response.status_code != 204:
        raise RuntimeError(
            f"Domeneshop svarte HTTP {response.status_code}: "
            f"{response.text[:300]}"
        )

    log(f"DNS oppdatert for {hostname}.")


def main():
    if not HOSTNAMES or not TOKEN or not SECRET:
        raise RuntimeError("Mangler DDNS_HOSTNAME eller Domeneshop API-nøkkel.")

    log(
        f"Starter DDNS for {', '.join(HOSTNAMES)} "
        f"(intervall {INTERVAL_SECONDS} sekunder)."
    )

    while True:
        for hostname in HOSTNAMES:
            try:
                update_dns(hostname)
            except Exception as error:
                log(f"DDNS-feil for {hostname}: {error}")

        time.sleep(INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
