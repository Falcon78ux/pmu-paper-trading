"""
=============================================================================
VERIFIER_RESULTATS.PY - Compare aux resultats reels, met a jour l'historique
=============================================================================
A lancer regulierement (toutes les 15-30 min) via GitHub Actions, apres
verifier_a_venir.py. Pour chaque course deja notifiee dont le resultat est
maintenant disponible :
1. Met a jour paris_virtuels.csv avec le resultat (gagnant/perdant) et le
   gain/perte REEL EN EUROS de chaque pari virtuel logue (mise deja
   calculee lors de la detection)
2. Met a jour la bankroll virtuelle (une par modele, v1.4/v1.5)
3. Met a jour l'etat glissant (driver_forme, biais_hippodrome,
   speed_figure) pour TOUS les partants de la course
4. Envoie un message Telegram recapitulatif de la course, avec les
   montants et la bankroll a jour
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
    get_bankroll, mettre_a_jour_bankroll,
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

    bankroll_v14, chemin_bankroll_v14 = get_bankroll(RACINE, "v14")
    bankroll_v15, chemin_bankroll_v15 = get_bankroll(RACINE, "v15")

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
            continue

        infos_course = courses_notifiees.get(race_id, {})
        hippodrome_nom = infos_course.get("hippodrome") if isinstance(infos_course, dict) else None

        # --- 1. Mise a jour de l'etat glissant pour TOUS les partants ---
        valides = []
        for p in participants:
            incident = p.get("incident", "") or ""
            rk = p.get("reductionKilometrique")
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
            track_variant = sorted(rks_ajustees)[len(rks_ajustees) // 2]

            for p, rk_adj in zip(valides, rks_ajustees):
                sf_brut = track_variant - rk_adj
                sf_brut = max(-plafond_ecart, min(plafond_ecart, sf_brut))
                maj_cheval(etat_chevaux, p.get("nom"), sf_brut)

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

        # --- 2. Mise a jour de paris_virtuels.csv + calcul du gain en euros ---
        # La bankroll est capturee JUSTE APRES chaque pari individuel, dans
        # l'ordre, pour que le message Telegram montre la vraie progression
        # pari par pari plutot que le seul etat final de la course.
        lignes_message = []
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
            mise = float(l.get("mise", 0) or 0)

            gain_euros = mise * (cote - 1) if gagnant else -mise
            l["resultat"] = "GAGNANT" if gagnant else "PERDANT"
            l["gain_euros"] = f"{gain_euros:.2f}"

            if l["modele"] == "v1.4":
                bankroll_v14 += gain_euros
                bankroll_apres = bankroll_v14
            elif l["modele"] == "v1.5":
                bankroll_v15 += gain_euros
                bankroll_apres = bankroll_v15
            else:
                bankroll_apres = None

            emoji = "✅" if gagnant else "❌"
            lignes_message.append(
                f"{emoji} [{l['modele']}] {cheval_parie} (cote {cote:.1f}, mise {mise:.2f}€) "
                f"— gain {gain_euros:+.2f}€ | bankroll {l['modele']} : {bankroll_apres:.2f}€"
            )

        if lignes_message:
            msg = f"🏁 <b>Resultat course {race_id}</b>\n\n" + "\n".join(lignes_message)
            envoyer_telegram(msg)

        courses_traitees_ce_run.append(race_id)

    # --- Sauvegarde ---
    with open(chemin_log, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "race_id", "modele", "cheval", "cote", "ev", "mise",
            "date_detection", "resultat", "gain_euros",
        ])
        writer.writeheader()
        for l in lignes:
            writer.writerow(l)

    mettre_a_jour_bankroll(chemin_bankroll_v14, bankroll_v14)
    mettre_a_jour_bankroll(chemin_bankroll_v15, bankroll_v15)

    sauvegarder_json(f"{RACINE}/etat_drivers.json", etat_drivers)
    sauvegarder_json(f"{RACINE}/etat_hippodromes.json", etat_hippodromes)
    sauvegarder_json(f"{RACINE}/etat_chevaux.json", etat_chevaux)

    print(f"{len(courses_traitees_ce_run)} courses traitees dans ce run.")
    print(f"Bankroll v1.4 : {bankroll_v14:.2f}EUR | Bankroll v1.5 : {bankroll_v15:.2f}EUR")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        detail = traceback.format_exc()[-500:]
        envoyer_telegram(f"🔴 <b>Erreur dans verifier_resultats.py</b>\n\n{e}\n\n<code>{detail}</code>")
        raise
