"""
=============================================================================
VERIFIER_RESULTATS_GALOP.PY - Compare aux resultats reels, met a jour
la forme du jockey, resout les paris galop
=============================================================================
ENTIEREMENT SEPARE du trot. SIMPLIFIE (14 sept, apres-midi) : plus
aucune dependance au scraping PDF McLloyd (redk retire du modele final
suite a une investigation rigoureuse) - tout passe desormais par
l'API PMU officielle, comme le trot. Plus de delai 48h a gerer pour
un PDF externe, plus simple et plus fiable.
=============================================================================
"""

import sys
import os
import csv
from datetime import datetime, timezone

import requests

sys.path.insert(0, os.path.dirname(__file__))
from commun_galop import (
    charger_json, sauvegarder_json, envoyer_telegram_galop,
    maj_jockey_forme_galop, get_bankroll_galop, mettre_a_jour_bankroll_galop,
)

RACINE = os.path.join(os.path.dirname(__file__), "..")

DELAI_ABANDON_HEURES = 48


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


def recuperer_rapports_definitifs(date_str, num_reunion, num_course):
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


def delai_depasse(date_detection_iso, heures=DELAI_ABANDON_HEURES):
    try:
        date_detect = datetime.fromisoformat(date_detection_iso)
        return (datetime.now(timezone.utc) - date_detect).total_seconds() > heures * 3600
    except (ValueError, TypeError):
        return False


def main():
    etat_drivers_galop = charger_json(f"{RACINE}/etat_drivers_galop.json", {})
    etat_pause_galop = charger_json(f"{RACINE}/etat_pause_galop.json", {})

    bankroll_ev, chemin_bankroll_ev = get_bankroll_galop(RACINE, "galopev")
    bankroll_place, chemin_bankroll_place = get_bankroll_galop(RACINE, "galopplace")

    chemin_log = f"{RACINE}/paris_virtuels_galop.csv"
    if not os.path.exists(chemin_log):
        print("Aucun paris_virtuels_galop.csv - rien a traiter.")
        return

    with open(chemin_log, "r", encoding="utf-8") as f:
        lignes = list(csv.DictReader(f))

    races_en_attente = sorted(set(
        l["race_id"] for l in lignes if l.get("resultat", "") == ""
    ))

    for race_id in races_en_attente:
        parts = race_id.split("_")
        date_str, num_reunion, num_course = parts[0], parts[1], parts[2]

        try:
            participants = recuperer_participants(date_str, num_reunion, num_course)
        except Exception as e:
            print(f"Erreur recuperation resultat galop {race_id} : {e}")
            continue

        if not resultat_disponible(participants):
            continue

        rang_par_nom = {
            p.get("nom"): p.get("ordreArrivee")
            for p in participants if p.get("ordreArrivee") is not None
        }

        # --- Met a jour la forme du jockey pour TOUS les participants ---
        for p in participants:
            rang = p.get("ordreArrivee")
            if rang is None:
                continue
            gagnant = 1 if rang == 1 else 0
            driver = p.get("driver") or p.get("entraineur")
            if driver:
                maj_jockey_forme_galop(etat_drivers_galop, driver, gagnant)

        lignes_du_pari_en_attente = [l for l in lignes if l["race_id"] == race_id and l.get("resultat", "") == ""]

        # --- Rapports Place (necessaire pour resoudre galopplace) ---
        a_un_pari_place_en_attente = any(l["modele"] == "galopplace" for l in lignes_du_pari_en_attente)
        rapports_place = None
        rapports_indisponibles = False
        if a_un_pari_place_en_attente:
            rapports_data = recuperer_rapports_definitifs(date_str, num_reunion, num_course)
            if rapports_data is not None:
                rapports_place = extraire_rapports_place(rapports_data)
            else:
                rapports_indisponibles = True

        lignes_message = []

        for l in lignes:
            if l["race_id"] != race_id or l.get("resultat", "") != "":
                continue

            cheval_parie = l["cheval"]
            participant_correspondant = next((p for p in participants if p.get("nom") == cheval_parie), None)
            if participant_correspondant is None:
                continue
            rang_reel = rang_par_nom.get(cheval_parie)
            if rang_reel is None:
                continue

            if l["modele"] == "galopplace":
                if rapports_indisponibles:
                    if delai_depasse(l.get("date_detection", "")):
                        l["resultat"] = "ANNULE"
                        l["gain_euros"] = "0.00"
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

                if not etat_pause_galop.get("galopplace", False):
                    emoji = "✅" if a_place else "❌"
                    lignes_message.append(f"{emoji} [galopplace] {cheval_parie} (mise {mise:.2f}EUR) — gain {gain_euros:+.2f}EUR | bankroll galopplace : {bankroll_place:.2f}EUR")
                continue

            if l["modele"] == "galopev":
                gagnant = rang_reel == 1
                cote = float(l["cote"])
                mise = float(l.get("mise", 0) or 0)
                gain_euros = mise * (cote - 1) if gagnant else -mise
                l["resultat"] = "GAGNANT" if gagnant else "PERDANT"
                l["gain_euros"] = f"{gain_euros:.2f}"
                bankroll_ev += gain_euros

                if not etat_pause_galop.get("galopev", False):
                    emoji = "✅" if gagnant else "❌"
                    lignes_message.append(f"{emoji} [galopev] {cheval_parie} (cote {cote:.1f}, mise {mise:.2f}EUR) — gain {gain_euros:+.2f}EUR | bankroll galopev : {bankroll_ev:.2f}EUR")
                continue

        if lignes_message:
            msg = f"🏁 Resultat course galop {race_id}\n\n" + "\n".join(lignes_message)
            envoyer_telegram_galop(msg)

    with open(chemin_log, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "race_id", "modele", "cheval", "cote", "cote_cloture", "ev", "mise",
            "date_detection", "resultat", "gain_euros",
        ])
        writer.writeheader()
        for l in lignes:
            writer.writerow(l)

    mettre_a_jour_bankroll_galop(chemin_bankroll_ev, bankroll_ev)
    mettre_a_jour_bankroll_galop(chemin_bankroll_place, bankroll_place)

    sauvegarder_json(f"{RACINE}/etat_drivers_galop.json", etat_drivers_galop)

    print(f"Verification resultats galop terminee.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        detail = traceback.format_exc()[-500:]
        detail_echappe = detail.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        message_erreur_echappe = str(e).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        envoyer_telegram_galop(f"🔴 Erreur dans verifier_resultats_galop.py\n\n{message_erreur_echappe}\n\n{detail_echappe}")
        raise
