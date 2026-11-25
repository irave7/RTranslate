"""Pass native rendering workers the runtime environment without provider credentials."""
import os
import re


def native_env(environment=None, *, key_name=None, key=None):
    environment = os.environ if environment is None else environment
    names = {n for n in environment if re.search(r"(?:API_?KEY|TOKEN|SECRET(?:_?KEY)?|PASSWORD|CREDENTIALS?)$", n, re.I)
             or (key_name and n.casefold() == key_name.casefold())}
    secrets = {environment[n] for n in names if environment[n]}
    if key:
        secrets.add(key)
    def carries_secret(value):
        return value in secrets or any(len(s) >= 8 and s in value for s in secrets)
    return {n:v for n,v in environment.items() if n not in names and not carries_secret(v)}
