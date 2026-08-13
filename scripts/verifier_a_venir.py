"""
=============================================================================
VERIFIER_A_VENIR.PY - Detecte les value bets avant chaque course
=============================================================================
12 strategies en parallele. NOUVEAU : v1.4-FAVORI (reutilise le meme
modele/calcul que v1.4, filtre en plus sur "le cheval selectionne
est-il aussi le favori du marche" - meme principe que v1.10-Favori,
deploye le 13 aout). Taux de victoire attendu ~32%, croissance moindre
mais drawdown plus faible que v1.4 seul.
=============================================================================
"""

import sys
import os
import json
import math
import csv as csv_module
from datetime import datetime, timezone, timedelta

import requests

sys.path.insert(0, os.path.dirname(__file__))
from commun import (
    charger_json, sauvegarder_json, envoyer_telegram,
    calculer_proba_avec_contributions, calculer_proba_v18_avec_contributions,
    calculer_proba_v110_ou_place_avec_contributions,
    get_driver_forme, get_biais_hippodrome, get_speed_figure_avant_course,
    get_ecart_corde, extraire_cote_directe, extraire_deferre_4_pieds,
    extraire_age, extraire_indicateur_femelle, extraire_taux_victoire_carriere,
    get_dernier_rang, get_sire_forme, charger_table_pedigree,
    get_bankroll, calculer_mise, calculer_mise_v18, calculer_mise_v110,
    calculer_mise_place, calculer_mise_2favori, calculer_mise_v14sire,
)

RACINE = os.path.join(os.path.dirname(__file__), "..")

SEUIL_EV = 0.10
FENETRE_MIN_MINUTES = 15
FENETRE_MAX_MINUTES = 40


def recuperer_programme_du_jour(date_str):
    url = f"https://online.turfinfo.api.pmu.fr/rest/client/61/programme/{date_str}"
    r = requests.get(url, timeout=35)
    r.raise_for_status()
    data = r.json()

    courses = []
    for reunion in data.get("programme", {}).get("reunions", []):
        hippodrome = reunion.get("hippodrome", {}).get("libelleCourt", reunion.get("hippodrome", {}).get("libelle", "?"))
        num_reunion = reunion.get("numOfficiel", reunion.get("numExterne"))
        for course in reunion.get("courses", []):
            courses.append({
                "num_reunion": str(num_reunion),
                "num_course": str(course.get("numOrdre", course.get("numExterne"))),
                "discipline": course.get("discipline", ""),
                "heure_depart_ms": course.get("heureDepart"),
                "hippodrome": hippodrome,
                "statut": course.get("statut", ""),
                "corde": course.get("corde", ""),
            })
    return courses


def recuperer_participants(date_str, num_reunion, num_course):
    url = (
        f"https://online.turfinfo.api.pmu.fr/rest/client/61/programme/"
        f"{date_str}/R{num_reunion}/C{num_course}/participants"
    )
    r = requests.get(url, timeout=20)
    r.raise_for_status()
    return r.json().get("participants", [])


def main():
    maintenant = datetime.now(timezone.utc)
    date_str = maintenant.strftime("%d%m%Y")

    etat_drivers = charger_json(f"{RACINE}/etat_drivers.json", {})
    etat_hippodromes = charger_json(f"{RACINE}/etat_hippodromes.json", {})
    etat_chevaux = charger_json(f"{RACINE}/etat_chevaux.json", {})
    etat_chevaux_corde = charger_json(f"{RACINE}/etat_chevaux_corde.json", {})
    etat_dernier_rang = charger_json(f"{RACINE}/etat_dernier_rang.json", {})
    etat_sire_forme = charger_json(f"{RACINE}/etat_sire_forme.json", {})
    etat_pause = charger_json(f"{RACINE}/etat_pause.json", {})
    table_pedigree = charger_table_pedigree(f"{RACINE}/pedigree_aplati.csv")
    modele_v14 = charger_json(f"{RACINE}/modele_v14.json")
    modele_v15 = charger_json(f"{RACINE}/modele_v15.json")
    modele_v18 = charger_json(f"{RACINE}/modele_v18_production.json")
    modele_v110 = charger_json(f"{RACINE}/modele_v110_production.json")
    modele_place = charger_json(f"{RACINE}/modele_place_v1_production.json")
    modele_2favori = charger_json(f"{RACINE}/modele_deuxieme_favori_v2_production.json")
    modele_v14sire = charger_json(f"{RACINE}/modele_v14_sire_forme_production.json")
    courses_notifiees = charger_json(f"{RACINE}/courses_notifiees.json", {})

    bankroll_v14, chemin_bankroll_v14 = get_bankroll(RACINE, "v14")
    bankroll_v14favori, chemin_bankroll_v14favori = get_bankroll(RACINE, "v14favori")
    bankroll_v14sire, chemin_bankroll_v14sire = get_bankroll(RACINE, "v14sire")
    bankroll_v15, chemin_bankroll_v15 = get_bankroll(RACINE, "v15")
    bankroll_v18, chemin_bankroll_v18 = get_bankroll(RACINE, "v18")
    bankroll_v110, chemin_bankroll_v110 = get_bankroll(RACINE, "v110")
    bankroll_v110favori, chemin_bankroll_v110favori = get_bankroll(RACINE, "v110favori")
    bankroll_place, chemin_bankroll_place = get_bankroll(RACINE, "place")
    bankroll_2sur4, chemin_bankroll_2sur4 = get_bankroll(RACINE, "2sur4")
    bankroll_trio, chemin_bankroll_trio = get_bankroll(RACINE, "trio")
    bankroll_multi, chemin_bankroll_multi = get_bankroll(RACINE, "multi")
    bankroll_2favori, chemin_bankroll_2favori = get_bankroll(RACINE, "2favori")

    try:
        courses = recuperer_programme_du_jour(date_str)
    except Exception as e:
        envoyer_telegram(f"⚠️ Erreur recuperation programme du jour : {e}")
        return

    log_paris = []

    for course in courses:
        if course["discipline"] not in ("ATTELE", "MONTE"):
            contin
