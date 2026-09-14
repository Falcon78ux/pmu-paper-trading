"""
=============================================================================
COMMUN_GALOP.PY - Fonctions communes propres au GALOP - ENTIEREMENT
SEPAREES de commun.py (trot), a la demande explicite de l'utilisateur
(14 sept 2026).

SIMPLIFIE (14 sept, apres-midi) : le signal redk (McLloyd) a ete
retire suite a une investigation rigoureuse (ablation fenetre par
fenetre, AUC isole 0.497-0.572 selon la construction, gagnant
seulement 39-57% des fenetres de walk-forward face au modele sans
redk) - conclusion : redk n'apporte pas d'edge fiable et
generalisable une fois la cote et la forme du jockey deja connues.
Modele final : "cote + jockey" seulement, pour EV Gagnant ET Place.
Plus besoin de scraper le moindre PDF McLloyd - fiabilite bien
meilleure (tout via l'API PMU officielle, comme le trot).
=============================================================================
"""

import json
import math
import os
import requests


def charger_json(chemin, defaut=None):
    if os.path.exists(chemin):
        with open(chemin, "r", encoding="utf-8") as f:
            return json.load(f)
    return defaut if defaut is not None else {}


def sauvegarder_json(chemin, data):
    with open(chemin, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)


LIMITE_TELEGRAM = 4000


def decouper_message(message, limite=LIMITE_TELEGRAM):
    if len(message) <= limite:
        return [message]
    morceaux = []
    reste = message
    while len(reste) > limite:
        point_coupure = reste.rfind("\n\n", 0, limite)
        if point_coupure == -1:
            point_coupure = reste.rfind("\n", 0, limite)
        if point_coupure == -1 or point_coupure < limite // 2:
            point_coupure = limite
        morceaux.append(reste[:point_coupure])
        reste = reste[point_coupure:].lstrip("\n")
    if reste:
        morceaux.append(reste)
    return morceaux


def envoyer_telegram_galop(message):
    """Bot DISTINCT du trot - utilise TELEGRAM_BOT_TOKEN_GALOP et
    TELEGRAM_CHAT_ID_GALOP, jamais les variables du trot."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN_GALOP")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID_GALOP")

    if not token or not chat_id:
        print("ATTENTION : TELEGRAM_BOT_TOKEN_GALOP ou TELEGRAM_CHAT_ID_GALOP manquant, message non envoye.")
        print(message)
        return

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    morceaux = decouper_message(message)
    nb_morceaux = len(morceaux)

    for i, morceau in enumerate(morceaux):
        texte_envoye = morceau
        if nb_morceaux > 1:
            texte_envoye = f"[{i+1}/{nb_morceaux}]\n{morceau}"
        try:
            r = requests.post(url, data={"chat_id": chat_id, "text": texte_envoye, "parse_mode": "HTML"}, timeout=15)
            if r.status_code != 200:
                print(f"Erreur envoi Telegram galop ({r.status_code}) : {r.text}")
        except Exception as e:
            print(f"Exception envoi Telegram galop : {e}")


def sigmoid(x):
    try:
        return 1 / (1 + math.exp(-x))
    except OverflowError:
        return 0.0 if x < 0 else 1.0


def calculer_proba_galop(log_cote, jockey_forme, modele):
    """Calcule la probabilite (gagnant ou place selon le modele charge)
    a partir de la cote et de la forme du jockey UNIQUEMENT - redk
    retire (voir note d'en-tete du fichier)."""
    if log_cote is None or jockey_forme is None:
        return None

    coefs = modele["coefficients"]
    norm = modele["normalisation"]

    moy_jockey, ect_jockey = norm["jockey_forme"]["moyenne"], norm["jockey_forme"]["ecart_type"]
    jockey_std = (jockey_forme - moy_jockey) / ect_jockey

    z = coefs.get("const", 0.0)
    z += coefs.get("log_cote", 0.0) * log_cote
    z += coefs.get("jockey_std", 0.0) * jockey_std

    return sigmoid(z)


FENETRE_JOCKEY = 50
MIN_JOCKEY = 15


def get_jockey_forme_galop(etat_drivers_galop, jockey):
    historique = etat_drivers_galop.get(jockey, [])
    if len(historique) < MIN_JOCKEY:
        return None
    return sum(historique) / len(historique)


def maj_jockey_forme_galop(etat_drivers_galop, jockey, victoire):
    historique = etat_drivers_galop.get(jockey, [])
    historique.append(victoire)
    etat_drivers_galop[jockey] = historique[-FENETRE_JOCKEY:]


FRACTION_KELLY_GALOP = 0.10
MISE_MINIMUM = 1.0
BANKROLL_DEPART_GALOP = 1236
MISE_FIXE_PLACE_GALOP = 10

PALIERS_PLAFOND_GALOP = [
    (5000, 20),
    (20000, 50),
    (50000, 100),
    (150000, 250),
    (float("inf"), 500),
]


def arrondir_mise_euro(mise):
    mise_arrondie = round(mise)
    if mise_arrondie < MISE_MINIMUM:
        return 0.0
    return float(mise_arrondie)


def obtenir_plafond_dynamique_galop(bankroll):
    for seuil, plafond in PALIERS_PLAFOND_GALOP:
        if bankroll < seuil:
            return plafond
    return PALIERS_PLAFOND_GALOP[-1][1]


def get_bankroll_galop(racine, nom_strategie):
    chemin = f"{racine}/bankroll_{nom_strategie}.json"
    data = charger_json(chemin, {"bankroll": BANKROLL_DEPART_GALOP})
    return data["bankroll"], chemin


def mettre_a_jour_bankroll_galop(chemin, nouvelle_bankroll):
    """Plancher a zero applique DES LE DEPART (lecon tiree du bug de
    bankroll negative decouvert sur le trot le 13 septembre 2026)."""
    sauvegarder_json(chemin, {"bankroll": round(max(nouvelle_bankroll, 0), 2)})


def calculer_mise_ev_galop(proba, cote, bankroll):
    """Mise Kelly fractionnee pour la strategie EV Gagnant."""
    b = cote - 1
    if b <= 0:
        return 0.0
    kelly_full = max(0.0, (proba * b - (1 - proba)) / b)
    kelly_fraction = kelly_full * FRACTION_KELLY_GALOP
    plafond = obtenir_plafond_dynamique_galop(bankroll)
    mise = min(kelly_fraction * bankroll, plafond)
    return arrondir_mise_euro(mise)


def calculer_mise_place_galop(bankroll):
    """Mise fixe pour la strategie Place (1 pick/course)."""
    if bankroll < MISE_FIXE_PLACE_GALOP:
        return 0.0
    return float(MISE_FIXE_PLACE_GALOP)
