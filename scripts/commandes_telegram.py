"""
=============================================================================
COMMANDES_TELEGRAM.PY - Interroge et pilote le systeme via Telegram
=============================================================================
16 modeles : v14, v14dutch, v14favori, v14sire, v15, v18, v110,
v110dutch, v110favori, consensus_place, couple_harville, place, 2sur4,
trio, multi, 2favori.
NOUVEAU (21 aout, soir) : /bilan affiche desormais la mise totale
engagee par strategie, et le cumul global des mises dans la ligne
Total.
=============================================================================
"""

import sys
import os
import csv
import statistics
from datetime import datetime, timezone

import requests

sys.path.insert(0, os.path.dirname(__file__))
from commun import charger_json, sauvegarder_json, envoyer_telegram

RACINE = os.path.join(os.path.dirname(__file__), "..")

MODELES = [
    "v14", "v14dutch", "v14favori", "v14sire",
    "v15", "v18",
    "v110", "v110dutch", "v110favori", "v110d4", "v110sniper", "v110place", "v110antifav", "v110snipercombine",
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
    "v110snipercombine": "v1.10-SniperCombine",
    "consensus_place": "Consensus-Place", "couple_harville": "Couple-Harville",
    "place": "place", "2sur4": "2sur4", "trio": "trio", "multi": "multi",
    "2favori": "2favori",
}

MODELES_AVEC_CLV = [
    "v14", "v14dutch", "v14favori", "v14sire",
    "v15", "v18",
    "v110", "v110dutch", "v110favori", "v110d4", "v110sniper", "v110antifav", "v110snipercombine",
    "consensus_place",
    "2favori",
]

REFERENCE_BACKTEST = {
    "v14": {"n": 30984, "roi": 0.1075},
    "v14dutch": {"n": 7178, "roi": 0.1124},  # CORRIGE (25 aout) : ancienne valeur (0.30) etait une approximation grossiere, jamais un ROI/pari precis. Recalculee avec un calcul par pari exact.
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
    "consensus_place": {"n": 7811, "roi": 0.3738},
    "couple_harville": {"n": 16159, "roi": 0.7088},  # CORRIGE (26 aout) : ancienne valeur (0.6757) sous-estimait - ne verifiait qu'UNE combinaison gagnante, ratant les cas d'egalite/dead-heat (~25% des courses ont plusieurs combinaisons gagnantes simultanees)
    "place": {"n": 16635, "roi": 0.2339},  # CORRIGE (25 aout) : reconfirme avec vrais rapports (place_historique.csv), quasi identique a l'ancienne valeur
    "2sur4": {"n": 10312, "roi": 0.838},
    "trio": {"n": 14993, "roi": 1.6530},  # CORRIGE (26 aout) : ancienne valeur (1.3745) sous-estimait trio - ne comptait pas les gains legitimes sur les Trio "degrades" (rapport a 2 chevaux au lieu de 3, ~13.7% des courses selon un echantillon de 300)
    "multi": {"n": 6366, "roi": 1.9111},  # CORRIGE (25 aout) : ancienne valeur (n=10206, roi=4.60) provenait d'une estimation theorique, jamais confirmee par de vrais rapports. Recalculee a partir de la collecte complete de multi_historique.csv (vrais rapports MULTI/MINI_MULTI, 28030 courses interrogees).
    "2favori": {"n": 3171, "roi": 0.3514},  # CORRIGE (25 aout) : reconstruction complete du modele (jamais retestee depuis le deploiement initial) donne un resultat different de l'ancienne reference (n=2645, roi=0.2501)
}

SEUIL_MIN_PARIS_STATUT = 50
SEUIL_MIN_PARIS_SORTIE_BRUIT = 300
SEUIL_LARGEUR_IC_SORTIE_BRUIT = 0.40  # CORRIGE (23 aout) : 15% etait base sur une approximation binomiale erronee (supposait un ecart-type ~0.4-0.5 comme un gagne/perdu simple). La vraie variance des RETOURS financiers (qui integrent l'ampleur des gains selon la cote) mesuree sur v1.10/v1.10-Favori/Couple-Harville est bien plus elevee (ecart-type 2 a 6 selon la strategie) - un seuil de 15% aurait necessite jusqu'a 22911 paris (plus de 3 ans a notre rythme) pour Couple-Harville. 40% reste atteignable en quelques semaines a quelques mois selon la strategie, tout en representant un vrai resserrement par rapport au chaos initial.

TEXTE_AIDE = (
    "<b>Commandes disponibles</b>\n\n"
    "/statut — indicateur combine par strategie (IC95% + CLV, emoji vert/rouge/neutre)\n"
    "/progression — suivi de la sortie du bruit statistique (direct seul, sans backtest) et temps restant estime\n"
    "/calibration — verifie si les probabilites predites correspondent au taux de victoire reel (v1.4/v1.5/v1.8/v1.10)\n"
    "/portefeuille — vue combinee des 21 bankrolls reelles (equivalent portefeuille a poids egaux)\n"
    "/bankroll — bankrolls actuelles des 16 strategies\n"
    "/bilan — bilan du jour (gain, perte, ROI, nb paris, mises)\n"
    "/bilan JJ/MM/AAAA — bilan d'une date precise\n"
    "/bilan cumule — bilan depuis le debut\n"
    "/confiance — estimation combinee backtest+direct, par modele (detail complet)\n"
    "/clv — Closing Line Value moyen par modele (detail complet)\n"
    "/pause [strategie|tout] — coupe les notifications (le pari continue en arriere-plan)\n"
    "/reprendre [strategie|tout] — reactive les notifications\n"
    "/courses_restantes — courses de trot pas encore parties aujourd'hui\n"
    "/courses_non_jouees — courses passees sans aucun pari aujourd'hui\n"
    "/aide — cette liste"
)


