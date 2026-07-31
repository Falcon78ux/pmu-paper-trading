"""
=============================================================================
KEEP_ALIVE.PY - Battement de coeur quotidien AVEC statistiques de performance
=============================================================================
8 modeles : v1.4, v1.5, v1.8, v1.10, place, 2sur4, trio, multi.
=============================================================================
"""

import sys
import os
import csv
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(__file__))
from commun import charger_json, sauvegarder_json, envoyer_telegram

RACINE = os.path.join(os.path.dirname(__file__), "..")

HEURE_HEARTBEAT = 8


def calculer_stats_modele(lignes, nom_modele):
    sous_ensemble = [l for l in lignes if l.get("modele") == nom_modele and l.get("resultat", "") != ""]
    if not sous_ensemble:
        return None

    nb_paris = len(sous_ensemble)
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

        bankrolls = {
            nom: charger_json(f"{RACINE}/bankroll_{nom}.json", {}).get("bankroll")
            for nom in ["v14", "v15", "v18", "v110", "place", "2sur4", "trio", "multi"]
        }

        noms_affichage = {
            "v14": "v1.4", "v15": "v1.5", "v18": "v1.8", "v110": "v1.10",
            "place": "place", "2sur4": "2sur4", "trio": "trio", "multi": "multi",
        }

        etat_drivers = charger_json(f"{RACINE}/etat_drivers.json", {})
        etat_hippodromes = charger_json(f"{RACINE}/etat_hippodromes.json", {})

        msg = f"\U0001F4CA <b>Bilan quotidien</b> \u2014 {maintenant.strftime('%d/%m/%Y %H:%M')} UTC\n\n"
        msg += f"Paris logues : {nb_paris_total} ({nb_traites} traites, {nb_en_attente} en attente)\n\n"

        for cle, nom_affiche in noms_affichage.items():
            stats = calculer_stats_modele(lignes, nom_affiche)
            bankroll = bankrolls[cle]
            msg += f"<b>{nom_affiche}</b>\n"
            if bankroll is not None:
                variation = bankroll - 1236
                msg += f"Bankroll : {bankroll:.2f}€ ({variation:+.2f}€)\n"
            if stats:
                msg += (
                    f"{stats['nb_paris']} paris traites, "
                    f"{stats['taux_victoire']:.1%} de reussite\n"
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
