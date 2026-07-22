"""
=============================================================================
VERIFIER_RESULTATS.PY - Compare aux resultats reels, met a jour l'historique
=============================================================================
A lancer regulierement (toutes les 15-30 min) via GitHub Actions, apres
verifier_a_venir.py. Pour chaque course deja notifiee dont le resultat est
maintenant disponible :
1. Met a jour paris_virtuels.csv avec le resultat (gagnant/perdant) et le
   gain/perte de chaque pari virtuel logue
2. Met a jour l'etat glissant (driver_forme, biais_hippodrome,
   speed_figure) pour TOUS les partants de la course (pas seulement ceux
   sur lesquels on a "parie") - pour que le systeme continue d'apprendre
   correctement, comme le faisait le backtest original
3. Envoie un message Telegram recapitulatif de la course
=============================================================================
"""

import sys
import os
import csv
from datetime import datetime, timezone

import requests

sys.path.insert(0, os.path.dirname(__file__))
from commun import (
    charger_json, sauvegarder_json, envoyer_telegram,
    maj_driver, maj_hippodrome, maj_cheval,
)

RACINE = os.path.join(os.path.dirname(__file__), "..")

INCIDENTS_A_EXCLURE = {
    "DISQUALIFIE_POUR_ALLURE_IRREGULIERE", "NON_PARTANT", "DISTANCE",
    "ARRETE", "DISQUALIFIE_POTEAU_GALOP", "TOMBE", "RESTE_AU_POTEAU",
}


def recuperer_participants(date_str, num_reunion, num_course):
    url = (
        f"https://online.turfinfo.api.pmu.fr/rest/client/61/programme/"
        f"{date_str}/R{num_reunion}/C{num_course}/participants"
    )
    r = requests.get(url, timeout=20)
    r.raise_for_status()
    return r.json().get("participants", [])


def resultat_disponible(participants):
    """Considere le resultat comme disponible si au moins un participant a un rang d'arrivee renseigne."""
    return any(p.get("ordreArrivee") is not None for p in participants)