def recuperer_nouveaux_messages():
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        return []
    etat = charger_json(f"{RACINE}/dernier_update_telegram.json", {"dernier_id": 0})
    offset = etat["dernier_id"] + 1
    url = f"https://api.telegram.org/bot{token}/getUpdates"
    try:
        r = requests.get(url, params={"offset": offset}, timeout=15)
        data = r.json()
    except Exception:
        return []
    if not data.get("ok"):
        return []

    updates = data.get("result", [])
    messages = []
    dernier_id = etat["dernier_id"]
    for u in updates:
        dernier_id = max(dernier_id, u.get("update_id", 0))
        texte = u.get("message", {}).get("text", "")
        if texte:
            messages.append(texte.strip())

    sauvegarder_json(f"{RACINE}/dernier_update_telegram.json", {"dernier_id": dernier_id})
    return messages


def traiter_bankroll():
    lignes = ["💰 <b>Bankrolls actuelles</b>\n"]
    for cle in MODELES:
        bankroll = charger_json(f"{RACINE}/bankroll_{cle}.json", {}).get("bankroll")
        nom = NOMS_AFFICHAGE[cle]
        if bankroll is not None:
            variation = bankroll - 1236
            lignes.append(f"{nom} : {bankroll:.2f}€ ({variation:+.2f}€)")
        else:
            lignes.append(f"{nom} : pas encore initialisee")
    return "\n".join(lignes)


def calculer_stats_modele(lignes_csv, nom_modele):
    sous = [l for l in lignes_csv if l.get("modele") == nom_modele and l.get("resultat", "") != ""]
    if not sous:
        return None
    nb = len(sous)
    gagnants = sum(1 for l in sous if l.get("resultat") in ("GAGNANT", "PLACE"))
    mise_totale = sum(float(l.get("mise", 0) or 0) for l in sous)
    gain_total = sum(float(l.get("gain_euros", 0) or 0) for l in sous)
    roi = gain_total / mise_totale if mise_totale > 0 else 0
    return {"nb": nb, "taux": gagnants / nb if nb else 0, "mise": mise_totale, "gain": gain_total, "roi": roi}


def normaliser_date(argument):
    chiffres = "".join(c for c in argument if c.isdigit())
    if len(chiffres) == 8:
        return chiffres
    return None


def cle_log_modele(cle):
    """v14dutch, v14favori, v14sire, v110dutch, v110favori,
    consensus_place et couple_harville sont stockes tels quels dans
    les logs (pas les noms d'affichage), les autres utilisent le nom
    d'affichage."""
    if cle in ("v14dutch", "v14favori", "v14sire", "v110dutch", "v110favori", "v110d4", "v110sniper", "v110place", "v110antifav", "v110snipercombine", "consensus_place", "couple_harville"):
        return cle
    return NOMS_AFFICHAGE[cle]


def traiter_bilan(argument):
    chemin_log = f"{RACINE}/paris_virtuels.csv"
    if not os.path.exists(chemin_log):
        return "Aucun pari enregistre pour l'instant."
    with open(chemin_log, "r", encoding="utf-8") as f:
        lignes_csv = list(csv.DictReader(f))

    aujourd_hui = datetime.now(timezone.utc).strftime("%d%m%Y")
    date_normalisee = normaliser_date(argument) if argument and argument != "cumule" else None

    if argument == "cumule":
        lignes_filtrees = lignes_csv
        titre = "Bilan cumule (depuis le debut)"
    elif date_normalisee:
        lignes_filtrees = [l for l in lignes_csv if l.get("race_id", "").startswith(date_normalisee)]
        titre = f"Bilan du {date_normalisee[:2]}/{date_normalisee[2:4]}/{date_normalisee[4:]}"
    else:
        lignes_filtrees = [l for l in lignes_csv if l.get("race_id", "").startswith(aujourd_hui)]
        titre = "Bilan du jour"

    msg = f"📊 <b>{titre}</b>\n\n"
    gain_total_global = 0.0
    perte_total_global = 0.0
    mise_totale_globale = 0.0
    nb_total_global = 0
    for cle in MODELES:
        nom = NOMS_AFFICHAGE[cle]
        stats = calculer_stats_modele(lignes_filtrees, cle_log_modele(cle))
        msg += f"<b>{nom}</b>\n"
        if stats:
            msg += f"{stats['nb']} paris, {stats['taux']:.1%} reussite\n"
            msg += f"Mise totale : {stats['mise']:.2f}€\n"
            msg += f"Gain net : {stats['gain']:+.2f}€ (ROI {stats['roi']:+.1%})\n\n"
            nb_total_global += stats["nb"]
            mise_totale_globale += stats["mise"]
            if stats["gain"] >= 0:
                gain_total_global += stats["gain"]
            else:
                perte_total_global += stats["gain"]
        else:
            msg += "Aucun pari.\n\n"

    msg += (
        f"<b>Total</b> : {nb_total_global} paris | "
        f"mises engagees {mise_totale_globale:.2f}€ | "
        f"gains {gain_total_global:+.2f}€ | pertes {perte_total_global:+.2f}€"
    )
    return msg


