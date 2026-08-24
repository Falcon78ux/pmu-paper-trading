"""
=============================================================================
SUIVI_HEBDOMADAIRE.PY - Instantane automatique backtest+direct, chaque semaine
=============================================================================
S'execute a chaque cycle (comme tous les scripts), mais n'enregistre un
nouvel instantane qu'UNE FOIS PAR SEMAINE (meme principe que le heartbeat
quotidien de keep_alive.py). Ecrit dans suivi_hebdomadaire.csv - permet
de suivre l'evolution dans le temps de l'estimation combinee backtest+
direct, plutot que des instantanes isoles. Sert de journal de reference
pour un eventuel recalibrage periodique des modeles.

CORRIGE (24 aout) : etendu de 9 a 17 strategies (couvre desormais toutes
les strategies actuellement en production, y compris les decouvertes
recentes - dutching, Couple-Harville, Consensus-Place, v1.10-D4, etc.).
Corrige aussi un bug latent : plusieurs modeles sont enregistres sous
leur NOM DE CODE dans paris_virtuels.csv (ex. "v14dutch"), pas leur nom
d'affichage ("v1.4-Dutch") - la fonction cle_log_modele() fait
desormais la correspondance correcte, identique a celle deja utilisee
dans commandes_telegram.py.
=============================================================================
"""

import sys
import os
import csv
import statistics
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(__file__))
from commun import charger_json, sauvegarder_json, envoyer_telegram

RACINE = os.path.join(os.path.dirname(__file__), "..")

MODELES = [
    "v14", "v14dutch", "v14favori", "v14sire",
    "v15", "v18",
    "v110", "v110dutch", "v110favori", "v110d4",
    "consensus_place", "couple_harville",
    "place", "2sur4", "trio", "multi", "2favori",
]
NOMS_AFFICHAGE = {
    "v14": "v1.4", "v14dutch": "v1.4-Dutch", "v14favori": "v1.4-Favori",
    "v14sire": "v1.4+Genealogie",
    "v15": "v1.5", "v18": "v1.8",
    "v110": "v1.10", "v110dutch": "v1.10-Dutch", "v110favori": "v1.10-Favori",
    "v110d4": "v1.10-D4",
    "consensus_place": "Consensus-Place", "couple_harville": "Couple-Harville",
    "place": "place", "2sur4": "2sur4", "trio": "trio", "multi": "multi",
    "2favori": "2favori",
}

REFERENCE_BACKTEST = {
    "v14": {"n": 30984, "roi": 0.1075},
    "v14dutch": {"n": 7386, "roi": 0.30},
    "v14favori": {"n": 8654, "roi": 0.2789},
    "v14sire": {"n": 30042, "roi": 0.1176},
    "v15": {"n": 31756, "roi": 0.1391},
    "v18": {"n": 34248, "roi": 0.1556},
    "v110": {"n": 34379, "roi": 0.1879},
    "v110dutch": {"n": 14873, "roi": 0.2319},
    "v110favori": {"n": 7590, "roi": 0.3584},
    "v110d4": {"n": 3718, "roi": 0.5183},
    "consensus_place": {"n": 7811, "roi": 0.3738},
    "couple_harville": {"n": 15980, "roi": 0.6757},
    "place": {"n": 16635, "roi": 0.233},
    "2sur4": {"n": 10312, "roi": 0.838},
    "trio": {"n": 14993, "roi": 1.358},
    "multi": {"n": 10206, "roi": 4.60},
    "2favori": {"n": 2645, "roi": 0.2501},
}

CHAMPS_CSV = [
    "date_snapshot", "modele", "n_backtest", "roi_backtest",
    "n_direct", "roi_direct", "roi_combine", "ic_bas", "ic_haut",
    "poids_direct",
]


def cle_log_modele(cle):
    """v14dutch, v14favori, v14sire, v110dutch, v110favori, v110d4,
    consensus_place et couple_harville sont stockes sous leur nom de
    code dans paris_virtuels.csv (pas leur nom d'affichage) - meme
    logique que commandes_telegram.py, indispensable pour retrouver
    correctement leurs paris."""
    if cle in ("v14dutch", "v14favori", "v14sire", "v110dutch", "v110favori", "v110d4", "consensus_place", "couple_harville"):
        return cle
    return NOMS_AFFICHAGE[cle]


