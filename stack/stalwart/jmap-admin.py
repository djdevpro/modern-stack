#!/usr/bin/env python3
"""Petit client d'administration Stalwart v1 (JMAP, capacité urn:stalwart:jmap).

  STALWART_ADMIN_PASSWORD=... python3 jmap-admin.py '<méthode>' '<arguments JSON>'
  ex : jmap-admin.py x:Domain/get '{"ids": null}'
  « - » à la place des arguments : JSON lu sur l'entrée standard (pour les secrets).

Variables : STALWART_URL (défaut http://stalwart:8080), STALWART_ADMIN_USER (défaut admin).
À lancer depuis un conteneur du réseau « mail » (voir stack/README.md).
"""
import base64
import json
import os
import sys
import urllib.request

URL = os.environ.get("STALWART_URL", "http://stalwart:8080") + "/jmap/"
USER = os.environ.get("STALWART_ADMIN_USER", "admin")
PASSWORD = os.environ["STALWART_ADMIN_PASSWORD"]


def appel(methode, arguments):
    corps = {"using": ["urn:ietf:params:jmap:core", "urn:stalwart:jmap"],
             "methodCalls": [[methode, arguments, "c1"]]}
    auth = base64.b64encode(f"{USER}:{PASSWORD}".encode()).decode()
    req = urllib.request.Request(URL, data=json.dumps(corps).encode(), method="POST",
                                 headers={"Authorization": f"Basic {auth}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as rep:
        nom, resultat, _ = json.load(rep)["methodResponses"][0]
    return nom, resultat


if __name__ == "__main__":
    brut = sys.argv[2] if len(sys.argv) > 2 else "{}"
    nom, resultat = appel(sys.argv[1], json.loads(sys.stdin.read() if brut == "-" else brut))
    print(json.dumps({"reponse": nom, **resultat}, indent=1, ensure_ascii=False))