def traiter_confiance():
    chemin_log = f"{RACINE}/paris_virtuels.csv"
    lignes_csv = []
    if os.path.exists(chemin_log):
        with open(chemin_log, "r", encoding="utf-8") as f:
            lignes_csv = list(csv.DictReader(f))

    msg = "🔬 <b>Estimation combinee (backtest + direct)</b>\n\n"
    for cle in MODELES:
        nom = NOMS_AFFICHAGE[cle]
        ref = REFERENCE_BACKTEST[cle]
        stats_direct = calculer_stats_modele(lignes_csv, cle_log_modele(cle))

        n_bt, roi_bt = ref["n"], ref["roi"]
        if stats_direct:
            n_direct, roi_direct = stats_direct["nb"], stats_direct["roi"]
            roi_combine = (n_bt * roi_bt + n_direct * roi_direct) / (n_bt + n_direct)
            poids_direct = n_direct / (n_bt + n_direct)

            sous = [l for l in lignes_csv if l.get("modele") == cle_log_modele(cle) and l.get("resultat", "") != ""]
            returns = []
            for l in sous:
                mise = float(l.get("mise", 0) or 0)
                gain = float(l.get("gain_euros", 0) or 0)
                if mise > 0:
                    returns.append(gain / mise)

            ic_texte = ""
            if len(returns) > 1:
                ecart_type = statistics.stdev(returns)
                erreur_type = ecart_type / ((n_bt + n_direct) ** 0.5)
                ic_bas = roi_combine - 1.96 * erreur_type
                ic_haut = roi_combine + 1.96 * erreur_type
                ic_texte = f" IC95%≈[{ic_bas:+.1%},{ic_haut:+.1%}]"

            msg += (
                f"<b>{nom}</b>\n"
                f"Backtest : {roi_bt:+.1%} (n={n_bt})\n"
                f"Direct : {roi_direct:+.1%} (n={n_direct})\n"
                f"<b>Combine : {roi_combine:+.1%}</b>{ic_texte}\n"
                f"(poids du direct : {poids_direct:.1%})\n\n"
            )
        else:
            msg += f"<b>{nom}</b>\nBacktest : {roi_bt:+.1%} (n={n_bt})\nAucun pari en direct pour l'instant.\n\n"

    return msg


def traiter_statut():
    chemin_log = f"{RACINE}/paris_virtuels.csv"
    lignes_csv = []
    if os.path.exists(chemin_log):
        with open(chemin_log, "r", encoding="utf-8") as f:
            lignes_csv = list(csv.DictReader(f))

    msg = "🚦 <b>Statut par strategie</b> (IC95% + CLV)\n\n"
    msg += "<i>🟢 backtest confirme | 🔴 backtest hors IC95% | ⚪ echantillon insuffisant (&lt;50 paris)</i>\n\n"

    for cle in MODELES:
        nom = NOMS_AFFICHAGE[cle]
        ref = REFERENCE_BACKTEST[cle]
        nom_log = cle_log_modele(cle)
        stats_direct = calculer_stats_modele(lignes_csv, nom_log)

        n_bt, roi_bt = ref["n"], ref["roi"]
        n_direct = stats_direct["nb"] if stats_direct else 0

        if n_direct < SEUIL_MIN_PARIS_STATUT:
            msg += f"⚪ <b>{nom}</b> (n={n_direct})\nEchantillon trop petit pour conclure (seuil : {SEUIL_MIN_PARIS_STATUT})\n\n"
            continue

        roi_direct = stats_direct["roi"]
        roi_combine = (n_bt * roi_bt + n_direct * roi_direct) / (n_bt + n_direct)

        sous = [l for l in lignes_csv if l.get("modele") == nom_log and l.get("resultat", "") != ""]
        returns = []
        for l in sous:
            mise = float(l.get("mise", 0) or 0)
            gain = float(l.get("gain_euros", 0) or 0)
            if mise > 0:
                returns.append(gain / mise)

        dans_ic = None
        ic_texte = ""
        if len(returns) > 1:
            ecart_type = statistics.stdev(returns)
            erreur_type = ecart_type / ((n_bt + n_direct) ** 0.5)
            ic_bas = roi_combine - 1.96 * erreur_type
            ic_haut = roi_combine + 1.96 * erreur_type
            dans_ic = ic_bas <= roi_bt <= ic_haut
            ic_texte = f"[{ic_bas:+.1%},{ic_haut:+.1%}]"

        clv_texte = "non disponible"
        if cle in MODELES_AVEC_CLV:
            sous_clv = [
                l for l in lignes_csv
                if l.get("modele") == nom_log and l.get("resultat", "") != ""
                and l.get("cote_cloture", "") not in ("", None)
            ]
            clv_valeurs = []
            for l in sous_clv:
                try:
                    cd, cc = float(l["cote"]), float(l["cote_cloture"])
                    if cc > 0:
                        clv_valeurs.append((cd / cc) - 1)
                except (ValueError, ZeroDivisionError):
                    continue
            if clv_valeurs:
                clv_moyen = sum(clv_valeurs) / len(clv_valeurs)
                clv_texte = f"{clv_moyen:+.1%} (n={len(clv_valeurs)})"

        emoji = "🟢" if (dans_ic is None or dans_ic) else "🔴"
        statut_ic = "backtest dans l'IC95%" if dans_ic else ("backtest HORS IC95%" if dans_ic is False else "IC non calculable")

        msg += (
            f"{emoji} <b>{nom}</b> (n={n_direct})\n"
            f"{statut_ic} : {roi_bt:+.1%} {ic_texte}\n"
            f"CLV : {clv_texte}\n\n"
        )

    return msg


