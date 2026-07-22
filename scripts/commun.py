"""
=============================================================================
FONCTIONS COMMUNES - utilisees par verifier_a_venir.py et verifier_resultats.py
=============================================================================
"""

import json
import math
import os
import requests


# -----------------------------------------------------------------------
# CHARGEMENT / SAUVEGARDE JSON
# -----------------------------------------------------------------------

def charger_json(chemin, defaut=None):
    if os.path.exists(chemin):
        with open(chemin, "r", encoding="utf-8") as f:
            return json.load(f)
    return defaut if defaut is not None else {}


def sauvegarder_json(chemin, data):
    with open(chemin, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)


# -----------------------------------------------------------------------
# TELEGRAM
# -----------------------------------------------------------------------

def envoyer_telegram(message):
    """Envoie un message via le bot Telegram. Token/chat_id lus depuis les
    variables d'environnement (injectees par GitHub Actions depuis les Secrets)."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("ATTENTION : TELEGRAM_BOT_TOKEN ou TELEGRAM_CHAT_ID manquant, message non envoye.")
        print(message)
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        r = requests.post(url, data={"chat_id": chat_id, "text": message, "parse_mode": "HTML"}, timeout=15)
        if r.status_code != 200:
            print(f"Erreur envoi Telegram ({r.status_code}) : {r.text}")
    except Exception as e:
        print(f"Exception envoi Telegram : {e}")


# -----------------------------------------------------------------------
# SCORING DU MODELE (regression logistique - coefficients pre-calcules)
# -----------------------------------------------------------------------

def sigmoid(x):
    try:
        return 1 / (1 + math.exp(-x))
    except OverflowError:
        return 0.0 if x < 0 else 1.0


def calculer_proba(valeurs_brutes, modele):
    """
    valeurs_brutes : dict {nom_variable: valeur}, ex {"speed_figure_avant_course": 120, "log_cote": 1.5, ...}
    modele : dict charge depuis modele_v14.json ou modele_v15.json
    Retourne la probabilite de victoire estimee, ou None si une variable manque.
    """
    coefs = modele["coefficients"]
    norm = modele["normalisation"]
    z = coefs.get("const", 0.0)

    for var in modele["variables_brutes"]:
        if var not in valeurs_brutes or valeurs_brutes[var] is None:
            return None
        moyenne = norm[var]["moyenne"]
        ecart_type = norm[var]["ecart_type"]
        valeur_std = (valeurs_brutes[var] - moyenne) / ecart_type
        z += coefs.get(var + "_std", 0.0) * valeur_std

    return sigmoid(z)


# -----------------------------------------------------------------------
# GESTION DE L'ETAT GLISSANT (driver / hippodrome / cheval)
# -----------------------------------------------------------------------

FENETRE_DRIVER = 100
FENETRE_HIPPODROME = 200
FENETRE_CHEVAL = 3
MIN_DRIVER = 20
MIN_HIPPODROME = 10


def get_driver_forme(etat_drivers, driver):
    historique = etat_drivers.get(driver, [])
    if len(historique) < MIN_DRIVER:
        return None
    return sum(historique) / len(historique)


def maj_driver(etat_drivers, driver, victoire):
    """victoire : 1 si gagnant, 0 sinon."""
    historique = etat_drivers.get(driver, [])
    historique.append(victoire)
    etat_drivers[driver] = historique[-FENETRE_DRIVER:]


def get_biais_hippodrome(etat_hippodromes, hippodrome):
    donnees = etat_hippodromes.get(hippodrome)
    if donnees is None:
        return None
    nb_total = sum(donnees["nb_partants"])
    if nb_total < MIN_HIPPODROME:
        return None
    return sum(donnees["sommes_ecart"]) / nb_total


def maj_hippodrome(etat_hippodromes, hippodrome, somme_ecart_course, nb_partants_course):
    donnees = etat_hippodromes.get(hippodrome, {"sommes_ecart": [], "nb_partants": []})
    donnees["sommes_ecart"].append(somme_ecart_course)
    donnees["nb_partants"].append(nb_partants_course)
    donnees["sommes_ecart"] = donnees["sommes_ecart"][-FENETRE_HIPPODROME:]
    donnees["nb_partants"] = donnees["nb_partants"][-FENETRE_HIPPODROME:]
    etat_hippodromes[hippodrome] = donnees


def get_speed_figure_avant_course(etat_chevaux, cheval):
    historique = etat_chevaux.get(cheval, [])
    if len(historique) == 0:
        return None
    return sum(historique) / len(historique)


def maj_cheval(etat_chevaux, cheval, speed_figure_brut):
    historique = etat_chevaux.get(cheval, [])
    historique.append(speed_figure_brut)
    etat_chevaux[cheval] = historique[-FENETRE_CHEVAL:]


# -----------------------------------------------------------------------
# EXTRACTION DE LA COTE DIRECTE DEPUIS UN PARTICIPANT (API PMU)
# -----------------------------------------------------------------------

def extraire_cote_directe(participant):
    rapport = participant.get("dernierRapportDirect")
    if rapport and rapport.get("typePari") == "SIMPLE_GAGNANT":
        return rapport.get("rapport")
    return None
