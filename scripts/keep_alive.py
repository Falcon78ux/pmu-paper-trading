"""
=============================================================================
KEEP_ALIVE.PY - Battement de coeur quotidien AVEC statistiques de performance
=============================================================================
6 modeles maintenant : v1.4, v1.5, v1.8, v1.10, place, 2sur4.
=============================================================================
"""

import sys
import os
import csv
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(__file__))
from commun import charger_json, sauvegarder_json, envoyer_telegram

RACINE = os.path.join(os.path.dirname(__file__), "..")

HEURE_HEARTBEAT = 8  # heure UTC a laquelle envoyer le message quotidien


def calculer_stats_modele(lignes, nom_modele):
    sous_ensemble = [l for l in lignes if l.get("modele") == nom_modele and l.get("resultat", "") != ""]
    if not sous_ensemble:
        return None

    nb_paris = len(sous_ensemble)
    # "place" et "2sur4" utilisent GAGNANT/PERDANT (comme les autres modeles ici)
    nb_gagnants = sum(1 for l in sous_ensemble if l.get("resultat") in ("GAGNANT", "PLACE"))
    taux_victoire = nb_gagnants / nb_paris if nb_paris > 0 else 0

    mise_totale = sum(float(l.get("mise", 0) or 0) for l in sous_ensemble)
    gain_total = sum(float(l.get("gain_euros", 0) or 0) for l in sous_ensemble)
    roi = gain_total / mise_totale if mise_totale > 0 else 0

    return {
        "nb_paris": nb_paris,
        "taux_victoire": taux_victoire,
        "mise_totale": mise_totale,
        "gain_total": gain_total,
        "roi": roi,
    }


def main():
    maintenant = datetime.now(timezone.utc)
    aujourd_hui = maintenant.strftime("%Y-%m-%d")

    dernier_run = {"derniere_execution_utc": maintenant.isoformat()}
    sauvegarder_json(f"{RACINE}/dernier_run.json", dernier_run)

    etat_heartbeat = charger_json(f"{RACINE}/dernier_heartbeat.json", {})
    dernier_jour_envoye = etat_heartbeat.get("dernier_jour")

    if dernier_jour_envoye != aujourd_hui and maintenant.hour >= HEURE_HEARTBEAT:
        chemin_log = f"{RACINE}/paris_virtuels.csv"
        lignes = []
        if os.path.exists(chemin_log):
            with open(chemin_log, "r", encoding="utf-8") as f:
                lignes = list(csv.DictReader(f))

        nb_paris_total = len(lignes)
        nb_traites = sum(1 for l in lignes if l.get("resultat", "") != "")
        nb_en_attente = nb_paris_total - nb_traites

        bankroll_v14 = charger_json(f"{RACINE}/bankroll_v14.json", {}).get("bankroll")
        bankroll_v15 = charger_json(f"{RACINE}/bankroll_v15.json", {}).get("bankroll")
        bankroll_v18 = charger_json(f"{RACINE}/bankroll_v18.json", {}).get("bankroll")
        bankroll_v110 = charger_json(f"{RACINE}/bankroll_v110.json", {}).get("bankroll")
        bankroll_place = charger_json(f"{RACINE}/bankroll_place.json", {}).get("bankroll")
        bankroll_2sur4 = charger_json(f"{RACINE}/bankroll_2sur4.json", {}).get("bankroll")

        stats_v14 = calculer_stats_modele(lignes, "v1.4")
        stats_v15 = calculer_stats_modele(lignes, "v1.5")
        stats_v18 = calculer_stats_modele(lignes, "v1.8")
        stats_v110 = calculer_stats_modele(lignes, "v1.10")
        stats_place = calculer_stats_modele(lignes, "place")
        stats_2sur4 = calculer_stats_modele(lignes, "2sur4")

        etat_drivers = charger_json(f"{RACINE}/etat_drivers.json", {})
        etat_hippodromes = charger_json(f"{RACINE}/etat_hippodromes.json", {})

        msg = f"\U0001F4CA <b>Bilan quotidien</b> \u2014 {maintenant.strftime('%d/%m/%Y %H:%M')} UTC\n\n"
        msg += f"Paris logues : {nb_paris_total} ({nb_traites} traites, {nb_en_attente} en attente)\n\n"

        for nom, stats, bankroll in [
            ("v1.4", stats_v14, bankroll_v14), ("v1.5", stats_v15, bankroll_v15),
            ("v1.8", stats_v18, bankroll_v18), ("v1.10", stats_v110, bankroll_v110),
            ("place", stats_place, bankroll_place), ("2sur4", stats_2sur4, bankroll_2sur4),
        ]:
            msg += f"<b>{nom}</b>\n"
            if bankroll is not None:
                variation = bankroll - 1236
                msg += f"Bankroll : {bankroll:.2f}€ ({variation:+.2f}€)\n"
            if stats:
                msg += (
                    f"{stats['nb_paris']} paris traites, "
                    f"{stats['taux_victoire']:.1%} de {'reussite' if nom in ('place','2sur4') else 'victoires'}\n"
                    f"Mise totale : {stats['mise_totale']:.2f}€, "
                    f"gain net : {stats['gain_total']:+.2f}€ "
                    f"(ROI {stats['roi']:+.1%})\n"
                )
            else:
                msg += "Aucun pari traite pour l'instant.\n"
            msg += "\n"

        msg += f"Drivers suivis : {len(etat_drivers)} | Hippodromes suivis : {len(etat_hippodromes)}"

        envoyer_telegram(msg)

        etat_heartbeat["dernier_jour"] = aujourd_hui
        sauvegarder_json(f"{RACINE}/dernier_heartbeat.json", etat_heartbeat)
        print("Heartbeat quotidien avec statistiques envoye.")
    else:
        print("Pas encore l'heure du heartbeat quotidien, ou deja envoye aujourd'hui.")


if __name__ == "__main__":
    main()