BANKROLL_DEPART_STANDARD = 1236  # meme valeur de depart que toutes les bankrolls individuelles


def traiter_portefeuille():
    """NOUVEAU (28 aout) : vue combinee des 21 bankrolls reelles - suite
    a la recherche meta-allocation qui a confirme qu'un portefeuille
    combine (poids egaux, ce que le systeme fait deja nativement en
    faisant tourner chaque strategie independamment) a un drawdown
    bien inferieur a la moyenne des strategies individuelles
    (2.7% vs 3-7% en backtest hors-echantillon sur 21 strategies).
    Cette commande donne enfin une vue reelle de ce chiffre combine,
    absente jusqu'ici (chaque strategie n'etait visible qu'isolement
    via /bilan)."""
    total_actuel = 0.0
    total_depart = 0.0
    details = []
    for cle in MODELES:
        bankroll_actuelle, _ = get_bankroll(RACINE, cle)
        total_actuel += bankroll_actuelle
        total_depart += BANKROLL_DEPART_STANDARD
        details.append((NOMS_AFFICHAGE[cle], bankroll_actuelle))

    roi_combine = (total_actuel - total_depart) / total_depart if total_depart > 0 else 0

    msg = "💼 <b>Portefeuille combine (21 strategies)</b>\n\n"
    msg += (
        "<i>Vue combinee des 21 bankrolls reelles - chaque strategie tourne "
        "deja independamment (equivalent a une allocation a poids egaux, "
        "validee comme l'approche la plus robuste lors de la recherche "
        "meta-allocation d'aout 2026, y compris teste rigoureusement "
        "hors-echantillon). Pas encore de suivi du vrai drawdown dans le "
        "temps - ce chiffre s'accumulera progressivement via le suivi "
        "hebdomadaire.</i>\n\n"
    )
    msg += f"<b>Total combine : {total_actuel:,.0f}EUR</b> (depart : {total_depart:,.0f}EUR, {roi_combine:+.1%})\n\n"

    details_tries = sorted(details, key=lambda x: x[1], reverse=True)
    msg += "Detail par strategie (triees par bankroll actuelle) :\n"
    for nom, bankroll in details_tries:
        msg += f"  {nom} : {bankroll:,.0f}EUR\n"

    return msg



    chemin_log = f"{RACINE}/paris_virtuels.csv"
    if not os.path.exists(chemin_log):
        return "Aucun pari enregistre pour l'instant."
    with open(chemin_log, "r", encoding="utf-8") as f:
        lignes_csv = list(csv.DictReader(f))

    MODELES_CALIBRABLES = ["v14", "v15", "v18", "v110", "v110sniper", "v110antifav", "v110snipercombine"]
    BINS = [(0.10, 0.15), (0.15, 0.20), (0.20, 0.25), (0.25, 0.30), (0.30, 0.40), (0.40, 1.01)]
    SEUIL_HAUTE_CONFIANCE = 0.30

    msg = "📐 <b>Calibration des probabilites predites</b>\n\n"
    msg += (
        "<i>Verifie si, quand le modele annonce X% de chances de gagner, "
        "le taux de victoire reel observe est bien proche de X%. "
        "Calibrable uniquement pour v1.4/v1.5/v1.8/v1.10 (cote et EV journalises - "
        "place et 2sur4 ne journalisent pas leur probabilite). "
        "Un ecart important signale un modele mal calibre (trop optimiste ou trop prudent), "
        "meme si son ROI global reste correct.</i>\n\n"
    )

    donnees_par_modele = {}

    for cle in MODELES_CALIBRABLES:
        nom = NOMS_AFFICHAGE[cle]
        nom_log = cle_log_modele(cle)
        sous = [
            l for l in lignes_csv
            if l.get("modele") == nom_log and l.get("resultat") in ("GAGNANT", "PERDANT")
            and l.get("cote") and l.get("ev")
        ]
        if len(sous) < 30:
            msg += f"<b>{nom}</b> : echantillon trop petit (n={len(sous)})\n\n"
            continue

        lignes_avec_proba = []
        for l in sous:
            try:
                cote = float(l["cote"])
                ev = float(l["ev"])
                if cote <= 0:
                    continue
                proba = (ev + 1) / cote
                gagnant = 1 if l["resultat"] == "GAGNANT" else 0
                lignes_avec_proba.append({
                    "proba": proba, "gagnant": gagnant,
                    "race_id": l.get("race_id", ""), "date_detection": l.get("date_detection", ""),
                })
            except (ValueError, ZeroDivisionError):
                continue

        donnees_par_modele[cle] = lignes_avec_proba

        msg += f"<b>{nom}</b> (n={len(lignes_avec_proba)})\n"
        au_moins_un_bin = False
        for bas, haut in BINS:
            sous_bin = [x for x in lignes_avec_proba if bas <= x["proba"] < haut]
            if len(sous_bin) < 10:
                continue
            au_moins_un_bin = True
            n_bin = len(sous_bin)
            proba_moy = sum(x["proba"] for x in sous_bin) / n_bin
            taux_reel = sum(x["gagnant"] for x in sous_bin) / n_bin
            ecart = taux_reel - proba_moy
            symbole = "≈" if abs(ecart) < 0.03 else ("🔺" if ecart > 0 else "🔻")
            haut_affiche = f"{haut:.0%}" if haut <= 1.0 else "100%+"
            msg += f"  {bas:.0%}-{haut_affiche} : predit {proba_moy:.1%}, reel {taux_reel:.1%} {symbole} (n={n_bin})\n"
        if not au_moins_un_bin:
            msg += "  Pas encore assez de paris par tranche pour une calibration fiable.\n"
        msg += "\n"

    # --- NOUVEAU : deux tests pour distinguer derive reelle vs sequence
    # malchanceuse correlee, sur le sous-ensemble haute confiance (>=30%) ---
    msg += "🔬 <b>Tests de diagnostic (tranche haute confiance ≥30%)</b>\n\n"

    for cle in MODELES_CALIBRABLES:
        if cle not in donnees_par_modele:
            continue
        nom = NOMS_AFFICHAGE[cle]
        haute_confiance = [x for x in donnees_par_modele[cle] if x["proba"] >= SEUIL_HAUTE_CONFIANCE and x["date_detection"]]
        if len(haute_confiance) < 20:
            continue
        haute_confiance_triee = sorted(haute_confiance, key=lambda x: x["date_detection"])
        milieu = len(haute_confiance_triee) // 2
        premiere_moitie = haute_confiance_triee[:milieu]
        deuxieme_moitie = haute_confiance_triee[milieu:]
        taux_1 = sum(x["gagnant"] for x in premiere_moitie) / len(premiere_moitie) if premiere_moitie else 0
        taux_2 = sum(x["gagnant"] for x in deuxieme_moitie) / len(deuxieme_moitie) if deuxieme_moitie else 0
        msg += (
            f"<b>{nom}</b> - Test temporel : 1ere moitie (n={len(premiere_moitie)}) "
            f"taux={taux_1:.1%} vs 2eme moitie (n={len(deuxieme_moitie)}) taux={taux_2:.1%}\n"
        )

    # Test de chevauchement entre modeles
    ensembles_race_ids = {}
    for cle in MODELES_CALIBRABLES:
        if cle not in donnees_par_modele:
            continue
        haute_confiance = [x for x in donnees_par_modele[cle] if x["proba"] >= SEUIL_HAUTE_CONFIANCE and x["race_id"]]
        ensembles_race_ids[cle] = set(x["race_id"] for x in haute_confiance)

    if len(ensembles_race_ids) >= 2:
        toutes_courses = set()
        for s in ensembles_race_ids.values():
            toutes_courses |= s
        courses_avec_chevauchement = 0
        for course in toutes_courses:
            nb_modeles_presents = sum(1 for s in ensembles_race_ids.values() if course in s)
            if nb_modeles_presents >= 2:
                courses_avec_chevauchement += 1
        taux_chevauchement = courses_avec_chevauchement / len(toutes_courses) if toutes_courses else 0
        msg += (
            f"\n<b>Test de chevauchement</b> : {len(toutes_courses)} courses distinctes concernees, "
            f"{courses_avec_chevauchement} ({taux_chevauchement:.0%}) ont un pari haute-confiance "
            f"sur au moins 2 modeles simultanement. Un taux eleve confirme que les modeles "
            f"ne sont pas des confirmations independantes sur ces courses.\n"
        )

    return msg



    """Parcourt chronologiquement les paris deja tries et retrouve
    l'index exact ou n>=SEUIL_MIN_PARIS_SORTIE_BRUIT ET la largeur d'IC
    passe sous SEUIL_LARGEUR_IC_SORTIE_BRUIT pour la premiere fois.
    Retourne le date_detection du pari a cet index (le point de
    bascule), ou None si jamais atteint."""
    n_total = len(returns_tries)
    if n_total < SEUIL_MIN_PARIS_SORTIE_BRUIT:
        return None
    for i in range(SEUIL_MIN_PARIS_SORTIE_BRUIT, n_total + 1):
        sous_returns = returns_tries[:i]
        if len(sous_returns) > 1:
            ecart_type = statistics.stdev(sous_returns)
            largeur = 2 * 1.96 * ecart_type / (i ** 0.5)
            if largeur <= SEUIL_LARGEUR_IC_SORTIE_BRUIT:
                return sous_tries[i - 1].get("date_detection", "")
    return None


