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
from commun import charger_json, sauvegarder_json, envoyer_telegram, get_bankroll

RACINE = os.path.join(os.path.dirname(__file__), "..")

MODELES = [
    "v14", "v14dutch", "v14favori", "v14sire",
    "v15", "v18",
    "v110", "v110dutch", "v110favori", "v110d4", "v110sniper", "v110place", "v110antifav", "v110snipercombine", "v110ecartfaible",
    "consensus_place", "couple_harville",
    "place", "2sur4", "trio", "multi", "2favori",
]
NOMS_AFFICHAGE = {
    "v14": "v1.4", "v14dutch": "v1.4-Dutch", "v14favori": "v1.4-Favori",
    "v14sire": "v1.4+Genealogie",
    "v15": "v1.5", "v18": "v1.8",
    "v110": "v1.10", "v110dutch": "v1.10-Dutch", "v110favori": "v1.10-Favori",
    "v110d4": "v1.10-D4", "v110sniper": "v1.10-Sniper",
    "v110place": "v1.10-Place", "v110antifav": "v1.10-AntiFav",
    "v110snipercombine": "v1.10-SniperCombine", "v110ecartfaible": "v1.10-EcartFaible",
    "consensus_place": "Consensus-Place", "couple_harville": "Couple-Harville",
    "place": "place", "2sur4": "2sur4", "trio": "trio", "multi": "multi",
    "2favori": "2favori",
}

REFERENCE_BACKTEST = {
    "v14": {"n": 30984, "roi": 0.1075},
    "v14dutch": {"n": 7178, "roi": 0.1124},  # CORRIGE (25 aout), coherent avec commandes_telegram.py
    "v14favori": {"n": 8654, "roi": 0.2789},
    "v14sire": {"n": 30042, "roi": 0.1176},
    "v15": {"n": 31756, "roi": 0.1391},
    "v18": {"n": 34248, "roi": 0.1556},
    "v110": {"n": 34379, "roi": 0.1879},
    "v110dutch": {"n": 14873, "roi": 0.2319},
    "v110favori": {"n": 7590, "roi": 0.3584},
    "v110d4": {"n": 3718, "roi": 0.5183},
    "v110sniper": {"n": 966, "roi": 0.2027},
    "v110place": {"n": 34379, "roi": 0.1913},
    "v110antifav": {"n": 26824, "roi": 0.2655},
    "v110snipercombine": {"n": 273, "roi": 0.3238},
    "v110ecartfaible": {"n": 1882, "roi": 0.3258},
    "consensus_place": {"n": 7811, "roi": 0.3738},
    "couple_harville": {"n": 16159, "roi": 0.7088},  # CORRIGE (26 aout), coherent avec commandes_telegram.py
    "place": {"n": 16635, "roi": 0.2339},  # CORRIGE (25 aout), coherent avec commandes_telegram.py
    "2sur4": {"n": 10312, "roi": 0.838},
    "trio": {"n": 14993, "roi": 1.6530},  # CORRIGE (26 aout), coherent avec commandes_telegram.py
    "multi": {"n": 6366, "roi": 1.9111},  # CORRIGE (25 aout), coherent avec commandes_telegram.py
    "2favori": {"n": 3171, "roi": 0.3514},  # CORRIGE (25 aout), coherent avec commandes_telegram.py
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
    if cle in ("v14dutch", "v14favori", "v14sire", "v110dutch", "v110favori", "v110d4", "v110sniper", "v110place", "v110antifav", "v110snipercombine", "v110ecartfaible", "consensus_place", "couple_harville"):
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


BANKROLL_DEPART_STANDARD = 1236


def enregistrer_snapshot_portefeuille():
    """NOUVEAU (28 aout) : accumule un historique du TOTAL combine des
    21 bankrolls, chaque semaine - absent jusqu'ici (seul le niveau
    actuel etait consultable, jamais son evolution dans le temps).
    Permettra, une fois assez de semaines accumulees, de calculer un
    vrai drawdown du portefeuille combine (comme mesure en backtest
    lors de la recherche meta-allocation d'aout 2026).

    ETENDU (31 aout) : enregistre aussi le niveau de CHAQUE bankroll
    individuelle chaque semaine (suivi_bankrolls_hebdo.csv) - absent
    jusqu'ici, necessaire pour calculer une vraie "performance recente"
    (croissance sur les 16 dernieres semaines) par strategie, validee
    comme meilleure methode d'allocation lors du chantier meta-
    allocation (bootstrap double : portefeuille et par strategie,
    100% de victoire en croissance sur 200 historiques synthetiques).
    Prendra ~16 semaines avant d'avoir assez de recul pour un premier
    calcul reel - demarre la collecte des maintenant."""
    total_actuel = 0.0
    niveaux_par_strategie = {}
    for cle in MODELES:
        bankroll_actuelle, _ = get_bankroll(RACINE, cle)
        total_actuel += bankroll_actuelle
        niveaux_par_strategie[cle] = bankroll_actuelle

    chemin = f"{RACINE}/suivi_portefeuille.csv"
    existe = os.path.exists(chemin)
    aujourd_hui = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with open(chemin, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["date_snapshot", "total_bankroll", "nb_strategies"])
        if not existe:
            writer.writeheader()
        writer.writerow({"date_snapshot": aujourd_hui, "total_bankroll": round(total_actuel, 2), "nb_strategies": len(MODELES)})

    chemin_bankrolls = f"{RACINE}/suivi_bankrolls_hebdo.csv"
    existe_bankrolls = os.path.exists(chemin_bankrolls)
    champs_bankrolls = ["date_snapshot"] + MODELES
    with open(chemin_bankrolls, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=champs_bankrolls)
        if not existe_bankrolls:
            writer.writeheader()
        ligne = {"date_snapshot": aujourd_hui}
        ligne.update({cle: round(niveaux_par_strategie[cle], 2) for cle in MODELES})
        writer.writerow(ligne)

    return total_actuel


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
    total_portefeuille = enregistrer_snapshot_portefeuille()
    etat["derniere_semaine"] = annee_semaine
    sauvegarder_json(f"{RACINE}/dernier_suivi_hebdo.json", etat)

    envoyer_telegram(
        f"📈 <b>Instantane hebdomadaire enregistre</b> ({annee_semaine})\n\n"
        f"{nb_lignes} modeles consignes dans suivi_hebdomadaire.csv\n"
        f"Portefeuille combine : {total_portefeuille:,.0f}EUR"
    )
    print(f"Instantane hebdomadaire enregistre pour {annee_semaine}, {nb_lignes} lignes. Portefeuille : {total_portefeuille:,.0f}EUR")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        detail = traceback.format_exc()[-500:]
        detail_echappe = detail.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        envoyer_telegram(f"🔴 <b>Erreur dans suivi_hebdomadaire.py</b>\n\n{e}\n\n<code>{detail_echappe}</code>")
        raise
