"""
=============================================================================
CORRECTION HISTORIQUE PONCTUELLE (26 aout 2026) - a executer UNE SEULE
FOIS puis a supprimer
=============================================================================
Corrige retroactivement les paris trio/couple_harville deja resolus en
PERDANT dans paris_virtuels.csv, en re-verifiant avec la logique
corrigee (Trio degrade a 2 chevaux = sous-ensemble gagnant, Couple
Gagnant avec combinaisons multiples = cherche dans toute la liste).
Met a jour paris_virtuels.csv ET les bankrolls trio/couple_harville en
consequence.
=============================================================================
"""

import sys
import os
import csv
import time
import requests
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(__file__))
from commun import charger_json, sauvegarder_json, mettre_a_jour_bankroll, get_bankroll

RACINE = os.path.join(os.path.dirname(__file__), "..")
DELAI_ENTRE_REQUETES = 0.2


def recuperer_participants(date_str, num_reunion, num_course):
    url = (
        f"https://online.turfinfo.api.pmu.fr/rest/client/61/programme/"
        f"{date_str}/R{num_reunion}/C{num_course}/participants"
    )
    r = requests.get(url, timeout=20)
    r.raise_for_status()
    return r.json().get("participants", [])


def recuperer_rapports(date_str, num_reunion, num_course):
    url = f"https://online.turfinfo.api.pmu.fr/rest/client/1/programme/{date_str}/R{num_reunion}/C{num_course}/rapports-definitifs"
    r = requests.get(url, timeout=15)
    if r.status_code != 200:
        return None
    return r.json()


def extraire_trio_corrige(data):
    for pari in data:
        if pari.get("typePari") != "TRIO":
            continue
        mise_base = pari.get("miseBase", 200)
        rapports = pari.get("rapports", [])
        if not rapports:
            continue
        combinaison = rapports[0].get("combinaison", "")
        if "NP" in str(combinaison):
            return None, None, False
        try:
            ensemble = frozenset(int(x) for x in str(combinaison).split("-"))
        except Exception:
            return None, None, False
        dividende = rapports[0].get("dividendePourUneMiseDeBase")
        if dividende is not None:
            return ensemble, dividende / mise_base, len(ensemble) < 3
    return None, None, False


def extraire_couple_corrige(data):
    resultats = []
    for pari in data:
        type_pari = pari.get("typePari", "")
        if type_pari not in ("COUPLE_GAGNANT", "COUPLE_ORDRE"):
            continue
        mise_base = pari.get("miseBase", 200)
        for rap in pari.get("rapports", []):
            combinaison = rap.get("combinaison", "")
            try:
                ensemble = frozenset(int(x) for x in str(combinaison).split("-"))
            except Exception:
                continue
            dividende = rap.get("dividendePourUneMiseDeBase")
            if dividende is not None:
                resultats.append((ensemble, dividende / mise_base))
        break
    return resultats