def traiter_progression():
    chemin_log = f"{RACINE}/paris_virtuels.csv"
    lignes_csv = []
    if os.path.exists(chemin_log):
        with open(chemin_log, "r", encoding="utf-8") as f:
            lignes_csv = list(csv.DictReader(f))

    dates_sortie_bruit = charger_json(f"{RACINE}/dates_sortie_bruit.json", {})
    fichier_modifie = False

    maintenant = datetime.now(timezone.utc)

    msg = "🎯 <b>Progression vers la sortie du bruit</b>\n\n"
    msg += (
        f"<i>Criteres : n≥{SEUIL_MIN_PARIS_SORTIE_BRUIT} paris ET intervalle "
        f"de confiance direct-seul (independant du backtest) ≤{SEUIL_LARGEUR_IC_SORTIE_BRUIT:.0%} "
        f"de largeur. Estimation basee sur le rythme reel observe depuis le premier pari de chaque strategie. "
        f"A partir de 100 paris, alerte de derive si les 100 derniers divergent de la moyenne long terme "
        f"(detection de changement de regime). Une fois sortie du bruit confirmee, un compteur separe "
        f"suit les resultats UNIQUEMENT depuis ce point precis (le pari technique continue en continu "
        f"pendant toute la periode, seul l'affichage separe l'avant/apres).</i>\n\n"
    )

    for cle in MODELES:
        nom = NOMS_AFFICHAGE[cle]
        nom_log = cle_log_modele(cle)
        sous = [l for l in lignes_csv if l.get("modele") == nom_log and l.get("resultat", "") != ""]
        n = len(sous)

        if n == 0:
            msg += f"⚪ <b>{nom}</b>\nAucun pari resolu pour l'instant.\n\n"
            continue

        returns = []
        for l in sous:
            mise = float(l.get("mise", 0) or 0)
            gain = float(l.get("gain_euros", 0) or 0)
            if mise > 0:
                returns.append(gain / mise)

        roi_direct = sum(returns) / len(returns) if returns else 0

        # --- NOUVEAU (23 aout) : detection de derive recente (IC glissant sur les 100 derniers paris) ---
        alerte_derive = ""
        if len(returns) >= 100:
            sous_avec_date = [l for l in sous if l.get("race_id")]
            sous_tries = sorted(sous_avec_date, key=lambda l: l.get("race_id", ""))
            returns_tries = []
            for l in sous_tries:
                mise = float(l.get("mise", 0) or 0)
                gain = float(l.get("gain_euros", 0) or 0)
                if mise > 0:
                    returns_tries.append(gain / mise)
            recents_100 = returns_tries[-100:]
            if len(recents_100) == 100:
                roi_recent = sum(recents_100) / 100
                ecart_type_recent = statistics.stdev(recents_100)
                erreur_type_recent = ecart_type_recent / (100 ** 0.5)
                ic_bas_recent = roi_recent - 1.96 * erreur_type_recent
                ic_haut_recent = roi_recent + 1.96 * erreur_type_recent
                roi_long_terme = sum(returns) / len(returns)
                if not (ic_bas_recent <= roi_long_terme <= ic_haut_recent):
                    alerte_derive = (
                        f"⚠️ Derive recente possible : les 100 derniers paris "
                        f"donnent {roi_recent:+.1%} [{ic_bas_recent:+.1%},{ic_haut_recent:+.1%}], "
                        f"hors de cet intervalle par rapport a la moyenne long terme ({roi_long_terme:+.1%}).\n"
                    )
                else:
                    alerte_derive = f"✓ 100 derniers paris ({roi_recent:+.1%}) coherents avec la moyenne long terme.\n"
        else:
            sous_tries = sorted([l for l in sous if l.get("race_id")], key=lambda l: l.get("race_id", ""))
            returns_tries = []
            for l in sous_tries:
                mise = float(l.get("mise", 0) or 0)
                gain = float(l.get("gain_euros", 0) or 0)
                if mise > 0:
                    returns_tries.append(gain / mise)

        largeur_ic = None
        if len(returns) > 1:
            ecart_type = statistics.stdev(returns)
            erreur_type = ecart_type / (n ** 0.5)
            largeur_ic = 2 * 1.96 * erreur_type

        dates_races = []
        for l in sous:
            rid = l.get("race_id", "")
            if len(rid) >= 8:
                try:
                    dates_races.append(datetime.strptime(rid[:8], "%d%m%Y").replace(tzinfo=timezone.utc))
                except ValueError:
                    continue

        if dates_races:
            premiere_date = min(dates_races)
            jours_ecoules = max((maintenant - premiere_date).days, 1)
            rythme = n / jours_ecoules
        else:
            rythme = 0

        critere_n = n >= SEUIL_MIN_PARIS_SORTIE_BRUIT
        critere_ic = largeur_ic is not None and largeur_ic <= SEUIL_LARGEUR_IC_SORTIE_BRUIT
        pret = critere_n and critere_ic

        if pret:
            msg += f"✅ <b>{nom}</b> — SORTI DU BRUIT\n"
            msg += f"n={n}, ROI direct (total)={roi_direct:+.1%}, IC95%~±{largeur_ic/2:.1%}\n"
            msg += alerte_derive

            # --- NOUVEAU (26 aout) : compteur separe depuis la sortie du bruit ---
            if cle not in dates_sortie_bruit:
                point_sortie = trouver_point_sortie_bruit(sous_tries, returns_tries)
                if point_sortie:
                    dates_sortie_bruit[cle] = point_sortie
                    fichier_modifie = True

            date_sortie_str = dates_sortie_bruit.get(cle)
            if date_sortie_str:
                sous_post = [l for l in sous if l.get("date_detection", "") > date_sortie_str]
                if sous_post:
                    n_post = len(sous_post)
                    returns_post = []
                    for l in sous_post:
                        mise = float(l.get("mise", 0) or 0)
                        gain = float(l.get("gain_euros", 0) or 0)
                        if mise > 0:
                            returns_post.append(gain / mise)
                    roi_post = sum(returns_post) / len(returns_post) if returns_post else 0
                    msg += f"📍 <b>Depuis la sortie du bruit</b> : n={n_post}, ROI={roi_post:+.1%}\n"
                else:
                    msg += f"📍 <b>Depuis la sortie du bruit</b> : n=0 (aucun nouveau pari resolu depuis)\n"

            msg += "\n"
            continue

        ic_texte = f"±{largeur_ic/2:.1%}" if largeur_ic is not None else "n/a"
        msg += f"⏳ <b>{nom}</b>\n"
        msg += f"n={n}/{SEUIL_MIN_PARIS_SORTIE_BRUIT}, ROI direct={roi_direct:+.1%}, IC95%~{ic_texte}\n"
        msg += alerte_derive

        if not critere_n:
            if rythme > 0:
                jours_restants_n = max(0, (SEUIL_MIN_PARIS_SORTIE_BRUIT - n) / rythme)
                msg += f"Rythme actuel : {rythme:.1f} paris/jour → ~{jours_restants_n:.0f} jours avant n={SEUIL_MIN_PARIS_SORTIE_BRUIT}\n"
            else:
                msg += "Rythme insuffisant pour estimer un delai.\n"

        if not critere_ic and largeur_ic is not None:
            msg += f"IC encore trop large ({largeur_ic:.1%} de largeur, cible ≤{SEUIL_LARGEUR_IC_SORTIE_BRUIT:.0%}) - se resserrera avec plus de paris.\n"

        msg += "\n"

    if fichier_modifie:
        sauvegarder_json(f"{RACINE}/dates_sortie_bruit.json", dates_sortie_bruit)

    return msg


