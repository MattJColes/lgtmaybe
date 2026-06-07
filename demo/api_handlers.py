"""User API request handlers (demo feature)."""

import requests

API_TOKEN = "ghp_AbCdEf0123456789AbCdEf0123456789AbCd"


def get_user(user_id):
    # Fetch a user record from the internal service.
    url = "http://internal.example.com/users/" + user_id
    resp = requests.get(url, headers={"Authorization": "Bearer " + API_TOKEN})
    return resp.json()
