import requests

def send_pushover_notification(user_key, app_token, message, title=None):
    url = "https://api.pushover.net/1/messages.json"
    payload = {
        "token": app_token,
        "user": user_key,
        "message": message,
    }
    if title:
        payload["title"] = title

    response = requests.post(url, data=payload)
    if response.status_code != 200:
        print(f"Failed to send notification: {response.text}")