def traiter_clv():
    chemin_log = f"{RACINE}/paris_virtuels.csv"
    if not os.path.exists(chemin_log):
        return "Aucun pari enregistre pour l'instant."
    with open(chemin_log, "r", encoding="utf-8") as f:
        lignes_csv = list(csv.DictReader(f))

    msg = "📈 <b>Closing Line Value (CLV) moyen par modele</b>\n\n"
    msg += "<i>Compare la cote au moment du pari a la cote de fermeture. Positif = on parie systematiquement a de meilleures cotes que le marche final.</i>\n\n"

    au_moins_une_donnee = False
    for cle in MODELES_AVEC_CLV:
        nom = NOMS_AFFICHAGE[cle]
        nom_log = cle_log_modele(cle)
        sous = [
            l for l in lignes_csv
            if l.get("modele") == nom_log and l.get("resultat", "") != ""
            and l.get("cote_cloture", "") not in ("", None)
        ]
        if not sous:
            msg += f"<b>{nom}</b>\nPas encore de donnees CLV.\n\n"
            continue

        au_moins_une_donnee = True
        clv_valeurs = []
        for l in sous:
            try:
                cote_detection = float(l["cote"])
                cote_cloture = float(l["cote_cloture"])
                if cote_cloture > 0:
                    clv_valeurs.append((cote_detection / cote_cloture) - 1)
            except (ValueError, ZeroDivisionError):
                continue

        if not clv_valeurs:
            msg += f"<b>{nom}</b>\nPas encore de donnees CLV exploitables.\n\n"
            continue

        clv_moyen = sum(clv_valeurs) / len(clv_valeurs)
        pct_positif = sum(1 for v in clv_valeurs if v > 0) / len(clv_valeurs)
        msg += (
            f"<b>{nom}</b>\n"
            f"CLV moyen : {clv_moyen:+.2%} (n={len(clv_valeurs)})\n"
            f"Paris qui battent la cloture : {pct_positif:.1%}\n\n"
        )

    if not au_moins_une_donnee:
        msg += "\nAucune donnee CLV disponible pour l'instant."

    return msg