def main():
    courses_notifiees = charger_json(f"{RACINE}/courses_notifiees.json", {})
    etat_drivers = charger_json(f"{RACINE}/etat_drivers.json", {})
    etat_hippodromes = charger_json(f"{RACINE}/etat_hippodromes.json", {})
    etat_chevaux = charger_json(f"{RACINE}/etat_chevaux.json", {})
    constantes = charger_json(f"{RACINE}/constantes.json", {})
    offset_discipline = constantes.get("offset_discipline_monte_moins_attele_ms_km", -400)
    min_partants_sf = constantes.get("min_partants_valides_speed_figure", 6)
    plafond_ecart = constantes.get("plafond_ecart_speed_figure_ms", 8000)

    chemin_log = f"{RACINE}/paris_virtuels.csv"
    if not os.path.exists(chemin_log):
        print("Aucun paris_virtuels.csv - rien a traiter.")
        return

    with open(chemin_log, "r", encoding="utf-8") as f:
        lignes = list(csv.DictReader(f))

    races_en_attente = sorted(set(
        l["race_id"] for l in lignes if l.get("resultat", "") == ""
    ))

    courses_traitees_ce_run = []

    for race_id in races_en_attente:
        parts = race_id.split("_")
        date_str, num_reunion, num_course = parts[0], parts[1], parts[2]

        try:
            participants = recuperer_participants(date_str, num_reunion, num_course)
        except Exception as e:
            print(f"Erreur recuperation resultat {race_id} : {e}")
            continue

        if not resultat_disponible(participants):
            continue  # pas encore couru ou resultat pas encore publie

        infos_course = courses_notifiees.get(race_id, {})
        hippodrome_nom = infos_course.get("hippodrome") if isinstance(infos_course, dict) else None

        # --- 1. Mise a jour de l'etat glissant pour TOUS les partants ---
        valides = []
        for p in participants:
            incident = p.get("incident", "") or ""
            rk = p.get("reductionKilometrique")
            allure = p.get("allure")
            if incident in INCIDENTS_A_EXCLURE or rk is None:
                continue
            valides.append(p)

        if len(valides) >= min_partants_sf:
            rks_ajustees = []
            for p in valides:
                rk = p["reductionKilometrique"]
                if p.get("allure") == "MONTE" or (p.get("discipline") == "MONTE"):
                    rk = rk - offset_discipline
                rks_ajustees.append(rk)
            track_variant = sorted(rks_ajustees)[len(rks_ajustees) // 2]  # mediane approx

            for p, rk_adj in zip(valides, rks_ajustees):
                sf_brut = track_variant - rk_adj
                sf_brut = max(-plafond_ecart, min(plafond_ecart, sf_brut))
                maj_cheval(etat_chevaux, p.get("nom"), sf_brut)

        # --- Mise a jour driver_forme et biais_hippodrome pour TOUS les partants avec cote connue ---
        somme_ecart_course = 0.0
        nb_partants_course = 0
        for p in participants:
            rang = p.get("ordreArrivee")
            if rang is None:
                continue
            gagnant = 1 if rang == 1 else 0
            driver = p.get("driver") or p.get("entraineur")
            if driver:
                maj_driver(etat_drivers, driver, gagnant)

            cote = None
            rapport = p.get("dernierRapportDirect")
            if rapport and rapport.get("typePari") == "SIMPLE_GAGNANT":
                cote = rapport.get("rapport")
            if cote and cote > 1:
                ecart = gagnant - (1 / cote)
                somme_ecart_course += ecart
                nb_partants_course += 1

        if nb_partants_course > 0 and hippodrome_nom:
            maj_hippodrome(etat_hippodromes, hippodrome_nom, somme_ecart_course, nb_partants_course)

        # --- 2. Mise a jour de paris_virtuels.csv avec le resultat de chaque pari logue ---
        gains_msg = []
        for l in lignes:
            if l["race_id"] != race_id or l.get("resultat", "") != "":
                continue
            cheval_parie = l["cheval"]
            participant_correspondant = next((p for p in participants if p.get("nom") == cheval_parie), None)
            if participant_correspondant is None:
                continue
            rang = participant_correspondant.get("ordreArrivee")
            gagnant = rang == 1
            cote = float(l["cote"])
            gain = (cote - 1) if gagnant else -1  # en unites de mise (1 unite = mise virtuelle)
            l["resultat"] = "GAGNANT" if gagnant else "PERDANT"
            l["gain"] = f"{gain:.2f}"
            gains_msg.append((l["modele"], cheval_parie, cote, gagnant, gain))

        if gains_msg:
            msg = f"🏁 <b>Resultat course {race_id}</b>\n\n"
            for modele, cheval, cote, gagnant, gain in gains_msg:
                emoji = "✅" if gagnant else "❌"
                msg += f"{emoji} [{modele}] {cheval} (cote {cote:.1f}) — gain unitaire {gain:+.2f}\n"
            envoyer_telegram(msg)

        courses_traitees_ce_run.append(race_id)

    # --- Sauvegarde de tous les etats mis a jour ---
    with open(chemin_log, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["race_id", "modele", "cheval", "cote", "ev", "date_detection", "resultat", "gain"])
        writer.writeheader()
        for l in lignes:
            writer.writerow(l)

    sauvegarder_json(f"{RACINE}/etat_drivers.json", etat_drivers)
    sauvegarder_json(f"{RACINE}/etat_hippodromes.json", etat_hippodromes)
    sauvegarder_json(f"{RACINE}/etat_chevaux.json", etat_chevaux)

    print(f"{len(courses_traitees_ce_run)} courses traitees dans ce run.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        detail = traceback.format_exc()[-500:]
        envoyer_telegram(f"🔴 <b>Erreur dans verifier_resultats.py</b>\n\n{e}\n\n<code>{detail}</code>")
        raise
