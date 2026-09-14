"""
=============================================================================
VERIFIER_A_VENIR_GALOP.PY - Detecte les paris galop avant chaque
course (2 strategies : EV Gagnant, Place pick unique)
=============================================================================
ENTIEREMENT SEPARE du trot. SIMPLIFIE (14 sept, apres-midi) : plus de
restriction aux 4 hippodromes Premium, plus besoin d'historique redk -
le modele final ne depend que de la cote et de la forme du jockey,
disponibles pour TOUTES les courses PLAT via l'API PMU standard.
=============================================================================
"""

import sys
import os
import math
from datetime import datetime, timezone

import requests

sys.path.insert(0, os.path.dirname(__file__))
from commun_galop import (
    charger_json, sauvegarder_json, envoyer_telegram_galop,
    calculer_proba_galop, get_jockey_forme_galop, get_bankroll_galop,
    calculer_mise_ev_galop, calculer_mise_place_galop,
)

RACINE = os.path.join(os.path.dirname(__file__), "..")

SEUIL_EV = 0.10
FENETRE_MIN_MINUTES = 15
FENETRE_MAX_MINUTES = 40

# CORRIGE (14 sept, apres-midi) : le modele final a ete entraine et
# valide UNIQUEMENT sur ces 4 hippodromes Premium (coherent avec toute
# la validation de la semaine - walk-forward, ablation, AUC) - jamais
# teste a l'echelle nationale, ou le coefficient jockey_std s'est
# revele quasi nul (signal probablement specifique a ce niveau de
# competition). Restriction remise en place pour rester coherent avec
# ce qui a ete reellement valide.
HIPPODROMES_COUVERTS = {"CHANTILLY", "DEAUVILLE", "SAINT-CLOUD", "PARISLONGCHAMP"}


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


def extraire_cote_directe(participant):
    rapport = participant.get("dernierRapportDirect")
    if rapport and rapport.get("typePari") == "SIMPLE_GAGNANT":
        return rapport.get("rapport")
    return None


