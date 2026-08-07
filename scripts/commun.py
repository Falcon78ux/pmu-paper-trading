"""
=============================================================================
FONCTIONS COMMUNES - utilisees par verifier_a_venir.py et verifier_resultats.py
=============================================================================
"""

import json
import math
import os
import requests


def charger_json(chemin, defaut=None):
