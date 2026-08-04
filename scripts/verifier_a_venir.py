"""
=============================================================================
VERIFIER_A_VENIR.PY - Detecte les value bets avant chaque course
=============================================================================
8 strategies en parallele : v1.4, v1.5, v1.8, v1.10 (EV>seuil, mise Kelly),
PLACE, 2SUR4, TRIO, MULTI/MINI_MULTI (top pick, mise fixe).

MULTI (14+ partants) et MINI_MULTI (10-13 partants) sont mutuellement
exclusifs selon la taille du champ - une seule des deux s'applique par
course.

Ajout diagnostic_couverture.csv : journalise, pour CHAQUE course de trot
traitee (pas seulement celles avec un pari), combien de partants avaient
toutes les donnees necessaires - pour comprendre pourquoi certaines
courses ne generent aucun pari combine (place/2sur4/trio/multi).
=============================================================================
"""

import sys
import os
import json
import math
import csv as csv_module  # eviter collision avec le csv importe plus bas dans main()
from datetime import datetime, timezone, timedelta

import requests

sys.path.insert(0, os.path.dirname(__file__))
from commun import (
    charger_json, sauvegarder_json, envoyer_telegram, calculer_proba,
    calculer_proba_v18, calculer_proba_v110_ou_place, get_driver_forme,
    get_biais_hippodrome, get_speed_figure_avant_course, get_ecart_corde,
    extraire_cote_directe, extraire_deferre_4_pieds, extraire_age,
    extraire_indicateur_femelle, extraire_taux_victoire_carriere,
    get_bankroll, calculer_mise, calculer_mise_v18, calculer_mise_v110,
    calculer_mise_place,
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
    modele_v14 = charger_json(f"{RACINE}/modele_v14.json")
    modele_v15 = charger_json(f"{RACINE}/modele_v15.json")
    modele_v18 = charger_json(f"{RACINE}/modele_v18_production.json")
    modele_v110 = charger_json(f"{RACINE}/modele_v110_production.json")
    modele_place = charger_json(f"{RACINE}/modele_place_v1_production.json")
    courses_notifiees = charger_json(f"{RACINE}/courses_notifiees.json", {})

    bankroll_v14, chemin_bankroll_v14 = get_bankroll(RACINE, "v14")
    bankroll_v15, chemin_bankroll_v15 = get_bankroll(RACINE, "v15")
    bankroll_v18, chemin_bankroll_v18 = get_bankroll(RACINE, "v18")
    bankroll_v110, chemin_bankroll_v110 = get_bankroll(RACINE, "v110")
    bankroll_place, chemin_bankroll_place = get_bankroll(RACINE, "place")
    bankroll_2sur4, chemin_bankroll_2sur4 = get_bankroll(RACINE, "2sur4")
    bankroll_trio, chemin_bankroll_trio = get_bankroll(RACINE, "trio")
    bankroll_multi, chemin_bankroll_multi = get_bankroll(RACINE, "multi")

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

        # --- Diagnostic de couverture (pourquoi certaines courses n'ont aucun pari combine) ---
        diag = {
            "sans_cote": 0, "sans_sf": 0, "sans_driver_forme": 0,
            "sans_biais_hippo": 0, "sans_ecart_corde": 0, "valides_pour_place": 0,
        }

        value_bets_v14 = []
        value_bets_v15 = []
        value_bets_v18 = []
        value_bets_v110 = []
        candidats_place = []  # tous les partants avec une proba_place valide

        for p in participants:
            if p.get("statut") != "PARTANT":
                continue
            cheval = p.get("nom")
            driver = p.get("driver") or p.get("entraineur")
            cote = extraire_cote_directe(p)

            sf_avant = get_speed_figure_avant_course(etat_chevaux, cheval)
            driver_forme = get_driver_forme(etat_drivers, driver)
            biais_hippo = get_biais_hippodrome(etat_hippodromes, course["hippodrome"])

            if cote is None or cote <= 1:
                diag["sans_cote"] += 1
            elif sf_avant is None:
                diag["sans_sf"] += 1
            elif driver_forme is None:
                diag["sans_driver_forme"] += 1
            elif biais_hippo is None:
                diag["sans_biais_hippo"] += 1

            if cote is None or cote <= 1:
                continue

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

            ecart_corde = None
            deferre_4_pieds = None
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
            else:
                diag["sans_ecart_corde"] += 1

            if sf_avant is not None and driver_forme is not None and biais_hippo is not None and ecart_corde is not None:
                age = extraire_age(p)
                indicateur_femelle = extraire_indicateur_femelle(p)
                taux_victoire_carriere = extraire_taux_victoire_carriere(p)

                valeurs_communes = {
                    "speed_figure_avant_course": sf_avant, "log_cote": log_cote,
                    "driver_forme": driver_forme, "biais_hippodrome": biais_hippo,
                    "nb_partants_course": nb_partants_course, "ecart_corde": ecart_corde,
                    "deferre_4_pieds": deferre_4_pieds, "age": age,
                    "indicateur_femelle": indicateur_femelle,
                    "taux_victoire_carriere": taux_victoire_carriere,
                }

                proba110 = calculer_proba_v110_ou_place(valeurs_communes, modele_v110)
                if proba110 is not None:
                    ev110 = proba110 * cote - 1
                    if ev110 > SEUIL_EV:
                        mise110 = calculer_mise_v110(proba110, cote, bankroll_v110, deferre_4_pieds)
                        if mise110 > 0:
                            value_bets_v110.append((cheval, cote, proba110, ev110, mise110, deferre_4_pieds))

                proba_place = calculer_proba_v110_ou_place(valeurs_communes, modele_place)
                if proba_place is not None:
                    candidats_place.append((cheval, proba_place, cote))

        diag["valides_pour_place"] = len(candidats_place)

        chemin_diag = f"{RACINE}/diagnostic_couverture.csv"
        existe_diag = os.path.exists(chemin_diag)
        with open(chemin_diag, "a", newline="", encoding="utf-8") as f:
            writer_diag = csv_module.DictWriter(f, fieldnames=[
                "race_id", "hippodrome", "nb_partants", "sans_cote", "sans_sf",
                "sans_driver_forme", "sans_biais_hippo", "sans_ecart_corde",
                "valides_pour_place", "date_verif",
            ])
            if not existe_diag:
                writer_diag.writeheader()
            writer_diag.writerow({
                "race_id": race_id, "hippodrome": course["hippodrome"],
                "nb_partants": nb_partants_course, **diag,
                "date_verif": maintenant.isoformat(),
            })

        # --- PLACE : top 1 ---
        value_bets_place = []
        if candidats_place:
            meilleur = max(candidats_place, key=lambda x: x[1])
            cheval_place, proba_place_choisi, _ = meilleur
            mise_place = calculer_mise_place()
            value_bets_place.append((cheval_place, proba_place_choisi, mise_place))

        # --- 2 SUR 4 : top 2 ---
        value_bets_deux_sur_quatre = []
        if len(candidats_place) >= 2:
            top2 = sorted(candidats_place, key=lambda x: x[1], reverse=True)[:2]
            chevaux_choisis = [c[0] for c in top2]
            mise_2sur4 = calculer_mise_place()
            value_bets_deux_sur_quatre.append((chevaux_choisis, mise_2sur4))

        # --- TRIO (non ordonne) : top 3 ---
        value_bets_trio = []
        if len(candidats_place) >= 3:
            top3 = sorted(candidats_place, key=lambda x: x[1], reverse=True)[:3]
            chevaux_choisis_trio = [c[0] for c in top3]
            mise_trio = calculer_mise_place()
            value_bets_trio.append((chevaux_choisis_trio, mise_trio))

        # --- MULTI (14+ partants) ou MINI_MULTI (10-13 partants) : top 4 ---
        value_bets_multi = []
        type_multi = None
        if nb_partants_course >= 14:
            type_multi = "MULTI"
        elif 10 <= nb_partants_course <= 13:
            type_multi = "MINI_MULTI"
        if type_multi and len(candidats_place) >= 4:
            top4 = sorted(candidats_place, key=lambda x: x[1], reverse=True)[:4]
            chevaux_choisis_multi = [c[0] for c in top4]
            mise_multi = calculer_mise_place()
            value_bets_multi.append((chevaux_choisis_multi, mise_multi, type_multi))

        if (value_bets_v14 or value_bets_v15 or value_bets_v18 or value_bets_v110
                or value_bets_place or value_bets_deux_sur_quatre or value_bets_trio or value_bets_multi):
            msg = f"🐎 <b>Course {course['hippodrome']} R{course['num_reunion']}C{course['num_course']}</b>\n"
            msg += f"Depart dans ~{int(minutes_avant_depart)} min\n\n"
            if value_bets_v14:
                msg += f"<b>Modele v1.4</b> (bankroll : {bankroll_v14:.0f}€) :\n"
                for cheval, cote, proba, ev, mise in value_bets_v14:
                    msg += f"• {cheval} — cote {cote:.1f}, proba {proba:.1%}, EV {ev:+.1%}, <b>mise {mise:.2f}€</b>\n"
                    log_paris.append({"race_id": race_id, "modele": "v1.4", "cheval": cheval, "cote": cote, "ev": ev, "mise": mise, "date_detection": maintenant.isoformat()})
            if value_bets_v15:
                msg += f"\n<b>Modele v1.5</b> (bankroll : {bankroll_v15:.0f}€) :\n"
                for cheval, cote, proba, ev, mise in value_bets_v15:
                    msg += f"• {cheval} — cote {cote:.1f}, proba {proba:.1%}, EV {ev:+.1%}, <b>mise {mise:.2f}€</b>\n"
                    log_paris.append({"race_id": race_id, "modele": "v1.5", "cheval": cheval, "cote": cote, "ev": ev, "mise": mise, "date_detection": maintenant.isoformat()})
            if value_bets_v18:
                msg += f"\n<b>Modele v1.8</b> (bankroll : {bankroll_v18:.0f}€) :\n"
                for cheval, cote, proba, ev, mise, d4 in value_bets_v18:
                    marque_d4 = " [D4]" if d4 else ""
                    msg += f"• {cheval}{marque_d4} — cote {cote:.1f}, proba {proba:.1%}, EV {ev:+.1%}, <b>mise {mise:.2f}€</b>\n"
                    log_paris.append({"race_id": race_id, "modele": "v1.8", "cheval": cheval, "cote": cote, "ev": ev, "mise": mise, "date_detection": maintenant.isoformat()})
            if value_bets_v110:
                msg += f"\n<b>Modele v1.10</b> (bankroll : {bankroll_v110:.0f}€) :\n"
                for cheval, cote, proba, ev, mise, d4 in value_bets_v110:
                    marque_d4 = " [D4]" if d4 else ""
                    msg += f"• {cheval}{marque_d4} — cote {cote:.1f}, proba {proba:.1%}, EV {ev:+.1%}, <b>mise {mise:.2f}€</b>\n"
                    log_paris.append({"race_id": race_id, "modele": "v1.10", "cheval": cheval, "cote": cote, "ev": ev, "mise": mise, "date_detection": maintenant.isoformat()})
            if value_bets_place:
                msg += f"\n<b>Modele PLACE</b> (bankroll : {bankroll_place:.0f}€, top pick, mise fixe) :\n"
                for cheval, proba, mise in value_bets_place:
                    msg += f"• {cheval} — proba place {proba:.1%}, <b>mise {mise:.2f}€</b>\n"
                    log_paris.append({"race_id": race_id, "modele": "place", "cheval": cheval, "cote": "", "ev": "", "mise": mise, "date_detection": maintenant.isoformat()})
            if value_bets_deux_sur_quatre:
                msg += f"\n<b>Modele 2 SUR 4</b> (bankroll : {bankroll_2sur4:.0f}€, top 2, mise fixe) :\n"
                for chevaux, mise in value_bets_deux_sur_quatre:
                    msg += f"• {' + '.join(chevaux)} — <b>mise {mise:.2f}€</b>\n"
                    log_paris.append({"race_id": race_id, "modele": "2sur4", "cheval": "|".join(chevaux), "cote": "", "ev": "", "mise": mise, "date_detection": maintenant.isoformat()})
            if value_bets_trio:
                msg += f"\n<b>Modele TRIO</b> (bankroll : {bankroll_trio:.0f}€, top 3, mise fixe) :\n"
                for chevaux, mise in value_bets_trio:
                    msg += f"• {' + '.join(chevaux)} — <b>mise {mise:.2f}€</b>\n"
                    log_paris.append({"race_id": race_id, "modele": "trio", "cheval": "|".join(chevaux), "cote": "", "ev": "", "mise": mise, "date_detection": maintenant.isoformat()})
            if value_bets_multi:
                msg += f"\n<b>Modele {type_multi}</b> (bankroll : {bankroll_multi:.0f}€, top 4, mise fixe) :\n"
                for chevaux, mise, type_pari in value_bets_multi:
                    msg += f"• {' + '.join(chevaux)} — <b>mise {mise:.2f}€</b>\n"
                    log_paris.append({"race_id": race_id, "modele": "multi", "cheval": "|".join(chevaux), "cote": type_pari, "ev": "", "mise": mise, "date_detection": maintenant.isoformat()})

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
