"""
=============================================================================
COLLECTE PROPRE DE COTE PRE-COURSE - script a greffer sur le cron
=============================================================================
Racine du probleme decouvert ce soir : aucune source fiable de cote
PRE-COURSE a grande echelle n'existe dans le projet.
  - entries_v2.csv (collecteur historique v2) : API OFFLINE de PMU,
    "dernierRapportDirect" archive - proche de la CLOTURE, pas de la
    detection. Coefficient log_cote gonfle d'environ 40% quand entraine
    dessus (confirme ce soir, n=4397).
  - paris_virtuels.csv : vraie cote de detection, mais BIAISE PAR
    SELECTION (seulement les paris que nos propres modeles ont deja
    flagues EV>10%) et TROP PETIT (~21k lignes) pour un reentrainement
    complet.

Ce script logue la cote de TOUS les chevaux de TOUTES les courses trot
au moment ou le cron tourne (meme cadence que verifier_a_venir.py,
15-40 min avant chaque depart) - PAS SEULEMENT les chevaux ou une mise
serait placee. Objectif : accumuler, jour apres jour, un historique
NON BIAISE et suffisamment large pour un futur reentrainement propre
de log_cote (et donc, en aval, du terme quadratique et de l'EWA).

A ajouter comme etape supplementaire dans automatisation.yml (meme
cron que verifier_a_venir.py), en parallele du pipeline existant - ne
modifie RIEN au fonctionnement actuel, ajoute seulement une nouvelle
collecte independante.

Format de sortie : cote_pre_course_propre.csv (race_id, cheval,
cote_a_ce_moment, minutes_avant_depart_estimees, date_collecte)
=============================================================================
"""

import os
import csv
import requests
from datetime import datetime, timezone

RACINE = os.path.join(os.path.dirname(__file__), "..")
CHEMIN_SORTIE = f"{RACINE}/cote_pre_course_propre.csv"
CHAMPS = ["race_id", "date", "num_reunion", "num_course", "cheval", "cote",
          "minutes_avant_depart_estime", "date_collecte_utc"]

HEADERS = {"User-Agent": "Mozilla/5.0 (research script - personal use)"}
URL_BASE = "https://online.turfinfo.api.pmu.fr/rest/client/61/programme"


def _get(url):
    try:
        r = requests.get(url, headers=HEADERS, timeout=20)
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        print(f"[WARN] {url} : {e}")
    return None


def collecter_cote_propre():
    aujourd_hui = datetime.now(timezone.utc).strftime("%d%m%Y")
    programme = _get(f"{URL_BASE}/{aujourd_hui}")
    if not programme:
        print("Aucun programme recupere - rien a collecter ce cycle.")
        return

    fichier_existe = os.path.exists(CHEMIN_SORTIE)
    f = open(CHEMIN_SORTIE, "a", newline="", encoding="utf-8")
    writer = csv.DictWriter(f, fieldnames=CHAMPS)
    if not fichier_existe:
        writer.writeheader()

    maintenant = datetime.now(timezone.utc)
    n_lignes = 0

    for reunion in programme.get("programme", {}).get("reunions", []):
        num_reunion = reunion.get("numOfficiel") or reunion.get("numExterne")

        for course in reunion.get("courses", []):
            if course.get("discipline") not in ("ATTELE", "MONTE"):
                continue  # uniquement le trot - meme perimetre que le reste du projet

            num_course = course.get("numOrdre") or course.get("numExterne")
            heure_depart_ts = course.get("heureDepart")
            minutes_avant = None
            if heure_depart_ts:
                try:
                    heure_depart = datetime.fromtimestamp(heure_depart_ts / 1000, tz=timezone.utc)
                    minutes_avant = round((heure_depart - maintenant).total_seconds() / 60, 1)
                except Exception:
                    pass

            # ne collecte que les courses PAS ENCORE PARTIES (cote encore
            # pertinente comme "pre-course"), avec un depart estime dans
            # les 60 prochaines minutes - evite de logger des courses trop
            # lointaines (cote encore instable/non representative) ou deja
            # parties (cote figee post-course, pas d'interet ici)
            if minutes_avant is None or minutes_avant < 0 or minutes_avant > 60:
                continue

            url_part = f"{URL_BASE}/{aujourd_hui}/R{num_reunion}/C{num_course}/participants"
            part_data = _get(url_part)
            if not part_data:
                continue

            race_id = f"{aujourd_hui}_{num_reunion}_{num_course}"
            for p in part_data.get("participants", []):
                rapport_direct = p.get("dernierRapportDirect", {}) or {}
                cote = rapport_direct.get("rapport")
                nom_cheval = p.get("nom")
                if cote is None or not nom_cheval:
                    continue

                writer.writerow({
                    "race_id": race_id,
                    "date": aujourd_hui,
                    "num_reunion": num_reunion,
                    "num_course": num_course,
                    "cheval": nom_cheval,
                    "cote": cote,
                    "minutes_avant_depart_estime": minutes_avant,
                    "date_collecte_utc": maintenant.isoformat(),
                })
                n_lignes += 1

    f.close()
    print(f"[INFO] {n_lignes} lignes de cote pre-course propre collectees ce cycle.")


if __name__ == "__main__":
    collecter_cote_propre()
