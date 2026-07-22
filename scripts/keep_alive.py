"""
=============================================================================
KEEP_ALIVE.PY - Battement de coeur quotidien + garantie anti-desactivation
=============================================================================
Deux roles :
1. Envoie un message Telegram UNE FOIS PAR JOUR (pas a chaque execution,
   ce serait trop de spam) pour confirmer que le systeme tourne bien.
2. Met a jour un fichier dernier_run.json a CHAQUE execution (pas juste
   une fois par jour) - ce fichier est ensuite committe dans le workflow
   meme si aucun value bet n'a ete detecte, ce qui garantit une activite
   reguliere sur le depot. GitHub desactive automatiquement les workflows
   planifies apres 60 jours SANS AUCUNE activite sur le depot - ce fichier
   evite que ca arrive, meme lors de longues periodes sans course a se
   mettre sous la dent.
=============================================================================
"""

import sys
import os
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(__file__))
from commun import charger_json, sauvegarder_json, envoyer_telegram

RACINE = os.path.join(os.path.dirname(__file__), "..")

HEURE_HEARTBEAT = 8  # heure UTC a laquelle envoyer le message quotidien


def main():
    maintenant = datetime.now(timezone.utc)
    aujourd_hui = maintenant.strftime("%Y-%m-%d")

    # --- 1. Mise a jour du fichier "preuve de vie", a chaque execution ---
    dernier_run = {
        "derniere_execution_utc": maintenant.isoformat(),
    }
    sauvegarder_json(f"{RACINE}/dernier_run.json", dernier_run)

    # --- 2. Heartbeat Telegram, une fois par jour seulement ---
    etat_heartbeat = charger_json(f"{RACINE}/dernier_heartbeat.json", {})
    dernier_jour_envoye = etat_heartbeat.get("dernier_jour")

    if dernier_jour_envoye != aujourd_hui and maintenant.hour >= HEURE_HEARTBEAT:
        # Petit resume : nombre de paris logues au total, nombre de courses connues
        nb_paris_total = 0
        chemin_log = f"{RACINE}/paris_virtuels.csv"
        if os.path.exists(chemin_log):
            with open(chemin_log, "r", encoding="utf-8") as f:
                nb_paris_total = max(0, sum(1 for _ in f) - 1)  # -1 pour l'en-tete

        etat_drivers = charger_json(f"{RACINE}/etat_drivers.json", {})
        etat_hippodromes = charger_json(f"{RACINE}/etat_hippodromes.json", {})

        msg = (
            f"✅ <b>Systeme actif</b> — {maintenant.strftime('%d/%m/%Y %H:%M')} UTC\n\n"
            f"Paris virtuels logues (total) : {nb_paris_total}\n"
            f"Drivers suivis : {len(etat_drivers)}\n"
            f"Hippodromes suivis : {len(etat_hippodromes)}"
        )
        envoyer_telegram(msg)

        etat_heartbeat["dernier_jour"] = aujourd_hui
        sauvegarder_json(f"{RACINE}/dernier_heartbeat.json", etat_heartbeat)
        print("Heartbeat quotidien envoye.")
    else:
        print("Pas encore l'heure du heartbeat quotidien, ou deja envoye aujourd'hui.")


if __name__ == "__main__":
    main()