def main():
    maintenant = datetime.now(timezone.utc)
    date_str = maintenant.strftime("%d%m%Y")

    etat_drivers_galop = charger_json(f"{RACINE}/etat_drivers_galop.json", {})
    courses_notifiees_galop = charger_json(f"{RACINE}/courses_notifiees_galop.json", {})
    etat_pause_galop = charger_json(f"{RACINE}/etat_pause_galop.json", {})

    modele_ev = charger_json(f"{RACINE}/modele_galop_ev.json")
    modele_place = charger_json(f"{RACINE}/modele_galop_place.json")

    bankroll_ev, chemin_bankroll_ev = get_bankroll_galop(RACINE, "galopev")
    bankroll_place, chemin_bankroll_place = get_bankroll_galop(RACINE, "galopplace")

    try:
        courses = recuperer_programme_du_jour(date_str)
    except Exception as e:
        envoyer_telegram_galop(f"Erreur recuperation programme galop : {e}")
        return

    log_paris = []

    for course in courses:
        if course["discipline"] != "PLAT":
            continue
        if course["hippodrome"].upper().strip() not in HIPPODROMES_COUVERTS:
            continue

        race_id = f"{date_str}_{course['num_reunion']}_{course['num_course']}"
        if race_id in courses_notifiees_galop:
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

        candidats_course = []

        for p in participants:
            if p.get("statut") != "PARTANT":
                continue
            cheval = p.get("nom")
            driver = p.get("driver") or p.get("entraineur")
            cote = extraire_cote_directe(p)

            if cote is None or cote <= 1:
                continue

            jockey_forme = get_jockey_forme_galop(etat_drivers_galop, driver)
            if jockey_forme is None:
                continue

            log_cote = math.log(cote)

            proba_ev = calculer_proba_galop(log_cote, jockey_forme, modele_ev)
            proba_place = calculer_proba_galop(log_cote, jockey_forme, modele_place)

            if proba_ev is None or proba_place is None:
                continue

            candidats_course.append({
                "cheval": cheval, "num_pmu": p.get("numPmu"), "cote": cote,
                "proba_ev": proba_ev, "proba_place": proba_place,
            })

        if not candidats_course:
            courses_notifiees_galop[race_id] = {"date_notif": maintenant.isoformat(), "hippodrome": course["hippodrome"]}
            continue

        sections_msg = []

        # --- Strategie 1 : EV Gagnant (seuil EV>10%, mise Kelly) ---
        value_bets_ev = []
        for c in candidats_course:
            ev = c["proba_ev"] * c["cote"] - 1
            if ev > SEUIL_EV:
                mise = calculer_mise_ev_galop(c["proba_ev"], c["cote"], bankroll_ev)
                if mise > 0:
                    value_bets_ev.append((c["cheval"], c["cote"], c["proba_ev"], ev, mise))

        if value_bets_ev and not etat_pause_galop.get("galopev", False):
            bloc = f"<b>Galop EV Gagnant</b> (bankroll : {bankroll_ev:.0f}EUR) :\n"
            for cheval, cote, proba, ev, mise in value_bets_ev:
                bloc += f"- {cheval} - cote {cote:.1f}, proba {proba:.1%}, EV {ev:+.1%}, <b>mise {mise:.0f}EUR</b>\n"
            sections_msg.append(bloc)

        for cheval, cote, proba, ev, mise in value_bets_ev:
            log_paris.append({"race_id": race_id, "modele": "galopev", "cheval": cheval, "cote": cote, "cote_cloture": "", "ev": ev, "mise": mise, "date_detection": maintenant.isoformat()})

        # --- Strategie 2 : Place, un seul pick par course (le plus probable) ---
        meilleur_place = max(candidats_course, key=lambda c: c["proba_place"])
        mise_place = calculer_mise_place_galop(bankroll_place)
        if mise_place > 0 and not etat_pause_galop.get("galopplace", False):
            bloc = f"<b>Galop Place</b> (bankroll : {bankroll_place:.0f}EUR) :\n"
            bloc += f"- {meilleur_place['cheval']} - proba place {meilleur_place['proba_place']:.1%}, <b>mise {mise_place:.0f}EUR</b>\n"
            sections_msg.append(bloc)

        if mise_place > 0:
            log_paris.append({"race_id": race_id, "modele": "galopplace", "cheval": meilleur_place["cheval"], "cote": "", "cote_cloture": "", "ev": "", "mise": mise_place, "date_detection": maintenant.isoformat()})

        if sections_msg:
            msg = f"<b>Course galop {course['hippodrome']} R{course['num_reunion']}C{course['num_course']}</b>\n"
            msg += f"Depart dans ~{int(minutes_avant_depart)} min\n\n"
            msg += "\n".join(sections_msg)
            envoyer_telegram_galop(msg)

        courses_notifiees_galop[race_id] = {"date_notif": maintenant.isoformat(), "hippodrome": course["hippodrome"]}

    sauvegarder_json(f"{RACINE}/courses_notifiees_galop.json", courses_notifiees_galop)

    if log_paris:
        chemin_log = f"{RACINE}/paris_virtuels_galop.csv"
        existe = os.path.exists(chemin_log)
        import csv
        with open(chemin_log, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=[
                "race_id", "modele", "cheval", "cote", "cote_cloture", "ev", "mise",
                "date_detection", "resultat", "gain_euros",
            ])
            if not existe:
                writer.writeheader()
            for ligne in log_paris:
                ligne["resultat"] = ""
                ligne["gain_euros"] = ""
                writer.writerow(ligne)

    print(f"Verification galop terminee. {len(log_paris)} value bets detectes.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        detail = traceback.format_exc()[-500:]
        detail_echappe = detail.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        message_erreur_echappe = str(e).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        envoyer_telegram_galop(f"🔴 Erreur dans verifier_a_venir_galop.py\n\n{message_erreur_echappe}\n\n{detail_echappe}")
        raise
