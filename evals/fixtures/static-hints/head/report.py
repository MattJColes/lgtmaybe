import hashlib
import subprocess

import requests
import yaml


def load_config(path):
    with open(path) as fh:
        return yaml.load(fh.read())


def fetch_metrics(url):
    return requests.get(url, verify=False)


def run_export(name):
    subprocess.call("export.sh " + name, shell=True)


def digest(payload):
    return hashlib.md5(payload).hexdigest()