def main():
    chemin_log = f"{RACINE}/paris_virtuels.csv"
    with open(chemin_log, "r", encoding="utf-8") as f:
        lignes = list(csv.DictReader(f))
    fieldnames = list(lignes[0].keys()) if lignes else []

    candidats = [
        l for l in lignes
        if l.get("modele") in ("trio", "couple_harville") and l.get("resultat") == "PERDANT"
    ]
    print(f"Candidats a re-verifier : {len(candidats)}")

    race_ids_uniques = sorted(set(l["race_id"] for l in candidats))
    print(f"Courses uniques concernees : {len(race_ids_uniques)}")

    cache_participants = {}
    cache_rapports = {}

    delta_trio = 0.0
    delta_couple = 0.0
    nb_corriges_trio = 0
    nb_corriges_couple = 0

    for i, race_id in enumerate(race_ids_uniques):
        parts = race_id.split("_")
        date_str, num_reunion, num_course = parts[0], parts[1], parts[2]

        try:
            if race_id not in cache_rapports:
                cache_rapports[race_id] = recuperer_rapports(date_str, num_reunion, num_course)
            data = cache_rapports[race_id]
            if data is None:
                continue

            lignes_trio = [l for l in candidats if l["race_id"] == race_id and l["modele"] == "trio"]
            lignes_couple = [l for l in candidats if l["race_id"] == race_id and l["modele"] == "couple_harville"]

            if lignes_trio:
                if race_id not in cache_participants:
                    cache_participants[race_id] = recuperer_participants(date_str, num_reunion, num_course)
                participants = cache_participants[race_id]
                trio_ensemble, trio_cote, trio_degrade = extraire_trio_corrige(data)
                if trio_ensemble is not None and trio_cote is not None:
                    for l in lignes_trio:
                        chevaux = l["cheval"].split("|")
                        try:
                            ensemble_parie = frozenset(
                                int(next(p.get("numPmu") for p in participants if p.get("nom") == c))
                                for c in chevaux
                            )
                        except Exception:
                            continue
                        if trio_degrade:
                            a_gagne = trio_ensemble.issubset(ensemble_parie)
                        else:
                            a_gagne = ensemble_parie == trio_ensemble
                        if a_gagne:
                            mise = float(l.get("mise", 0) or 0)
                            ancien_gain = float(l.get("gain_euros", 0) or 0)
                            nouveau_gain = mise * (trio_cote - 1)
                            l["resultat"] = "GAGNANT"
                            l["gain_euros"] = f"{nouveau_gain:.2f}"
                            l["cote"] = f"{trio_cote:.2f}"
                            delta_trio += (nouveau_gain - ancien_gain)
                            nb_corriges_trio += 1
                            print(f"  TRIO corrige : {race_id} {chevaux} -> GAGNANT ({nouveau_gain:+.2f}EUR, etait {ancien_gain:+.2f}EUR)")

            if lignes_couple:
                couple_liste = extraire_couple_corrige(data)
                if couple_liste:
                    for l in lignes_couple:
                        chevaux = l["cheval"].split("|")
                        try:
                            num_pmu_1, num_pmu_2 = (int(x) for x in l["cote"].split("-"))
                            notre_pick = frozenset([num_pmu_1, num_pmu_2])
                        except Exception:
                            continue
                        for ensemble_gagnant, cote_gagnante in couple_liste:
                            if notre_pick == ensemble_gagnant:
                                mise = float(l.get("mise", 0) or 0)
                                ancien_gain = float(l.get("gain_euros", 0) or 0)
                                nouveau_gain = mise * (cote_gagnante - 1)
                                l["resultat"] = "GAGNANT"
                                l["gain_euros"] = f"{nouveau_gain:.2f}"
                                l["cote"] = f"{cote_gagnante:.2f}"
                                delta_couple += (nouveau_gain - ancien_gain)
                                nb_corriges_couple += 1
                                print(f"  COUPLE corrige : {race_id} {chevaux} -> GAGNANT ({nouveau_gain:+.2f}EUR, etait {ancien_gain:+.2f}EUR)")
                                break

        except Exception as e:
            print(f"Erreur sur {race_id} : {e}")

        time.sleep(DELAI_ENTRE_REQUETES)
        if (i + 1) % 50 == 0:
            print(f"Progression : {i+1}/{len(race_ids_uniques)}")

    print(f"\n=== RESUME ===")
    print(f"Trio : {nb_corriges_trio} paris corriges, delta bankroll = {delta_trio:+.2f}EUR")
    print(f"Couple-Harville : {nb_corriges_couple} paris corriges, delta bankroll = {delta_couple:+.2f}EUR")

    with open(chemin_log, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for l in lignes:
            writer.writerow(l)
    print(f"\npar is_virtuels.csv mis a jour.")

    if delta_trio != 0:
        bankroll_trio, chemin_trio = get_bankroll(RACINE, "trio")
        mettre_a_jour_bankroll(chemin_trio, bankroll_trio + delta_trio)
        print(f"bankroll_trio.json mis a jour : {bankroll_trio:.2f} -> {bankroll_trio + delta_trio:.2f}")

    if delta_couple != 0:
        bankroll_couple, chemin_couple = get_bankroll(RACINE, "couple_harville")
        mettre_a_jour_bankroll(chemin_couple, bankroll_couple + delta_couple)
        print(f"bankroll_couple_harville.json mis a jour : {bankroll_couple:.2f} -> {bankroll_couple + delta_couple:.2f}")

    print("\nCorrection terminee.")


if __name__ == "__main__":
    main()
