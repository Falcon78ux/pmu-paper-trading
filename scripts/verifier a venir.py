"""
=============================================================================
VERIFIER_A_VENIR.PY - Detecte les value bets avant chaque course
=============================================================================
16 strategies en parallele. CORRECTION MAJEURE (22 aout) : le dutching
(v14dutch, v110dutch) traite desormais CHAQUE outsider qualifie
(cote>=8, EV>10%) d'une meme course comme une opportunite INDEPENDANTE.

NOUVEAU (16 sept) : LIMITE D'EXPOSITION GLOBALE PAR CHEVAL - suite a
une analyse de risque (veille GitHub externe + mesure sur donnees
reelles), decouverte que 55.7% des paris concernent un cheval deja
parie par une autre strategie, avec jusqu'a 17 strategies
simultanement sur le meme cheval. Corrige en reduisant
proportionnellement les mises si l'exposition totale sur un cheval
depasse un plafond.

NOUVEAU (17 sept) : ENTROPIE COMME 12e VARIABLE DE v1.10 - calcul en
deux passes par course (voir candidats_v110_stage1 plus bas).

CORRIGE (17 sept, soir) : LE PLAFOND D'EXPOSITION PASSE D'UN MONTANT
FIXE (300EUR) A UN POURCENTAGE DE LA BANKROLL COMBINEE - suite a une
critique de l'audit externe : un plafond fixe de 300EUR representait
44% d'une bankroll v1.10 descendue a 687EUR, soit exactement le risque
de concentration qu'on cherchait a corriger, juste avec un seuil mal
calibre. Le nouveau plafond est desormais FRACTION_EXPOSITION_CHEVAL
(15% par defaut) de la SOMME des bankrolls des strategies qui parient
reellement sur ce cheval dans cette course - un plafond qui suit
naturellement la taille reelle des bankrolls concernees, a la hausse
comme a la baisse, plutot qu'un chiffre arbitraire fixe dans le temps.
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
    calculer_proba_v110_A, calculer_entropie_course,
    calculer_proba_v110_avec_entropie_et_contributions,
    get_driver_forme, get_biais_hippodrome, get_speed_figure_avant_course,
    get_ecart_corde, extraire_cote_directe, extraire_deferre_4_pieds,
    extraire_age, extraire_indicateur_femelle, extraire_taux_victoire_carriere,
    get_dernier_rang, get_sire_forme, charger_table_pedigree,
    get_bankroll, calculer_mise, calculer_mise_v18, calculer_mise_v110,
    charger_table_calibration, appliquer_calibration,
    calculer_mise_place, calculer_mise_2favori, calculer_mise_v14sire,
    arrondir_mise_euro, MISE_MINIMUM,
    get_deferre_precedent, detecter_changement_vers_d4,
)

RACINE = os.path.join(os.path.dirname(__file__), "..")

SEUIL_EV = 0.10
FENETRE_MIN_MINUTES = 15
FENETRE_MAX_MINUTES = 40
SEUIL_OUTSIDER_DUTCHING = 8.0
SEUIL_PROBA_SNIPER = 0.45
SEUIL_COTE_FAVORI_ANTIFAV = 2.5
SEUIL_ENTROPIE_BASSE = 1.30
SEUIL_ECART_FAIBLE = 0.055
FRACTION_EXPOSITION_CHEVAL = 0.15  # CORRIGE (17 sept, soir) : 15% de la SOMME des bankrolls des strategies qui parient sur ce cheval dans cette course - remplace l'ancien plafond fixe de 300EUR, juge trop concentre sur une bankroll individuelle amoindrie


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


def calculer_dutching(cheval_outsider, cote_outsider, proba_outsider, ev_outsider,
                       cheval_favori, cote_favori, bankroll_dutch,
                       fonction_mise, *args_fonction_mise):
    mise_totale = fonction_mise(proba_outsider, cote_outsider, bankroll_dutch, *args_fonction_mise)
    if mise_totale <= 0:
        return None

    ratio = cote_outsider / cote_favori
    mise_outsider_brute = mise_totale / (1 + ratio)
    mise_favori_brute = mise_totale - mise_outsider_brute

    mise_outsider = arrondir_mise_euro(mise_outsider_brute)
    mise_favori = arrondir_mise_euro(mise_favori_brute)

    if mise_outsider < MISE_MINIMUM or mise_favori < MISE_MINIMUM:
        return None

    return {
        "cheval_outsider": cheval_outsider, "cote_outsider": cote_outsider,
        "mise_outsider": mise_outsider,
        "cheval_favori": cheval_favori, "cote_favori": cote_favori,
        "mise_favori": mise_favori,
        "ev_outsider": ev_outsider,
    }


def main():
    maintenant = datetime.now(timezone.utc)
    date_str = maintenant.strftime("%d%m%Y")

    etat_drivers = charger_json(f"{RACINE}/etat_drivers.json", {})
    etat_hippodromes = charger_json(f"{RACINE}/etat_hippodromes.json", {})
    etat_chevaux = charger_json(f"{RACINE}/etat_chevaux.json", {})
    etat_chevaux_corde = charger_json(f"{RACINE}/etat_chevaux_corde.json", {})
    etat_dernier_rang = charger_json(f"{RACINE}/etat_dernier_rang.json", {})
    etat_sire_forme = charger_json(f"{RACINE}/etat_sire_forme.json", {})
    etat_deferrage = charger_json(f"{RACINE}/etat_deferrage.json", {})
    etat_pause = charger_json(f"{RACINE}/etat_pause.json", {})
    table_pedigree = charger_table_pedigree(f"{RACINE}/pedigree_aplati.csv")

    modele_v14 = charger_json(f"{RACINE}/modele_v14.json")
    modele_v15 = charger_json(f"{RACINE}/modele_v15.json")
    modele_v18 = charger_json(f"{RACINE}/modele_v18_production.json")
    modele_v110_A = charger_json(f"{RACINE}/modele_v110_A_production.json")
    modele_v110 = charger_json(f"{RACINE}/modele_v110_production.json")
    modele_place = charger_json(f"{RACINE}/modele_place_v1_production.json")
    modele_2favori = charger_json(f"{RACINE}/modele_deuxieme_favori_v2_production.json")
    modele_v14sire = charger_json(f"{RACINE}/modele_v14_sire_forme_production.json")

    courses_notifiees = charger_json(f"{RACINE}/courses_notifiees.json", {})

    bankroll_v14, chemin_bankroll_v14 = get_bankroll(RACINE, "v14")
    bankroll_v14dutch, chemin_bankroll_v14dutch = get_bankroll(RACINE, "v14dutch")
    bankroll_v14favori, chemin_bankroll_v14favori = get_bankroll(RACINE, "v14favori")
    bankroll_v14sire, chemin_bankroll_v14sire = get_bankroll(RACINE, "v14sire")
    bankroll_v14recalibre, chemin_bankroll_v14recalibre = get_bankroll(RACINE, "v14recalibre")
    bankroll_v15recalibre, chemin_bankroll_v15recalibre = get_bankroll(RACINE, "v15recalibre")
    bankroll_v18recalibre, chemin_bankroll_v18recalibre = get_bankroll(RACINE, "v18recalibre")
    bankroll_v110recalibre, chemin_bankroll_v110recalibre = get_bankroll(RACINE, "v110recalibre")
    table_calibration = charger_table_calibration(RACINE)
    bankroll_v15, chemin_bankroll_v15 = get_bankroll(RACINE, "v15")
    bankroll_v18, chemin_bankroll_v18 = get_bankroll(RACINE, "v18")
    bankroll_v110, chemin_bankroll_v110 = get_bankroll(RACINE, "v110")
    bankroll_v110dutch, chemin_bankroll_v110dutch = get_bankroll(RACINE, "v110dutch")
    bankroll_v110favori, chemin_bankroll_v110favori = get_bankroll(RACINE, "v110favori")
    bankroll_place, chemin_bankroll_place = get_bankroll(RACINE, "place")
    bankroll_2sur4, chemin_bankroll_2sur4 = get_bankroll(RACINE, "2sur4")
    bankroll_trio, chemin_bankroll_trio = get_bankroll(RACINE, "trio")
    bankroll_multi, chemin_bankroll_multi = get_bankroll(RACINE, "multi")
    bankroll_2favori, chemin_bankroll_2favori = get_bankroll(RACINE, "2favori")
    bankroll_couple_harville, chemin_bankroll_couple_harville = get_bankroll(RACINE, "couple_harville")
    bankroll_consensus_place, chemin_bankroll_consensus_place = get_bankroll(RACINE, "consensus_place")
    bankroll_v110d4, chemin_bankroll_v110d4 = get_bankroll(RACINE, "v110d4")
    bankroll_v110sniper, chemin_bankroll_v110sniper = get_bankroll(RACINE, "v110sniper")
    bankroll_v110place, chemin_bankroll_v110place = get_bankroll(RACINE, "v110place")
    bankroll_v110antifav, chemin_bankroll_v110antifav = get_bankroll(RACINE, "v110antifav")
    bankroll_v110snipercombine, chemin_bankroll_v110snipercombine = get_bankroll(RACINE, "v110snipercombine")
    bankroll_v110ecartfaible, chemin_bankroll_v110ecartfaible = get_bankroll(RACINE, "v110ecartfaible")

    # NOUVEAU (17 sept, soir) : table de correspondance modele -> bankroll,
    # utilisee pour calculer le plafond d'exposition dynamique par cheval
    bankrolls_par_modele = {
        "v1.4": bankroll_v14, "v14dutch": bankroll_v14dutch, "v14favori": bankroll_v14favori,
        "v14sire": bankroll_v14sire, "v14recalibre": bankroll_v14recalibre,
        "v1.5": bankroll_v15, "v15recalibre": bankroll_v15recalibre,
        "v1.8": bankroll_v18, "v18recalibre": bankroll_v18recalibre,
        "v1.10": bankroll_v110, "v110recalibre": bankroll_v110recalibre,
        "v110dutch": bankroll_v110dutch, "v110favori": bankroll_v110favori,
        "place": bankroll_place, "2sur4": bankroll_2sur4, "trio": bankroll_trio,
        "multi": bankroll_multi, "2favori": bankroll_2favori,
        "couple_harville": bankroll_couple_harville, "consensus_place": bankroll_consensus_place,
        "v110d4": bankroll_v110d4, "v110sniper": bankroll_v110sniper,
        "v110place": bankroll_v110place, "v110antifav": bankroll_v110antifav,
        "v110snipercombine": bankroll_v110snipercombine, "v110ecartfaible": bankroll_v110ecartfaible,
    }

    try:
        courses = recuperer_programme_du_jour(date_str)
    except Exception as e:
        envoyer_telegram(f"Erreur recuperation programme du jour : {e}")
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

        diag = {
            "sans_cote": 0, "sans_sf": 0, "sans_driver_forme": 0,
            "sans_biais_hippo": 0, "sans_ecart_corde": 0, "valides_pour_place": 0,
        }

        value_bets_v14 = []
        value_bets_v14recalibre = []
        value_bets_v14sire = []
        value_bets_v15 = []
        value_bets_v15recalibre = []
        value_bets_v18 = []
        value_bets_v18recalibre = []
        value_bets_v110 = []
        value_bets_v110recalibre = []
        value_bets_v110d4 = []
        value_bets_v110sniper = []
        value_bets_v110place = []
        value_bets_v110antifav = []
        candidats_place = []
        toutes_probas_v110 = []
        partants_avec_cote = []
        candidats_v110_stage1 = []

        for p in participants:
            if p.get("statut") != "PARTANT":
                continue

            cheval = p.get("nom")
            num_pmu_cheval = p.get("numPmu")
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

            partants_avec_cote.append((cheval, cote))
            log_cote = math.log(cote)

            if sf_avant is not None and driver_forme is not None:
                proba14, contrib14 = calculer_proba_avec_contributions(
                    {"speed_figure_avant_course": sf_avant, "log_cote": log_cote, "driver_forme": driver_forme},
                    modele_v14,
                )
                if proba14 is not None:
                    ev14 = proba14 * cote - 1
                    if ev14 > SEUIL_EV:
                        mise14 = calculer_mise(proba14, cote, bankroll_v14)
                        if mise14 > 0:
                            value_bets_v14.append((cheval, cote, proba14, ev14, mise14))

                    proba14_calibree = appliquer_calibration(table_calibration, "v14", proba14)
                    ev14_calibre = proba14_calibree * cote - 1
                    if ev14_calibre > SEUIL_EV:
                        mise14recalibre = calculer_mise(proba14_calibree, cote, bankroll_v14recalibre)
                        if mise14recalibre > 0:
                            value_bets_v14recalibre.append((cheval, cote, proba14_calibree, ev14_calibre, mise14recalibre))

                pere = table_pedigree.get(cheval)
                sire_forme = get_sire_forme(etat_sire_forme, pere)
                if sire_forme is not None:
                    proba14sire, contrib14sire = calculer_proba_avec_contributions(
                        {
                            "speed_figure_avant_course": sf_avant, "log_cote": log_cote,
                            "driver_forme": driver_forme, "sire_forme": sire_forme,
                        },
                        modele_v14sire,
                    )
                    if proba14sire is not None:
                        ev14sire = proba14sire * cote - 1
                        if ev14sire > SEUIL_EV:
                            mise14sire = calculer_mise_v14sire(proba14sire, cote, bankroll_v14sire)
                            if mise14sire > 0:
                                value_bets_v14sire.append((cheval, cote, proba14sire, ev14sire, mise14sire))

            if sf_avant is not None and driver_forme is not None and biais_hippo is not None:
                proba15, contrib15 = calculer_proba_avec_contributions(
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

                    proba15_calibree = appliquer_calibration(table_calibration, "v15", proba15)
                    ev15_calibre = proba15_calibree * cote - 1
                    if ev15_calibre > SEUIL_EV:
                        mise15recalibre = calculer_mise(proba15_calibree, cote, bankroll_v15recalibre)
                        if mise15recalibre > 0:
                            value_bets_v15recalibre.append((cheval, cote, proba15_calibree, ev15_calibre, mise15recalibre))

            ecart_corde = None
            deferre_4_pieds = None
            if sf_avant is not None and driver_forme is not None and biais_hippo is not None:
                ecart_corde = get_ecart_corde(etat_chevaux_corde, etat_chevaux, cheval, course["corde"])
                deferre_4_pieds = extraire_deferre_4_pieds(p)

                proba18, contrib18 = calculer_proba_v18_avec_contributions(
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

                    proba18_calibree = appliquer_calibration(table_calibration, "v18", proba18)
                    ev18_calibre = proba18_calibree * cote - 1
                    if ev18_calibre > SEUIL_EV:
                        mise18recalibre = calculer_mise_v18(proba18_calibree, cote, bankroll_v18recalibre, deferre_4_pieds)
                        if mise18recalibre > 0:
                            value_bets_v18recalibre.append((cheval, cote, proba18_calibree, ev18_calibre, mise18recalibre, deferre_4_pieds))
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

                proba_A = calculer_proba_v110_A(valeurs_communes, modele_v110_A)
                if proba_A is not None:
                    candidats_v110_stage1.append({
                        "cheval": cheval, "cote": cote, "num_pmu": num_pmu_cheval,
                        "deferre_4_pieds": deferre_4_pieds,
                        "valeurs_communes": valeurs_communes, "proba_A": proba_A,
                    })

                proba_place, contrib_place = calculer_proba_v110_ou_place_avec_contributions(valeurs_communes, modele_place)
                if proba_place is not None:
                    candidats_place.append((cheval, proba_place, cote))

        entropie_valeur = calculer_entropie_course([item["proba_A"] for item in candidats_v110_stage1])

        for item in candidats_v110_stage1:
            cheval = item["cheval"]
            cote = item["cote"]
            num_pmu_cheval = item["num_pmu"]
            deferre_4_pieds = item["deferre_4_pieds"]
            valeurs_avec_entropie = dict(item["valeurs_communes"])
            valeurs_avec_entropie["entropie"] = entropie_valeur

            proba110, contrib110 = calculer_proba_v110_avec_entropie_et_contributions(valeurs_avec_entropie, modele_v110)
            if proba110 is None:
                continue

            if num_pmu_cheval is not None:
                toutes_probas_v110.append((cheval, num_pmu_cheval, proba110))

            ev110 = proba110 * cote - 1
            if ev110 > SEUIL_EV:
                mise110 = calculer_mise_v110(proba110, cote, bankroll_v110, deferre_4_pieds)
                if mise110 > 0:
                    value_bets_v110.append((cheval, cote, proba110, ev110, mise110, deferre_4_pieds))

                proba110_calibree = appliquer_calibration(table_calibration, "v110", proba110)
                ev110_calibre = proba110_calibree * cote - 1
                if ev110_calibre > SEUIL_EV:
                    mise110recalibre = calculer_mise_v110(proba110_calibree, cote, bankroll_v110recalibre, deferre_4_pieds)
                    if mise110recalibre > 0:
                        value_bets_v110recalibre.append((cheval, cote, proba110_calibree, ev110_calibre, mise110recalibre, deferre_4_pieds))

                historique_deferrage = get_deferre_precedent(etat_deferrage, cheval)
                if detecter_changement_vers_d4(historique_deferrage, deferre_4_pieds):
                    mise110d4 = calculer_mise_v110(proba110, cote, bankroll_v110d4, deferre_4_pieds)
                    if mise110d4 > 0:
                        value_bets_v110d4.append((cheval, cote, proba110, ev110, mise110d4))

                if proba110 >= SEUIL_PROBA_SNIPER:
                    mise110sniper = calculer_mise_v110(proba110, cote, bankroll_v110sniper, deferre_4_pieds)
                    if mise110sniper > 0:
                        value_bets_v110sniper.append((cheval, cote, proba110, ev110, mise110sniper))

                mise110place = calculer_mise_v110(proba110, cote, bankroll_v110place, deferre_4_pieds)
                if mise110place > 0:
                    value_bets_v110place.append((cheval, cote, proba110, ev110, mise110place, num_pmu_cheval))

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

        value_bets_place = []
        meilleur_pick_place = None
        if candidats_place:
            meilleur = max(candidats_place, key=lambda x: x[1])
            cheval_place, proba_place_choisi, _ = meilleur
            meilleur_pick_place = cheval_place
            mise_place = calculer_mise_place(bankroll_place)
            if mise_place > 0:
                value_bets_place.append((cheval_place, proba_place_choisi, mise_place))

        value_bets_deux_sur_quatre = []
        if len(candidats_place) >= 2:
            top2 = sorted(candidats_place, key=lambda x: x[1], reverse=True)[:2]
            chevaux_choisis = [c[0] for c in top2]
            mise_2sur4 = calculer_mise_place(bankroll_2sur4)
            if mise_2sur4 > 0:
                value_bets_deux_sur_quatre.append((chevaux_choisis, mise_2sur4))

        value_bets_trio = []
        if len(candidats_place) >= 3:
            top3 = sorted(candidats_place, key=lambda x: x[1], reverse=True)[:3]
            chevaux_choisis_trio = [c[0] for c in top3]
            mise_trio = calculer_mise_place(bankroll_trio)
            if mise_trio > 0:
                value_bets_trio.append((chevaux_choisis_trio, mise_trio))

        value_bets_multi = []
        type_multi = None
        if nb_partants_course >= 14:
            type_multi = "MULTI"
        elif 10 <= nb_partants_course <= 13:
            type_multi = "MINI_MULTI"
        if type_multi and len(candidats_place) >= 4:
            top4 = sorted(candidats_place, key=lambda x: x[1], reverse=True)[:4]
            chevaux_choisis_multi = [c[0] for c in top4]
            mise_multi = calculer_mise_place(bankroll_multi)
            if mise_multi > 0:
                value_bets_multi.append((chevaux_choisis_multi, mise_multi, type_multi))

        value_bets_2favori = []
        if len(partants_avec_cote) >= 2:
            partants_tries = sorted(partants_avec_cote, key=lambda x: x[1])
            cheval_favori, cote_favori = partants_tries[0]
            cheval_2favori, cote_2favori = partants_tries[1]
            dernier_rang_favori = get_dernier_rang(etat_dernier_rang, cheval_favori)
            favori_non_place_avant = dernier_rang_favori is not None and dernier_rang_favori > 3
            if favori_non_place_avant:
                p_2favori = next((p for p in participants if p.get("nom") == cheval_2favori), None)
                if p_2favori is not None:
                    driver_2fav = p_2favori.get("driver") or p_2favori.get("entraineur")
                    sf_2fav = get_speed_figure_avant_course(etat_chevaux, cheval_2favori)
                    driver_forme_2fav = get_driver_forme(etat_drivers, driver_2fav)
                    biais_hippo_2fav = get_biais_hippodrome(etat_hippodromes, course["hippodrome"])
                    log_cote_2fav = math.log(cote_2favori)
                    if sf_2fav is not None and driver_forme_2fav is not None and biais_hippo_2fav is not None:
                        proba_2favori, contrib_2favori = calculer_proba_avec_contributions(
                            {
                                "speed_figure_avant_course": sf_2fav, "log_cote": log_cote_2fav,
                                "driver_forme": driver_forme_2fav, "biais_hippodrome": biais_hippo_2fav,
                            },
                            modele_2favori,
                        )
                        if proba_2favori is not None:
                            ev_2favori = proba_2favori * cote_2favori - 1
                            if ev_2favori > SEUIL_EV:
                                mise_2favori = calculer_mise_2favori(proba_2favori, cote_2favori, bankroll_2favori)
                                if mise_2favori > 0:
                                    value_bets_2favori.append((cheval_2favori, cote_2favori, proba_2favori, ev_2favori, mise_2favori))

        value_bets_v110favori = []
        if partants_avec_cote and value_bets_v110:
            cote_favori_marche = min(c for _, c in partants_avec_cote)
            for item in value_bets_v110:
                cheval_v110, cote_v110 = item[0], item[1]
                if cote_v110 == cote_favori_marche:
                    proba_v110, ev_v110, deferre_v110 = item[2], item[3], item[5]
                    mise_v110favori = calculer_mise_v110(proba_v110, cote_v110, bankroll_v110favori, deferre_v110)
                    if mise_v110favori > 0:
                        value_bets_v110favori.append((cheval_v110, cote_v110, proba_v110, ev_v110, mise_v110favori, deferre_v110))

        value_bets_v110antifav = []
        if partants_avec_cote and value_bets_v110:
            cote_favori_marche_antifav = min(c for _, c in partants_avec_cote)
            if cote_favori_marche_antifav >= SEUIL_COTE_FAVORI_ANTIFAV:
                for item in value_bets_v110:
                    cheval_v110, cote_v110, proba_v110, ev_v110, deferre_v110 = item[0], item[1], item[2], item[3], item[5]
                    mise_v110antifav = calculer_mise_v110(proba_v110, cote_v110, bankroll_v110antifav, deferre_v110)
                    if mise_v110antifav > 0:
                        value_bets_v110antifav.append((cheval_v110, cote_v110, proba_v110, ev_v110, mise_v110antifav))

        value_bets_v14favori = []
        if partants_avec_cote and value_bets_v14:
            cote_favori_marche = min(c for _, c in partants_avec_cote)
            for item in value_bets_v14:
                cheval_v14, cote_v14 = item[0], item[1]
                if cote_v14 == cote_favori_marche:
                    proba_v14, ev_v14 = item[2], item[3]
                    mise_v14favori = calculer_mise(proba_v14, cote_v14, bankroll_v14favori)
                    if mise_v14favori > 0:
                        value_bets_v14favori.append((cheval_v14, cote_v14, proba_v14, ev_v14, mise_v14favori))

        dutch_v14_liste = []
        dutch_v110_liste = []
        if partants_avec_cote:
            favori_marche_nom, cote_favori_marche = min(partants_avec_cote, key=lambda x: x[1])
            for item in value_bets_v14:
                cheval_out, cote_out, proba_out, ev_out, _ = item
                if cote_out >= SEUIL_OUTSIDER_DUTCHING and cheval_out != favori_marche_nom:
                    resultat = calculer_dutching(
                        cheval_out, cote_out, proba_out, ev_out,
                        favori_marche_nom, cote_favori_marche, bankroll_v14dutch,
                        calculer_mise,
                    )
                    if resultat:
                        dutch_v14_liste.append(resultat)

            for item in value_bets_v110:
                cheval_out, cote_out, proba_out, ev_out = item[0], item[1], item[2], item[3]
                deferre_out = item[5]
                if cote_out >= SEUIL_OUTSIDER_DUTCHING and cheval_out != favori_marche_nom:
                    resultat = calculer_dutching(
                        cheval_out, cote_out, proba_out, ev_out,
                        favori_marche_nom, cote_favori_marche, bankroll_v110dutch,
                        calculer_mise_v110, deferre_out,
                    )
                    if resultat:
                        dutch_v110_liste.append(resultat)

        couple_harville_pick = None
        if len(toutes_probas_v110) >= 2:
            tries = sorted(toutes_probas_v110, key=lambda x: x[2], reverse=True)
            cheval_1, num_pmu_1, proba_1 = tries[0]
            cheval_2, num_pmu_2, proba_2 = tries[1]
            mise_couple = calculer_mise_place(bankroll_couple_harville)
            if mise_couple > 0:
                couple_harville_pick = {
                    "cheval_1": cheval_1, "num_pmu_1": num_pmu_1,
                    "cheval_2": cheval_2, "num_pmu_2": num_pmu_2,
                    "mise": mise_couple,
                }

        value_bets_v110snipercombine = []
        if len(toutes_probas_v110) >= 2 and value_bets_v110:
            probas_norm = [p for _, _, p in toutes_probas_v110]
            somme_probas = sum(probas_norm)
            if somme_probas > 0:
                probas_norm = [p / somme_probas for p in probas_norm]
                entropie_course_snipercombine = -sum(p * math.log(p) for p in probas_norm if p > 0)
                if entropie_course_snipercombine <= SEUIL_ENTROPIE_BASSE:
                    for item in value_bets_v110:
                        cheval_v110, cote_v110, proba_v110, ev_v110, deferre_v110 = item[0], item[1], item[2], item[3], item[5]
                        if proba_v110 >= SEUIL_PROBA_SNIPER:
                            mise_combine = calculer_mise_v110(proba_v110, cote_v110, bankroll_v110snipercombine, deferre_v110)
                            if mise_combine > 0:
                                value_bets_v110snipercombine.append((cheval_v110, cote_v110, proba_v110, ev_v110, mise_combine))

        value_bets_v110ecartfaible = []
        if len(toutes_probas_v110) >= 2 and value_bets_v110:
            tries_ecart = sorted(toutes_probas_v110, key=lambda x: x[2], reverse=True)
            _, _, proba_rang1 = tries_ecart[0]
            cheval_rang2, num_pmu_rang2, proba_rang2 = tries_ecart[1]
            ecart_rang1_rang2 = proba_rang1 - proba_rang2
            if ecart_rang1_rang2 <= SEUIL_ECART_FAIBLE:
                for item in value_bets_v110:
                    cheval_v110, cote_v110, proba_v110, ev_v110, deferre_v110 = item[0], item[1], item[2], item[3], item[5]
                    if cheval_v110 == cheval_rang2:
                        mise_ecart = calculer_mise_v110(proba_v110, cote_v110, bankroll_v110ecartfaible, deferre_v110)
                        if mise_ecart > 0:
                            value_bets_v110ecartfaible.append((cheval_v110, cote_v110, proba_v110, ev_v110, mise_ecart))
                        break

        consensus_place_pick = None
        if meilleur_pick_place is not None and value_bets_v110:
            for item in value_bets_v110:
                cheval_v110, cote_v110, proba_v110, ev_v110, deferre_v110 = item[0], item[1], item[2], item[3], item[5]
                if cheval_v110 == meilleur_pick_place:
                    mise_consensus = calculer_mise_v110(proba_v110, cote_v110, bankroll_consensus_place, deferre_v110)
                    if mise_consensus > 0:
                        consensus_place_pick = (cheval_v110, cote_v110, proba_v110, ev_v110, mise_consensus)
                    break

        sections_msg = []

        if value_bets_v14 and not etat_pause.get("v14", False):
            bloc = f"<b>Modele v1.4</b> (bankroll : {bankroll_v14:.0f}EUR) :\n"
            for cheval, cote, proba, ev, mise in value_bets_v14:
                bloc += f"- {cheval} - cote {cote:.1f}, proba {proba:.1%}, EV {ev:+.1%}, <b>mise {mise:.0f}EUR</b>\n"
            sections_msg.append(bloc)

        if dutch_v14_liste and not etat_pause.get("v14dutch", False):
            bloc = f"<b>Modele v1.4-DUTCH</b> (bankroll : {bankroll_v14dutch:.0f}EUR, {len(dutch_v14_liste)} opportunite(s)) :\n"
            for d in dutch_v14_liste:
                bloc += f"- Outsider {d['cheval_outsider']} (cote {d['cote_outsider']:.1f}) - <b>mise {d['mise_outsider']:.0f}EUR</b>\n"
                bloc += f"  + Favori {d['cheval_favori']} (cote {d['cote_favori']:.1f}) - <b>mise {d['mise_favori']:.0f}EUR</b>\n"
            sections_msg.append(bloc)

        if value_bets_v14favori and not etat_pause.get("v14favori", False):
            bloc = f"<b>Modele v1.4-FAVORI</b> (bankroll : {bankroll_v14favori:.0f}EUR) :\n"
            for cheval, cote, proba, ev, mise in value_bets_v14favori:
                bloc += f"- {cheval} - cote {cote:.1f}, proba {proba:.1%}, EV {ev:+.1%}, <b>mise {mise:.0f}EUR</b>\n"
            sections_msg.append(bloc)

        if value_bets_v14sire and not etat_pause.get("v14sire", False):
            bloc = f"<b>Modele v1.4+GENEALOGIE</b> (bankroll : {bankroll_v14sire:.0f}EUR) :\n"
            for cheval, cote, proba, ev, mise in value_bets_v14sire:
                bloc += f"- {cheval} - cote {cote:.1f}, proba {proba:.1%}, EV {ev:+.1%}, <b>mise {mise:.0f}EUR</b>\n"
            sections_msg.append(bloc)

        if value_bets_v15 and not etat_pause.get("v15", False):
            bloc = f"<b>Modele v1.5</b> (bankroll : {bankroll_v15:.0f}EUR) :\n"
            for cheval, cote, proba, ev, mise in value_bets_v15:
                bloc += f"- {cheval} - cote {cote:.1f}, proba {proba:.1%}, EV {ev:+.1%}, <b>mise {mise:.0f}EUR</b>\n"
            sections_msg.append(bloc)

        if value_bets_v18 and not etat_pause.get("v18", False):
            bloc = f"<b>Modele v1.8</b> (bankroll : {bankroll_v18:.0f}EUR) :\n"
            for cheval, cote, proba, ev, mise, d4 in value_bets_v18:
                marque_d4 = " [D4]" if d4 else ""
                bloc += f"- {cheval}{marque_d4} - cote {cote:.1f}, proba {proba:.1%}, EV {ev:+.1%}, <b>mise {mise:.0f}EUR</b>\n"
            sections_msg.append(bloc)

        if value_bets_v110 and not etat_pause.get("v110", False):
            bloc = f"<b>Modele v1.10</b> (bankroll : {bankroll_v110:.0f}EUR) :\n"
            for cheval, cote, proba, ev, mise, d4 in value_bets_v110:
                marque_d4 = " [D4]" if d4 else ""
                bloc += f"- {cheval}{marque_d4} - cote {cote:.1f}, proba {proba:.1%}, EV {ev:+.1%}, <b>mise {mise:.0f}EUR</b>\n"
            sections_msg.append(bloc)

        if value_bets_v14recalibre and not etat_pause.get("v14recalibre", False):
            bloc = f"<b>v1.4-Recalibre</b> ({bankroll_v14recalibre:.0f}EUR) : " + ", ".join(f"{c} ({m:.0f}EUR)" for c, _, _, _, m in value_bets_v14recalibre) + "\n"
            sections_msg.append(bloc)

        if value_bets_v15recalibre and not etat_pause.get("v15recalibre", False):
            bloc = f"<b>v1.5-Recalibre</b> ({bankroll_v15recalibre:.0f}EUR) : " + ", ".join(f"{c} ({m:.0f}EUR)" for c, _, _, _, m in value_bets_v15recalibre) + "\n"
            sections_msg.append(bloc)

        if value_bets_v18recalibre and not etat_pause.get("v18recalibre", False):
            bloc = f"<b>v1.8-Recalibre</b> ({bankroll_v18recalibre:.0f}EUR) : " + ", ".join(f"{c} ({m:.0f}EUR)" for c, _, _, _, m, _ in value_bets_v18recalibre) + "\n"
            sections_msg.append(bloc)

        if value_bets_v110recalibre and not etat_pause.get("v110recalibre", False):
            bloc = f"<b>v1.10-Recalibre</b> ({bankroll_v110recalibre:.0f}EUR) : " + ", ".join(f"{c} ({m:.0f}EUR)" for c, _, _, _, m, _ in value_bets_v110recalibre) + "\n"
            sections_msg.append(bloc)

        if dutch_v110_liste and not etat_pause.get("v110dutch", False):
            bloc = f"<b>Modele v1.10-DUTCH</b> (bankroll : {bankroll_v110dutch:.0f}EUR, {len(dutch_v110_liste)} opportunite(s)) :\n"
            for d in dutch_v110_liste:
                bloc += f"- Outsider {d['cheval_outsider']} (cote {d['cote_outsider']:.1f}) - <b>mise {d['mise_outsider']:.0f}EUR</b>\n"
                bloc += f"  + Favori {d['cheval_favori']} (cote {d['cote_favori']:.1f}) - <b>mise {d['mise_favori']:.0f}EUR</b>\n"
            sections_msg.append(bloc)

        if value_bets_v110favori and not etat_pause.get("v110favori", False):
            bloc = f"<b>Modele v1.10-FAVORI</b> (bankroll : {bankroll_v110favori:.0f}EUR) :\n"
            for cheval, cote, proba, ev, mise, d4 in value_bets_v110favori:
                bloc += f"- {cheval} - cote {cote:.1f}, proba {proba:.1%}, EV {ev:+.1%}, <b>mise {mise:.0f}EUR</b>\n"
            sections_msg.append(bloc)

        if value_bets_v110d4 and not etat_pause.get("v110d4", False):
            bloc = f"<b>Modele v1.10-D4</b> (bankroll : {bankroll_v110d4:.0f}EUR, changement vers deferre 4 pieds) :\n"
            for cheval, cote, proba, ev, mise in value_bets_v110d4:
                bloc += f"- {cheval} - cote {cote:.1f}, proba {proba:.1%}, EV {ev:+.1%}, <b>mise {mise:.0f}EUR</b>\n"
            sections_msg.append(bloc)

        if value_bets_v110sniper and not etat_pause.get("v110sniper", False):
            bloc = f"<b>Modele v1.10-SNIPER</b> (bankroll : {bankroll_v110sniper:.0f}EUR, proba\u2265{SEUIL_PROBA_SNIPER:.0%}) :\n"
            for cheval, cote, proba, ev, mise in value_bets_v110sniper:
                bloc += f"- {cheval} - cote {cote:.1f}, proba {proba:.1%}, EV {ev:+.1%}, <b>mise {mise:.0f}EUR</b>\n"
            sections_msg.append(bloc)

        if value_bets_v110place and not etat_pause.get("v110place", False):
            bloc = f"<b>v1.10-PLACE</b> ({bankroll_v110place:.0f}EUR) : " + ", ".join(f"{c} ({m:.0f}EUR)" for c, _, _, _, m, _ in value_bets_v110place) + "\n"
            sections_msg.append(bloc)

        if value_bets_v110antifav and not etat_pause.get("v110antifav", False):
            bloc = f"<b>v1.10-AntiFav</b> ({bankroll_v110antifav:.0f}EUR) : " + ", ".join(f"{c} ({m:.0f}EUR)" for c, _, _, _, m in value_bets_v110antifav) + "\n"
            sections_msg.append(bloc)

        if value_bets_v110snipercombine and not etat_pause.get("v110snipercombine", False):
            bloc = f"<b>v1.10-SniperCombine</b> ({bankroll_v110snipercombine:.0f}EUR) : " + ", ".join(f"{c} ({m:.0f}EUR)" for c, _, _, _, m in value_bets_v110snipercombine) + "\n"
            sections_msg.append(bloc)

        if value_bets_v110ecartfaible and not etat_pause.get("v110ecartfaible", False):
            bloc = f"<b>v1.10-EcartFaible</b> ({bankroll_v110ecartfaible:.0f}EUR) : " + ", ".join(f"{c} ({m:.0f}EUR)" for c, _, _, _, m in value_bets_v110ecartfaible) + "\n"
            sections_msg.append(bloc)

        if consensus_place_pick and not etat_pause.get("consensus_place", False):
            cheval, cote, proba, ev, mise = consensus_place_pick
            bloc = f"<b>Modele CONSENSUS-PLACE</b> (bankroll : {bankroll_consensus_place:.0f}EUR) :\n"
            bloc += f"- {cheval} - cote {cote:.1f}, proba {proba:.1%}, EV {ev:+.1%}, <b>mise {mise:.0f}EUR</b>\n"
            sections_msg.append(bloc)

        if couple_harville_pick and not etat_pause.get("couple_harville", False):
            bloc = f"<b>Modele COUPLE-HARVILLE</b> (bankroll : {bankroll_couple_harville:.0f}EUR) :\n"
            bloc += f"- {couple_harville_pick['cheval_1']} + {couple_harville_pick['cheval_2']} - <b>mise {couple_harville_pick['mise']:.0f}EUR</b>\n"
            sections_msg.append(bloc)

        if value_bets_place and not etat_pause.get("place", False):
            bloc = f"<b>Modele PLACE</b> (bankroll : {bankroll_place:.0f}EUR, top pick, mise fixe) :\n"
            for cheval, proba, mise in value_bets_place:
                bloc += f"- {cheval} - proba place {proba:.1%}, <b>mise {mise:.0f}EUR</b>\n"
            sections_msg.append(bloc)

        if value_bets_deux_sur_quatre and not etat_pause.get("2sur4", False):
            bloc = f"<b>Modele 2 SUR 4</b> (bankroll : {bankroll_2sur4:.0f}EUR, top 2, mise fixe) :\n"
            for chevaux, mise in value_bets_deux_sur_quatre:
                bloc += f"- {' + '.join(chevaux)} - <b>mise {mise:.0f}EUR</b>\n"
            sections_msg.append(bloc)

        if value_bets_trio and not etat_pause.get("trio", False):
            bloc = f"<b>Modele TRIO</b> (bankroll : {bankroll_trio:.0f}EUR, top 3, mise fixe) :\n"
            for chevaux, mise in value_bets_trio:
                bloc += f"- {' + '.join(chevaux)} - <b>mise {mise:.0f}EUR</b>\n"
            sections_msg.append(bloc)

        if value_bets_multi and not etat_pause.get("multi", False):
            bloc = f"<b>Modele {type_multi}</b> (bankroll : {bankroll_multi:.0f}EUR, top 4, mise fixe) :\n"
            for chevaux, mise, type_pari in value_bets_multi:
                bloc += f"- {' + '.join(chevaux)} - <b>mise {mise:.0f}EUR</b>\n"
            sections_msg.append(bloc)

        if value_bets_2favori and not etat_pause.get("2favori", False):
            bloc = f"<b>Modele 2E FAVORI</b> (bankroll : {bankroll_2favori:.0f}EUR) :\n"
            for cheval, cote, proba, ev, mise in value_bets_2favori:
                bloc += f"- {cheval} - cote {cote:.1f}, proba {proba:.1%}, EV {ev:+.1%}, <b>mise {mise:.0f}EUR</b>\n"
            sections_msg.append(bloc)

        if sections_msg:
            msg = f"<b>Course {course['hippodrome']} R{course['num_reunion']}C{course['num_course']}</b>\n"
            msg += f"Depart dans ~{int(minutes_avant_depart)} min\n\n"
            msg += "\n".join(sections_msg)
            envoyer_telegram(msg)

        lignes_course = []

        for cheval, cote, proba, ev, mise in value_bets_v14:
            lignes_course.append({"race_id": race_id, "modele": "v1.4", "cheval": cheval, "cote": cote, "cote_cloture": "", "ev": ev, "mise": mise, "date_detection": maintenant.isoformat()})
        for d in dutch_v14_liste:
            lignes_course.append({"race_id": race_id, "modele": "v14dutch", "cheval": d["cheval_outsider"], "cote": d["cote_outsider"], "cote_cloture": "", "ev": d["ev_outsider"], "mise": d["mise_outsider"], "date_detection": maintenant.isoformat()})
            lignes_course.append({"race_id": race_id, "modele": "v14dutch", "cheval": d["cheval_favori"], "cote": d["cote_favori"], "cote_cloture": "", "ev": "", "mise": d["mise_favori"], "date_detection": maintenant.isoformat()})
        for cheval, cote, proba, ev, mise in value_bets_v14favori:
            lignes_course.append({"race_id": race_id, "modele": "v14favori", "cheval": cheval, "cote": cote, "cote_cloture": "", "ev": ev, "mise": mise, "date_detection": maintenant.isoformat()})
        for cheval, cote, proba, ev, mise in value_bets_v14sire:
            lignes_course.append({"race_id": race_id, "modele": "v14sire", "cheval": cheval, "cote": cote, "cote_cloture": "", "ev": ev, "mise": mise, "date_detection": maintenant.isoformat()})
        for cheval, cote, proba, ev, mise in value_bets_v15:
            lignes_course.append({"race_id": race_id, "modele": "v1.5", "cheval": cheval, "cote": cote, "cote_cloture": "", "ev": ev, "mise": mise, "date_detection": maintenant.isoformat()})
        for cheval, cote, proba, ev, mise, d4 in value_bets_v18:
            lignes_course.append({"race_id": race_id, "modele": "v1.8", "cheval": cheval, "cote": cote, "cote_cloture": "", "ev": ev, "mise": mise, "date_detection": maintenant.isoformat()})
        for cheval, cote, proba, ev, mise in value_bets_v14recalibre:
            lignes_course.append({"race_id": race_id, "modele": "v14recalibre", "cheval": cheval, "cote": cote, "cote_cloture": "", "ev": ev, "mise": mise, "date_detection": maintenant.isoformat()})
        for cheval, cote, proba, ev, mise in value_bets_v15recalibre:
            lignes_course.append({"race_id": race_id, "modele": "v15recalibre", "cheval": cheval, "cote": cote, "cote_cloture": "", "ev": ev, "mise": mise, "date_detection": maintenant.isoformat()})
        for cheval, cote, proba, ev, mise, d4 in value_bets_v18recalibre:
            lignes_course.append({"race_id": race_id, "modele": "v18recalibre", "cheval": cheval, "cote": cote, "cote_cloture": "", "ev": ev, "mise": mise, "date_detection": maintenant.isoformat()})
        for cheval, cote, proba, ev, mise, d4 in value_bets_v110recalibre:
            lignes_course.append({"race_id": race_id, "modele": "v110recalibre", "cheval": cheval, "cote": cote, "cote_cloture": "", "ev": ev, "mise": mise, "date_detection": maintenant.isoformat()})
        for cheval, cote, proba, ev, mise, d4 in value_bets_v110:
            lignes_course.append({"race_id": race_id, "modele": "v1.10", "cheval": cheval, "cote": cote, "cote_cloture": "", "ev": ev, "mise": mise, "date_detection": maintenant.isoformat()})
        for d in dutch_v110_liste:
            lignes_course.append({"race_id": race_id, "modele": "v110dutch", "cheval": d["cheval_outsider"], "cote": d["cote_outsider"], "cote_cloture": "", "ev": d["ev_outsider"], "mise": d["mise_outsider"], "date_detection": maintenant.isoformat()})
            lignes_course.append({"race_id": race_id, "modele": "v110dutch", "cheval": d["cheval_favori"], "cote": d["cote_favori"], "cote_cloture": "", "ev": "", "mise": d["mise_favori"], "date_detection": maintenant.isoformat()})
        for cheval, cote, proba, ev, mise, d4 in value_bets_v110favori:
            lignes_course.append({"race_id": race_id, "modele": "v110favori", "cheval": cheval, "cote": cote, "cote_cloture": "", "ev": ev, "mise": mise, "date_detection": maintenant.isoformat()})
        for cheval, cote, proba, ev, mise in value_bets_v110d4:
            lignes_course.append({"race_id": race_id, "modele": "v110d4", "cheval": cheval, "cote": cote, "cote_cloture": "", "ev": ev, "mise": mise, "date_detection": maintenant.isoformat()})
        for cheval, cote, proba, ev, mise in value_bets_v110sniper:
            lignes_course.append({"race_id": race_id, "modele": "v110sniper", "cheval": cheval, "cote": cote, "cote_cloture": "", "ev": ev, "mise": mise, "date_detection": maintenant.isoformat()})
        for cheval, cote, proba, ev, mise, num_pmu in value_bets_v110place:
            lignes_course.append({"race_id": race_id, "modele": "v110place", "cheval": cheval, "cote": cote, "cote_cloture": "", "ev": ev, "mise": mise, "date_detection": maintenant.isoformat()})
        for cheval, cote, proba, ev, mise in value_bets_v110antifav:
            lignes_course.append({"race_id": race_id, "modele": "v110antifav", "cheval": cheval, "cote": cote, "cote_cloture": "", "ev": ev, "mise": mise, "date_detection": maintenant.isoformat()})
        for cheval, cote, proba, ev, mise in value_bets_v110snipercombine:
            lignes_course.append({"race_id": race_id, "modele": "v110snipercombine", "cheval": cheval, "cote": cote, "cote_cloture": "", "ev": ev, "mise": mise, "date_detection": maintenant.isoformat()})
        for cheval, cote, proba, ev, mise in value_bets_v110ecartfaible:
            lignes_course.append({"race_id": race_id, "modele": "v110ecartfaible", "cheval": cheval, "cote": cote, "cote_cloture": "", "ev": ev, "mise": mise, "date_detection": maintenant.isoformat()})
        if consensus_place_pick:
            cheval, cote, proba, ev, mise = consensus_place_pick
            lignes_course.append({"race_id": race_id, "modele": "consensus_place", "cheval": cheval, "cote": cote, "cote_cloture": "", "ev": ev, "mise": mise, "date_detection": maintenant.isoformat()})
        if couple_harville_pick:
            chevaux_str = f"{couple_harville_pick['cheval_1']}|{couple_harville_pick['cheval_2']}"
            nums_str = f"{couple_harville_pick['num_pmu_1']}-{couple_harville_pick['num_pmu_2']}"
            lignes_course.append({"race_id": race_id, "modele": "couple_harville", "cheval": chevaux_str, "cote": nums_str, "cote_cloture": "", "ev": "", "mise": couple_harville_pick["mise"], "date_detection": maintenant.isoformat()})
        for cheval, proba, mise in value_bets_place:
            lignes_course.append({"race_id": race_id, "modele": "place", "cheval": cheval, "cote": "", "cote_cloture": "", "ev": "", "mise": mise, "date_detection": maintenant.isoformat()})
        for chevaux, mise in value_bets_deux_sur_quatre:
            lignes_course.append({"race_id": race_id, "modele": "2sur4", "cheval": "|".join(chevaux), "cote": "", "cote_cloture": "", "ev": "", "mise": mise, "date_detection": maintenant.isoformat()})
        for chevaux, mise in value_bets_trio:
            lignes_course.append({"race_id": race_id, "modele": "trio", "cheval": "|".join(chevaux), "cote": "", "cote_cloture": "", "ev": "", "mise": mise, "date_detection": maintenant.isoformat()})
        for chevaux, mise, type_pari in value_bets_multi:
            lignes_course.append({"race_id": race_id, "modele": "multi", "cheval": "|".join(chevaux), "cote": type_pari, "cote_cloture": "", "ev": "", "mise": mise, "date_detection": maintenant.isoformat()})
        for cheval, cote, proba, ev, mise in value_bets_2favori:
            lignes_course.append({"race_id": race_id, "modele": "2favori", "cheval": cheval, "cote": cote, "cote_cloture": "", "ev": ev, "mise": mise, "date_detection": maintenant.isoformat()})

        # ---------------------------------------------------------------
        # CORRIGE (17 sept, soir) : LIMITE D'EXPOSITION DYNAMIQUE PAR
        # CHEVAL, EN POURCENTAGE DE LA BANKROLL COMBINEE - remplace
        # l'ancien plafond fixe de 300EUR. Pour chaque cheval, calcule
        # la SOMME des bankrolls des strategies (uniques) qui parient
        # dessus dans cette course, et plafonne l'exposition totale a
        # FRACTION_EXPOSITION_CHEVAL de cette somme - jamais de rejet
        # complet, toujours une reduction proportionnelle.
        # ---------------------------------------------------------------
        exposition_par_cheval = {}
        modeles_par_cheval = {}
        for ligne in lignes_course:
            cle = ligne["cheval"]
            exposition_par_cheval[cle] = exposition_par_cheval.get(cle, 0) + ligne["mise"]
            modeles_par_cheval.setdefault(cle, set()).add(ligne["modele"])

        for cle, exposition_totale in exposition_par_cheval.items():
            bankroll_combinee = sum(bankrolls_par_modele.get(m, 0) for m in modeles_par_cheval[cle])
            plafond_dynamique = bankroll_combinee * FRACTION_EXPOSITION_CHEVAL
            if plafond_dynamique > 0 and exposition_totale > plafond_dynamique:
                facteur_reduction = plafond_dynamique / exposition_totale
                for ligne in lignes_course:
                    if ligne["cheval"] == cle:
                        ligne["mise"] = arrondir_mise_euro(ligne["mise"] * facteur_reduction)

        log_paris.extend(lignes_course)

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
                "race_id", "modele", "cheval", "cote", "cote_cloture", "ev", "mise",
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
        envoyer_telegram(f"Erreur dans verifier_a_venir.py\n\n{e}\n\n{detail_echappe}")
        raise
