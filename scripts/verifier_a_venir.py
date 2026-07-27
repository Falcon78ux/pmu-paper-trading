"""
=============================================================================
VERIFIER_A_VENIR.PY - Detecte les value bets avant chaque course
=============================================================================
A lancer regulierement (toutes les 10-15 min) via GitHub Actions. Pour
chaque course de trot (attele/monte) qui demarre dans la fenetre de
verification, calcule l'EV de chaque partant avec les modeles v1.4, v1.5
ET v1.8, calcule la mise Kelly correspondante (bankroll virtuelle
independante par modele), et notifie par Telegram les value bets
detectes (EV > SEUIL_EV) avec le montant en euros.

v1.8 ajoute : nombre de partants, aptitude corde (etat_chevaux_corde.json,
nouveau), deferrage 4 pieds, et une mise Kelly a deux regimes (fraction
reduite sur les paris D4).
=============================================================================
"""

import sys
import os
import json
import math
from datetime import datetime, timezone, timedelta

import requests

sys.path.insert(0, os.path.dirname(__file__))
from commun import (
    charger_json, sauvegarder_json, envoyer_telegram, calculer_proba,
    calculer_proba_v18, get_driver_forme, get_biais_hippodrome,
    get_speed_figure_avant_course, get_ecart_corde, extraire_cote_directe,
    extraire_deferre_4_pieds, get_bankroll, calculer_mise, calculer_mise_v18,
)

RACINE = os.path.join(os.path.dirname(__file__), "..")

SEUIL_EV = 0.10
FENETRE_MIN_MINUTES = 15
FENETRE_MAX_MINUTES = 40