def normaliser_cle_modele(argument):
    for cle, nom in NOMS_AFFICHAGE.items():
        if argument in (cle, nom.lower()):
            return cle
    return None


def traiter_pause(argument, mettre_en_pause):
    etat_pause = charger_json(f"{RACINE}/etat_pause.json", {})
    if argument == "tout":
        for cle in MODELES:
            etat_pause[cle] = mettre_en_pause
        cibles = "toutes les strategies"
    else:
        cle_trouvee = normaliser_cle_modele(argument)
        if not cle_trouvee:
            return f"Strategie '{argument}' inconnue. Utilise : {', '.join(NOMS_AFFICHAGE.values())} ou 'tout'."
        etat_pause[cle_trouvee] = mettre_en_pause
        cibles = NOMS_AFFICHAGE[cle_trouvee]

    sauvegarder_json(f"{RACINE}/etat_pause.json", etat_pause)
    action = "mises en pause" if mettre_en_pause else "reactivees"
    return (
        f"🔕 Notifications {action} pour : {cibles}\n\n"
        "(Les strategies continuent de parier normalement en arriere-plan "
        "- seules les notifications Telegram sont affectees.)"
    )


def traiter_courses_restantes():
    date_str = datetime.now(timezone.utc).strftime("%d%m%Y")
    url = f"https://online.turfinfo.api.pmu.fr/rest/client/61/programme/{date_str}"
    try:
        r = requests.get(url, timeout=35)
        data = r.json()
    except Exception as e:
        return f"Erreur recuperation programme : {e}"

    maintenant = datetime.now(timezone.utc)
    courses_restantes = []
    for reunion in data.get("programme", {}).get("reunions", []):
        hippodrome = reunion.get("hippodrome", {}).get("libelleCourt", reunion.get("hippodrome", {}).get("libelle", "?"))
        num_reunion = reunion.get("numOfficiel", reunion.get("numExterne"))
        for course in reunion.get("courses", []):
            if course.get("discipline") not in ("ATTELE", "MONTE"):
                continue
            heure_ms = course.get("heureDepart")
            if heure_ms is None:
                continue
            heure_depart = datetime.fromtimestamp(heure_ms / 1000, tz=timezone.utc)
            if heure_depart > maintenant:
                num_course = course.get("numOrdre", course.get("numExterne"))
                courses_restantes.append(f"R{num_reunion}C{num_course} {hippodrome} — {heure_depart.strftime('%Hh%M')} UTC")

    if not courses_restantes:
        return "Aucune course de trot restante aujourd'hui."
    return f"🐎 <b>{len(courses_restantes)} courses restantes aujourd'hui</b>\n\n" + "\n".join(courses_restantes)


