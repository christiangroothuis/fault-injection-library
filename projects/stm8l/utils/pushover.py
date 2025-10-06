import requests
import os

def send_pushover_notification(message, title=None):
    url = "https://api.pushover.net/1/messages.json"
    payload = {
        "token": os.getenv("PUSHOVER_APP_TOKEN"),
        "user": os.getenv("PUSHOVER_USER_KEY"),
        "message": message,
    }
    if title:
        payload["title"] = title

    response = requests.post(url, data=payload)
    if response.status_code != 200:
        print(f"Failed to send notification: {response.text}")