def recuperer_programme_du_jour(date_str):
    url = f"https://online.turfinfo.api.pmu.fr/rest/client/61/programme/{date_str}"
    r = requests.get(url, timeout=20)
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
    modele_v14 = charger_json(f"{RACINE}/modele_v14.json")
    modele_v15 = charger_json(f"{RACINE}/modele_v15.json")
    modele_v18 = charger_json(f"{RACINE}/modele_v18_production.json")
    courses_notifiees = charger_json(f"{RACINE}/courses_notifiees.json", {})

    bankroll_v14, chemin_bankroll_v14 = get_bankroll(RACINE, "v14")
    bankroll_v15, chemin_bankroll_v15 = get_bankroll(RACINE, "v15")
    bankroll_v18, chemin_bankroll_v18 = get_bankroll(RACINE, "v18")

    try:
        courses = recuperer_programme_du_jour(date_str)
    except Exception as e:
        envoyer_telegram(f"⚠️ Erreur recuperation programme du jour : {e}")
        return

    log_paris = []

    for course in courses:
        if course["discipline"] not in ("ATTELE", "MONTE"):
            continue

        race_id = f"{date_str}_{course['num_reunion']}_{course['num_course']}"
        if race_id in courses_notifiees:
            continue

        heure_depart_ms = course.get("heure_depart_ms")
        if heure_depart_ms is None:
            continue
        heure_depart = datetime.fromtimestamp(heure_depart_ms / 1000, tz=timezone.utc)
        minutes_avant_depart = (heure_depart - maintenant).total_seconds() / 60

        if not (FENETRE_MIN_MINUTES <= minutes_avant_depart <= FENETRE_MAX_MINUTES):
            continue

        try:
            participants = recuperer_participants(date_str, course["num_reunion"], course["num_course"])
        except Exception as e:
            print(f"Erreur participants {race_id} : {e}")
            continue

        nb_partants_course = sum(1 for p in participants if p.get("statut") == "PARTANT")

        value_bets_v14 = []
        value_bets_v15 = []
        value_bets_v18 = []

        for p in participants:
            if p.get("statut") != "PARTANT":
                continue
            cheval = p.get("nom")
            driver = p.get("driver") or p.get("entraineur")
            cote = extraire_cote_directe(p)
            if cote is None or cote <= 1:
                continue

            sf_avant = get_speed_figure_avant_course(etat_chevaux, cheval)
            driver_forme = get_driver_forme(etat_drivers, driver)
            biais_hippo = get_biais_hippodrome(etat_hippodromes, course["hippodrome"])
            log_cote = math.log(cote)

            if sf_avant is not None and driver_forme is not None:
                proba14 = calculer_proba(
                    {"speed_figure_avant_course": sf_avant, "log_cote": log_cote, "driver_forme": driver_forme},
                    modele_v14,
                )
                if proba14 is not None:
                    ev14 = proba14 * cote - 1
                    if ev14 > SEUIL_EV:
                        mise14 = calculer_mise(proba14, cote, bankroll_v14)
                        if mise14 > 0:
                            value_bets_v14.append((cheval, cote, proba14, ev14, mise14))

            if sf_avant is not None and driver_forme is not None and biais_hippo is not None:
                proba15 = calculer_proba(
                    {
                        "speed_figure_avant_course": sf_avant, "log_cote": log_cote,
                        "driver_forme": driver_forme, "biais_hippodrome": biais_hippo,
                    },
                    modele_v15,
                )
                if proba15 is not None:
                    ev15 = proba15 * cote - 1
                    if ev15 > SEUIL_EV:
                        mise15 = calculer_mise(proba15, cote, bankroll_v15)
                        if mise15 > 0:
                            value_bets_v15.append((cheval, cote, proba15, ev15, mise15))

            if sf_avant is not None and driver_forme is not None and biais_hippo is not None:
                ecart_corde = get_ecart_corde(etat_chevaux_corde, etat_chevaux, cheval, course["corde"])
                deferre_4_pieds = extraire_deferre_4_pieds(p)
                proba18 = calculer_proba_v18(
                    {
                        "speed_figure_avant_course": sf_avant, "log_cote": log_cote,
                        "driver_forme": driver_forme, "biais_hippodrome": biais_hippo,
                        "nb_partants_course": nb_partants_course, "ecart_corde": ecart_corde,
                        "deferre_4_pieds": deferre_4_pieds,
                    },
                    modele_v18,
                )
                if proba18 is not None:
                    ev18 = proba18 * cote - 1
                    if ev18 > SEUIL_EV:
                        mise18 = calculer_mise_v18(proba18, cote, bankroll_v18, deferre_4_pieds)
                        if mise18 > 0:
                            value_bets_v18.append((cheval, cote, proba18, ev18, mise18, deferre_4_pieds))

        if value_bets_v14 or value_bets_v15 or value_bets_v18:
            msg = f"🐎 <b>Course {course['hippodrome']} R{course['num_reunion']}C{course['num_course']}</b>\n"
            msg += f"Depart dans ~{int(minutes_avant_depart)} min\n\n"
            if value_bets_v14:
                msg += f"<b>Modele v1.4</b> (bankroll : {bankroll_v14:.0f}€) :\n"
                for cheval, cote, proba, ev, mise in value_bets_v14:
                    msg += f"• {cheval} — cote {cote:.1f}, proba {proba:.1%}, EV {ev:+.1%}, <b>mise {mise:.2f}€</b>\n"
                    log_paris.append({
                        "race_id": race_id, "modele": "v1.4", "cheval": cheval,
                        "cote": cote, "ev": ev, "mise": mise,
                        "date_detection": maintenant.isoformat(),
                    })
            if value_bets_v15:
                msg += f"\n<b>Modele v1.5</b> (bankroll : {bankroll_v15:.0f}€) :\n"
                for cheval, cote, proba, ev, mise in value_bets_v15:
                    msg += f"• {cheval} — cote {cote:.1f}, proba {proba:.1%}, EV {ev:+.1%}, <b>mise {mise:.2f}€</b>\n"
                    log_paris.append({
                        "race_id": race_id, "modele": "v1.5", "cheval": cheval,
                        "cote": cote, "ev": ev, "mise": mise,
                        "date_detection": maintenant.isoformat(),
                    })
            if value_bets_v18:
                msg += f"\n<b>Modele v1.8</b> (bankroll : {bankroll_v18:.0f}€) :\n"
                for cheval, cote, proba, ev, mise, d4 in value_bets_v18:
                    marque_d4 = " [D4]" if d4 else ""
                    msg += f"• {cheval}{marque_d4} — cote {cote:.1f}, proba {proba:.1%}, EV {ev:+.1%}, <b>mise {mise:.2f}€</b>\n"
                    log_paris.append({
                        "race_id": race_id, "modele": "v1.8", "cheval": cheval,
                        "cote": cote, "ev": ev, "mise": mise,
                        "date_detection": maintenant.isoformat(),
                    })

            envoyer_telegram(msg)

        courses_notifiees[race_id] = {
            "date_notif": maintenant.isoformat(),
            "hippodrome": course["hippodrome"],
            "corde": course.get("corde", ""),
        }

    sauvegarder_json(f"{RACINE}/courses_notifiees.json", courses_notifiees)

    if log_paris:
        chemin_log = f"{RACINE}/paris_virtuels.csv"
        existe = os.path.exists(chemin_log)
        import csv
        with open(chemin_log, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=[
                "race_id", "modele", "cheval", "cote", "ev", "mise",
                "date_detection", "resultat", "gain_euros",
            ])
            if not existe:
                writer.writeheader()
            for ligne in log_paris:
                ligne["resultat"] = ""
                ligne["gain_euros"] = ""
                writer.writerow(ligne)

    print(f"Verification terminee. {len(log_paris)} value bets detectes.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        detail = traceback.format_exc()[-500:]
        detail_echappe = detail.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        envoyer_telegram(f"🔴 <b>Erreur dans verifier_a_venir.py</b>\n\n{e}\n\n<code>{detail_echappe}</code>")
        raise