def calculer_stats_modele(lignes_csv, nom_log):
    sous = [l for l in lignes_csv if l.get("modele") == nom_log and l.get("resultat", "") != ""]
    if not sous:
        return None
    nb = len(sous)
    mise_totale = sum(float(l.get("mise", 0) or 0) for l in sous)
    gain_total = sum(float(l.get("gain_euros", 0) or 0) for l in sous)
    roi = gain_total / mise_totale if mise_totale > 0 else 0
    return {"nb": nb, "roi": roi}


def enregistrer_snapshot():
    chemin_log = f"{RACINE}/paris_virtuels.csv"
    lignes_csv = []
    if os.path.exists(chemin_log):
        with open(chemin_log, "r", encoding="utf-8") as f:
            lignes_csv = list(csv.DictReader(f))

    aujourd_hui = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    chemin_snapshot = f"{RACINE}/suivi_hebdomadaire.csv"
    existe = os.path.exists(chemin_snapshot)

    lignes_a_ecrire = []
    for cle in MODELES:
        nom = NOMS_AFFICHAGE[cle]
        nom_log = cle_log_modele(cle)
        ref = REFERENCE_BACKTEST[cle]
        n_bt, roi_bt = ref["n"], ref["roi"]
        stats_direct = calculer_stats_modele(lignes_csv, nom_log)

        if stats_direct:
            n_direct, roi_direct = stats_direct["nb"], stats_direct["roi"]
            roi_combine = (n_bt * roi_bt + n_direct * roi_direct) / (n_bt + n_direct)
            poids_direct = n_direct / (n_bt + n_direct)

            sous = [l for l in lignes_csv if l.get("modele") == nom_log and l.get("resultat", "") != ""]
            returns = [
                float(l.get("gain_euros", 0) or 0) / float(l.get("mise", 0) or 1)
                for l in sous if float(l.get("mise", 0) or 0) > 0
            ]
            ic_bas, ic_haut = "", ""
            if len(returns) > 1:
                ecart_type = statistics.stdev(returns)
                erreur_type = ecart_type / ((n_bt + n_direct) ** 0.5)
                ic_bas = round(roi_combine - 1.96 * erreur_type, 4)
                ic_haut = round(roi_combine + 1.96 * erreur_type, 4)

            lignes_a_ecrire.append({
                "date_snapshot": aujourd_hui, "modele": nom,
                "n_backtest": n_bt, "roi_backtest": round(roi_bt, 4),
                "n_direct": n_direct, "roi_direct": round(roi_direct, 4),
                "roi_combine": round(roi_combine, 4),
                "ic_bas": ic_bas, "ic_haut": ic_haut,
                "poids_direct": round(poids_direct, 4),
            })
        else:
            lignes_a_ecrire.append({
                "date_snapshot": aujourd_hui, "modele": nom,
                "n_backtest": n_bt, "roi_backtest": round(roi_bt, 4),
                "n_direct": 0, "roi_direct": "", "roi_combine": round(roi_bt, 4),
                "ic_bas": "", "ic_haut": "", "poids_direct": 0,
            })

    with open(chemin_snapshot, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CHAMPS_CSV)
        if not existe:
            writer.writeheader()
        for ligne in lignes_a_ecrire:
            writer.writerow(ligne)

    return len(lignes_a_ecrire)


def main():
    etat = charger_json(f"{RACINE}/dernier_suivi_hebdo.json", {"derniere_semaine": None})
    maintenant = datetime.now(timezone.utc)
    annee_semaine = f"{maintenant.isocalendar().year}-S{maintenant.isocalendar().week}"

    if etat.get("derniere_semaine") == annee_semaine:
        print(f"Instantane hebdomadaire deja enregistre pour {annee_semaine}.")
        return

    if maintenant.weekday() != 0:  # 0 = lundi
        print(f"Pas encore lundi (jour actuel : {maintenant.strftime('%A')}), on attend.")
        return

    nb_lignes = enregistrer_snapshot()
    etat["derniere_semaine"] = annee_semaine
    sauvegarder_json(f"{RACINE}/dernier_suivi_hebdo.json", etat)

    envoyer_telegram(
        f"📈 <b>Instantane hebdomadaire enregistre</b> ({annee_semaine})\n\n"
        f"{nb_lignes} modeles consignes dans suivi_hebdomadaire.csv"
    )
    print(f"Instantane hebdomadaire enregistre pour {annee_semaine}, {nb_lignes} lignes.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        detail = traceback.format_exc()[-500:]
        detail_echappe = detail.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        envoyer_telegram(f"🔴 <b>Erreur dans suivi_hebdomadaire.py</b>\n\n{e}\n\n<code>{detail_echappe}</code>")
        raise
