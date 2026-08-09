"""
=============================================================================
COMMANDES_TELEGRAM.PY - Interroge et pilote le systeme via Telegram
=============================================================================
Interroge les nouveaux messages Telegram (via getUpdates) a chaque cycle,
execute les commandes reconnues, repond directement dans Telegram.

IMPORTANT : /pause et /reprendre ne touchent QUE les notifications
Telegram. Les strategies continuent de calculer et de parier normalement
en arriere-plan, quelle que soit la commande - seule la visibilite dans
le chat est affectee.

Delai de reponse : jusqu'a ~15 minutes (rythme du cron GitHub Actions).

10 modeles maintenant : v14, v14sire (nouveau - v1.4 + genealogie du
pere, deploye le 9 aout), v15, v18, v110, place, 2sur4, trio, multi,
2favori.
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

MODELES = ["v14", "v14sire", "v15", "v18", "v110", "place", "2sur4", "trio", "multi", "2favori"]
NOMS_AFFICHAGE = {
    "v14": "v1.4", "v14sire": "v1.4+Genealogie", "v15": "v1.5", "v18": "v1.8", "v110": "v1.10",
    "place": "place", "2sur4": "2sur4", "trio": "trio", "multi": "multi",
    "2favori": "2favori",
}

# Reference backtest (walk-forward, flat-bet) pour chaque modele.
# v14sire : v1.4 + sire_forme (genealogie), 30042 paris, ROI +11.76%
# (walk-forward complet, teste le 9 aout).
REFERENCE_BACKTEST = {
    "v14": {"n": 30984, "roi": 0.1075},
    "v14sire": {"n": 30042, "roi": 0.1176},
    "v15": {"n": 31756, "roi": 0.1391},
    "v18": {"n": 34248, "roi": 0.1556},
    "v110": {"n": 34379, "roi": 0.1879},
    "place": {"n": 16635, "roi": 0.233},
    "2sur4": {"n": 10312, "roi": 0.838},
    "trio": {"n": 14993, "roi": 1.358},
    "multi": {"n": 10206, "roi": 4.60},
    "2favori": {"n": 2645, "roi": 0.2501},
}

TEXTE_AIDE = (
    "<b>Commandes disponibles</b>\n\n"
    "/bankroll — bankrolls actuelles des 10 strategies\n"
    "/bilan — bilan du jour (gain, perte, ROI, nb paris)\n"
    "/bilan JJ/MM/AAAA — bilan d'une date precise\n"
    "/bilan cumule — bilan depuis le debut\n"
    "/confiance — estimation combinee backtest+direct, par modele\n"
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
    """Accepte JJMMAAAA, JJ/MM/AAAA, JJ-MM-AAAA, etc. - ne garde que les
    chiffres et verifie qu'on obtient bien 8 caracteres."""
    chiffres = "".join(c for c in argument if c.isdigit())
    if len(chiffres) == 8:
        return chiffres
    return None


def cle_log_modele(cle):
    """Le nom stocke dans paris_virtuels.csv/journal_audit.csv differe
    parfois de la cle interne - v14sire est stocke tel quel (pas
    'v1.4+Genealogie'), les autres utilisent le nom d'affichage."""
    if cle == "v14sire":
        return "v14sire"
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
    nb_total_global = 0
    for cle in MODELES:
        nom = NOMS_AFFICHAGE[cle]
        stats = calculer_stats_modele(lignes_filtrees, cle_log_modele(cle))
        msg += f"<b>{nom}</b>\n"
        if stats:
            msg += f"{stats['nb']} paris, {stats['taux']:.1%} reussite\n"
            msg += f"Gain net : {stats['gain']:+.2f}€ (ROI {stats['roi']:+.1%})\n\n"
            nb_total_global += stats["nb"]
            if stats["gain"] >= 0:
                gain_total_global += stats["gain"]
            else:
                perte_total_global += stats["gain"]
        else:
            msg += "Aucun pari.\n\n"

    msg += (
        f"<b>Total</b> : {nb_total_global} paris | "
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

        if commande == "/bankroll":
            envoyer_telegram(traiter_bankroll())
        elif commande == "/bilan":
            envoyer_telegram(traiter_bilan(argument))
        elif commande == "/confiance":
            envoyer_telegram(traiter_confiance())
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
        # commandes inconnues ignorees silencieusement


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        detail = traceback.format_exc()[-500:]
        detail_echappe = detail.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        envoyer_telegram(f"🔴 <b>Erreur dans commandes_telegram.py</b>\n\n{e}\n\n<code>{detail_echappe}</code>")
        raise