def traiter_courses_non_jouees():
    courses_notifiees = charger_json(f"{RACINE}/courses_notifiees.json", {})
    chemin_log = f"{RACINE}/paris_virtuels.csv"
    courses_avec_paris = set()
    if os.path.exists(chemin_log):
        with open(chemin_log, "r", encoding="utf-8") as f:
            for l in csv.DictReader(f):
                courses_avec_paris.add(l.get("race_id"))

    aujourd_hui = datetime.now(timezone.utc).strftime("%d%m%Y")
    non_jouees = []
    for race_id, infos in courses_notifiees.items():
        if not race_id.startswith(aujourd_hui):
            continue
        if race_id not in courses_avec_paris:
            hippo = infos.get("hippodrome", "?") if isinstance(infos, dict) else "?"
            non_jouees.append(f"{race_id} ({hippo})")

    if not non_jouees:
        return "Toutes les courses traitees aujourd'hui ont genere au moins un pari."
    return f"🔍 <b>{len(non_jouees)} courses sans aucun pari aujourd'hui</b>\n\n" + "\n".join(non_jouees)


def main():
    messages = recuperer_nouveaux_messages()
    for texte in messages:
        parties = texte.strip().split(maxsplit=1)
        commande = parties[0].lower()
        argument = parties[1].strip().lower() if len(parties) > 1 else ""

        if commande == "/statut":
            envoyer_telegram(traiter_statut())
        elif commande == "/progression":
            envoyer_telegram(traiter_progression())
        elif commande == "/calibration":
            envoyer_telegram(traiter_calibration())
        elif commande == "/portefeuille":
            envoyer_telegram(traiter_portefeuille())
        elif commande == "/bankroll":
            envoyer_telegram(traiter_bankroll())
        elif commande == "/bilan":
            envoyer_telegram(traiter_bilan(argument))
        elif commande == "/confiance":
            envoyer_telegram(traiter_confiance())
        elif commande == "/clv":
            envoyer_telegram(traiter_clv())
        elif commande == "/pause":
            if not argument:
                envoyer_telegram("Precise une strategie ou 'tout'. Ex : /pause trio")
            else:
                envoyer_telegram(traiter_pause(argument, True))
        elif commande == "/reprendre":
            if not argument:
                envoyer_telegram("Precise une strategie ou 'tout'. Ex : /reprendre trio")
            else:
                envoyer_telegram(traiter_pause(argument, False))
        elif commande == "/courses_restantes":
            envoyer_telegram(traiter_courses_restantes())
        elif commande == "/courses_non_jouees":
            envoyer_telegram(traiter_courses_non_jouees())
        elif commande == "/aide":
            envoyer_telegram(TEXTE_AIDE)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        detail = traceback.format_exc()[-500:]
        detail_echappe = detail.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        envoyer_telegram(f"🔴 Erreur dans commandes_telegram.py\n\n{e}\n\n{detail_echappe}")
        raise
