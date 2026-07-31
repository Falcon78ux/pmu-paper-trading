"""
=============================================================================
VERIFIER_RESULTATS.PY - Compare aux resultats reels, met a jour l'historique
=============================================================================
TRIO ajoute : requete rapports-definitifs (type TRIO, non ordonne), gain
si les 3 chevaux paries sont EXACTEMENT le trio de tete (rapport avec NP
ignore comme non exploitable).

MULTI/MINI_MULTI ajoute : verite terrain (top 4 reel) prise directement
depuis ordreArrivee (comme pour 2sur4), le rapport sert uniquement a
recuperer le montant du palier le plus eleve (correspondant a notre
strategie "exactement 4 chevaux choisis").
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
    maj_driver, maj_hippodrome, maj_cheval, maj_cheval_corde,
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


def recuperer_rapports_definitifs(date_str, num_reunion, num_course):
    """Renvoie le JSON brut complet des rapports definitifs, ou None si
    pas encore disponible. Fonction generique reutilisee par place,
    2sur4, trio et multi."""
    url = f"https://online.turfinfo.api.pmu.fr/rest/client/1/programme/{date_str}/R{num_reunion}/C{num_course}/rapports-definitifs"
    try:
        r = requests.get(url, timeout=20)
        if r.status_code != 200:
            return None
        return r.json()
    except Exception:
        return None


def extraire_rapports_place(data):
    resultat = {}
    for pari in data:
        if pari.get("typePari") != "SIMPLE_PLACE":
            continue
        mise_base = pari.get("miseBase", 200)
        for rapport in pari.get("rapports", []):
            try:
                num_pmu = int(rapport.get("combinaison"))
            except (TypeError, ValueError):
                continue
            dividende = rapport.get("dividendePourUneMiseDeBase")
            if dividende is not None:
                resultat[num_pmu] = dividende / mise_base
    return resultat


def extraire_cote_deux_sur_quatre(data):
    for pari in data:
        if pari.get("typePari") != "DEUX_SUR_QUATRE":
            continue
        mise_base = pari.get("miseBase", 100)
        rapports = pari.get("rapports", [])
        if rapports:
            dividende = rapports[0].get("dividendePourUneMiseDeBase")
            if dividende is not None:
                return dividende / mise_base
    return None


def extraire_trio(data):
    """Renvoie (ensemble_gagnant, cote) pour TRIO (non ordonne), ou
    (None, None) si non disponible ou si la combinaison contient un NP
    (non-partant, non exploitable proprement)."""
    for pari in data:
        if pari.get("typePari") != "TRIO":
            continue
        mise_base = pari.get("miseBase", 200)
        rapports = pari.get("rapports", [])
        if not rapports:
            continue
        combinaison = rapports[0].get("combinaison", "")
        if "NP" in str(combinaison):
            return None, None  # non exploitable, on ignore ce pari
        try:
            ensemble = frozenset(int(x) for x in str(combinaison).split("-"))
        except Exception:
            return None, None
        dividende = rapports[0].get("dividendePourUneMiseDeBase")
        if dividende is not None:
            return ensemble, dividende / mise_base
    return None, None


def extraire_cote_multi(data, type_pari):
    """MULTI ou MINI_MULTI - renvoie la cote du palier le PLUS ELEVE
    (correspondant a une selection de 4 chevaux exactement)."""
    meilleure_cote = None
    for pari in data:
        if pari.get("typePari") != type_pari:
            continue
        mise_base = pari.get("miseBase", 300)
        for rapport in pari.get("rapports", []):
            dividende = rapport.get("dividendePourUneMiseDeBase")
            if dividende is not None:
                cote = dividende / mise_base
                if meilleure_cote is None or cote > meilleure_cote:
                    meilleure_cote = cote
    return meilleure_cote


def resultat_disponible(participants):
    return any(p.get("ordreArrivee") is not None for p in participants)


def main():
    courses_notifiees = charger_json(f"{RACINE}/courses_notifiees.json", {})
    etat_drivers = charger_json(f"{RACINE}/etat_drivers.json", {})
    etat_hippodromes = charger_json(f"{RACINE}/etat_hippodromes.json", {})
    etat_chevaux = charger_json(f"{RACINE}/etat_chevaux.json", {})
    etat_chevaux_corde = charger_json(f"{RACINE}/etat_chevaux_corde.json", {})
    constantes = charger_json(f"{RACINE}/constantes.json", {})
    offset_discipline = constantes.get("offset_discipline_monte_moins_attele_ms_km", -400)
    min_partants_sf = constantes.get("min_partants_valides_speed_figure", 6)
    plafond_ecart = constantes.get("plafond_ecart_speed_figure_ms", 8000)

    bankroll_v14, chemin_bankroll_v14 = get_bankroll(RACINE, "v14")
    bankroll_v15, chemin_bankroll_v15 = get_bankroll(RACINE, "v15")
    bankroll_v18, chemin_bankroll_v18 = get_bankroll(RACINE, "v18")
    bankroll_v110, chemin_bankroll_v110 = get_bankroll(RACINE, "v110")
    bankroll_place, chemin_bankroll_place = get_bankroll(RACINE, "place")
    bankroll_2sur4, chemin_bankroll_2sur4 = get_bankroll(RACINE, "2sur4")
    bankroll_trio, chemin_bankroll_trio = get_bankroll(RACINE, "trio")
    bankroll_multi, chemin_bankroll_multi = get_bankroll(RACINE, "multi")

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
        corde_course = infos_course.get("corde", "") if isinstance(infos_course, dict) else ""

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
                if corde_course:
                    maj_cheval_corde(etat_chevaux_corde, p.get("nom"), corde_course, sf_brut)

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

        # --- 1bis. Rapports definitifs : recuperes UNE FOIS si besoin, reutilises pour tout ---
        paris_combines_en_attente = {"place", "2sur4", "trio", "multi"}
        a_un_pari_combine_en_attente = any(
            l["race_id"] == race_id and l["modele"] in paris_combines_en_attente and l.get("resultat", "") == ""
            for l in lignes
        )
        rapports_data = None
        course_combine_incomplete = False
        if a_un_pari_combine_en_attente:
            rapports_data = recuperer_rapports_definitifs(date_str, num_reunion, num_course)
            if rapports_data is None:
                course_combine_incomplete = True

        rapports_place, cote_2sur4, trio_reel_ensemble, trio_reel_cote = None, None, None, None
        if rapports_data is not None:
            rapports_place = extraire_rapports_place(rapports_data)
            cote_2sur4 = extraire_cote_deux_sur_quatre(rapports_data)
            trio_reel_ensemble, trio_reel_cote = extraire_trio(rapports_data)

        # --- Top 4 REEL de la course (verite terrain pour multi/mini_multi) ---
        top4_reel = frozenset(
            p.get("numPmu") for p in participants
            if p.get("ordreArrivee") is not None and p.get("ordreArrivee") <= 4
        )

        # --- 2. Mise a jour de paris_virtuels.csv + calcul du gain en euros ---
        lignes_message = []
        for l in lignes:
            if l["race_id"] != race_id or l.get("resultat", "") != "":
                continue

            if l["modele"] == "2sur4":
                if course_combine_incomplete:
                    continue
                chevaux_paries = l["cheval"].split("|")
                rangs = [
                    next((p.get("ordreArrivee") for p in participants if p.get("nom") == c), None)
                    for c in chevaux_paries
                ]
                a_gagne = all(r is not None and r <= 4 for r in rangs)
                mise = float(l.get("mise", 0) or 0)
                gain_euros = mise * (cote_2sur4 - 1) if a_gagne and cote_2sur4 else -mise
                l["resultat"] = "GAGNANT" if a_gagne else "PERDANT"
                l["gain_euros"] = f"{gain_euros:.2f}"
                l["cote"] = f"{cote_2sur4:.2f}" if a_gagne and cote_2sur4 else ""
                bankroll_2sur4 += gain_euros
                emoji = "✅" if a_gagne else "❌"
                lignes_message.append(f"{emoji} [2sur4] {' + '.join(chevaux_paries)} (mise {mise:.2f}€) — gain {gain_euros:+.2f}€ | bankroll 2sur4 : {bankroll_2sur4:.2f}€")
                continue

            if l["modele"] == "trio":
                if course_combine_incomplete:
                    continue
                if trio_reel_ensemble is None:
                    continue  # NP dans la combinaison reelle, ou pas de TRIO propose sur cette course
                chevaux_paries = l["cheval"].split("|")
                try:
                    ensemble_parie = frozenset(int(next(p.get("numPmu") for p in participants if p.get("nom") == c)) for c in chevaux_paries)
                except Exception:
                    continue
                a_gagne = ensemble_parie == trio_reel_ensemble
                mise = float(l.get("mise", 0) or 0)
                gain_euros = mise * (trio_reel_cote - 1) if a_gagne else -mise
                l["resultat"] = "GAGNANT" if a_gagne else "PERDANT"
                l["gain_euros"] = f"{gain_euros:.2f}"
                l["cote"] = f"{trio_reel_cote:.2f}" if a_gagne else ""
                bankroll_trio += gain_euros
                emoji = "✅" if a_gagne else "❌"
                lignes_message.append(f"{emoji} [trio] {' + '.join(chevaux_paries)} (mise {mise:.2f}€) — gain {gain_euros:+.2f}€ | bankroll trio : {bankroll_trio:.2f}€")
                continue

            if l["modele"] == "multi":
                if course_combine_incomplete:
                    continue
                type_pari_multi = l.get("cote", "MULTI")  # stocke temporairement dans "cote" par verifier_a_venir.py
                chevaux_paries = l["cheval"].split("|")
                try:
                    ensemble_parie = frozenset(int(next(p.get("numPmu") for p in participants if p.get("nom") == c)) for c in chevaux_paries)
                except Exception:
                    continue
                a_gagne = ensemble_parie == top4_reel
                cote_multi = extraire_cote_multi(rapports_data, type_pari_multi) if rapports_data else None
                mise = float(l.get("mise", 0) or 0)
                gain_euros = mise * (cote_multi - 1) if a_gagne and cote_multi else -mise
                l["resultat"] = "GAGNANT" if a_gagne else "PERDANT"
                l["gain_euros"] = f"{gain_euros:.2f}"
                l["cote"] = f"{cote_multi:.2f}" if a_gagne and cote_multi else ""
                bankroll_multi += gain_euros
                emoji = "✅" if a_gagne else "❌"
                lignes_message.append(f"{emoji} [{type_pari_multi.lower()}] {' + '.join(chevaux_paries)} (mise {mise:.2f}€) — gain {gain_euros:+.2f}€ | bankroll multi : {bankroll_multi:.2f}€")
                continue

            cheval_parie = l["cheval"]
            participant_correspondant = next((p for p in participants if p.get("nom") == cheval_parie), None)
            if participant_correspondant is None:
                continue

            if l["modele"] == "place":
                if course_combine_incomplete:
                    continue
                num_pmu = participant_correspondant.get("numPmu")
                mise = float(l.get("mise", 0) or 0)
                cote_place_reelle = rapports_place.get(num_pmu) if rapports_place else None
                a_place = cote_place_reelle is not None
                gain_euros = mise * (cote_place_reelle - 1) if a_place else -mise
                l["resultat"] = "PLACE" if a_place else "NON_PLACE"
                l["gain_euros"] = f"{gain_euros:.2f}"
                l["cote"] = f"{cote_place_reelle:.2f}" if a_place else ""
                bankroll_place += gain_euros
                emoji = "✅" if a_place else "❌"
                lignes_message.append(f"{emoji} [place] {cheval_parie} (mise {mise:.2f}€) — gain {gain_euros:+.2f}€ | bankroll place : {bankroll_place:.2f}€")
                continue

            # --- Modeles gagnant classiques (v1.4/v1.5/v1.8/v1.10) ---
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
            elif l["modele"] == "v1.8":
                bankroll_v18 += gain_euros
                bankroll_apres = bankroll_v18
            elif l["modele"] == "v1.10":
                bankroll_v110 += gain_euros
                bankroll_apres = bankroll_v110
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

        if not course_combine_incomplete:
            courses_traitees_ce_run.append(race_id)

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
    mettre_a_jour_bankroll(chemin_bankroll_v18, bankroll_v18)
    mettre_a_jour_bankroll(chemin_bankroll_v110, bankroll_v110)
    mettre_a_jour_bankroll(chemin_bankroll_place, bankroll_place)
    mettre_a_jour_bankroll(chemin_bankroll_2sur4, bankroll_2sur4)
    mettre_a_jour_bankroll(chemin_bankroll_trio, bankroll_trio)
    mettre_a_jour_bankroll(chemin_bankroll_multi, bankroll_multi)

    sauvegarder_json(f"{RACINE}/etat_drivers.json", etat_drivers)
    sauvegarder_json(f"{RACINE}/etat_hippodromes.json", etat_hippodromes)
    sauvegarder_json(f"{RACINE}/etat_chevaux.json", etat_chevaux)
    sauvegarder_json(f"{RACINE}/etat_chevaux_corde.json", etat_chevaux_corde)

    print(f"{len(courses_traitees_ce_run)} courses traitees dans ce run.")
    print(f"v1.4:{bankroll_v14:.2f} v1.5:{bankroll_v15:.2f} v1.8:{bankroll_v18:.2f} v1.10:{bankroll_v110:.2f} place:{bankroll_place:.2f} 2sur4:{bankroll_2sur4:.2f} trio:{bankroll_trio:.2f} multi:{bankroll_multi:.2f}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        detail = traceback.format_exc()[-500:]
        detail_echappe = detail.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        envoyer_telegram(f"🔴 <b>Erreur dans verifier_resultats.py</b>\n\n{e}\n\n<code>{detail_echappe}</code>")
        raise
