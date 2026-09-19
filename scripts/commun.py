"""
=============================================================================
FONCTIONS COMMUNES - utilisees par verifier_a_venir.py et verifier_resultats.py
=============================================================================
CORRECTION MAJEURE (21 aout, soir) : les mises doivent etre des
MONTANTS ENTIERS EN EUROS (pas de centimes), minimum 1EUR.

CORRIGE (11 sept) : envoyer_telegram() decoupe desormais
automatiquement les messages trop longs (limite Telegram : 4096
caracteres).

NOUVEAU (17 sept) : calculer_proba_v110_A() et
calculer_proba_v110_avec_entropie_et_contributions() ajoutees -
entropie comme 12e variable de v1.10 (sigmoid).

NOUVEAU (19 sept) : calculer_probas_conditionnel_course() ajoutee -
LOGIT CONDITIONNEL (softmax intra-course, McFadden/Benter), valide
cette semaine comme superieur au sigmoid sur toutes les dimensions
(Brier/log-loss 16/16 fenetres, ROI/pari 5x superieur, croissance
superieure, drawdown divise par 3.4, calibration verifiee saine).
DEPLOYE EN DOUBLE LOGGING : ce modele devient le VRAI v1.10 (mise
reelle, bankroll_v110), le sigmoid (modele_v110_production.json,
12 variables, avec entropie) reste calcule et loggue comme REFERENCE
DE COMPARAISON UNIQUEMENT - plus aucune mise reelle sur ses value
bets. Contrairement au sigmoid, le conditionnel prend TOUS les
chevaux d'une course en une fois (pas un cheval a la fois) et
n'utilise QUE 9 variables per-cheval (biais_hippodrome,
nb_partants_course et entropie sont CONSTANTES par course, donc
INERTES sous softmax par construction - testees en interaction le 19
sept, sans effet). AUCUNE constante dans ce modele (s'annule sous
softmax).
=============================================================================
"""

import json
import math
import os
import requests
import numpy as np


def charger_json(chemin, defaut=None):
    if os.path.exists(chemin):
        with open(chemin, "r", encoding="utf-8") as f:
            return json.load(f)
    return defaut if defaut is not None else {}


def sauvegarder_json(chemin, data):
    with open(chemin, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)


LIMITE_TELEGRAM = 4000


def decouper_message(message, limite=LIMITE_TELEGRAM):
    if len(message) <= limite:
        return [message]

    morceaux = []
    reste = message
    while len(reste) > limite:
        point_coupure = reste.rfind("\n\n", 0, limite)
        if point_coupure == -1:
            point_coupure = reste.rfind("\n", 0, limite)
        if point_coupure == -1 or point_coupure < limite // 2:
            point_coupure = limite
        morceaux.append(reste[:point_coupure])
        reste = reste[point_coupure:].lstrip("\n")
    if reste:
        morceaux.append(reste)
    return morceaux


def envoyer_telegram(message):
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        print("ATTENTION : TELEGRAM_BOT_TOKEN ou TELEGRAM_CHAT_ID manquant, message non envoye.")
        print(message)
        return

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    morceaux = decouper_message(message)
    nb_morceaux = len(morceaux)

    for i, morceau in enumerate(morceaux):
        texte_envoye = morceau
        if nb_morceaux > 1:
            texte_envoye = f"[{i+1}/{nb_morceaux}]\n{morceau}"
        try:
            r = requests.post(url, data={"chat_id": chat_id, "text": texte_envoye, "parse_mode": "HTML"}, timeout=15)
            if r.status_code != 200:
                print(f"Erreur envoi Telegram ({r.status_code}) : {r.text}")
        except Exception as e:
            print(f"Exception envoi Telegram : {e}")


def sigmoid(x):
    try:
        return 1 / (1 + math.exp(-x))
    except OverflowError:
        return 0.0 if x < 0 else 1.0


def calculer_proba(valeurs_brutes, modele):
    coefs = modele["coefficients"]
    norm = modele["normalisation"]
    z = coefs.get("const", 0.0)

    for var in modele["variables_brutes"]:
        if var not in valeurs_brutes or valeurs_brutes[var] is None:
            return None
        moyenne = norm[var]["moyenne"]
        ecart_type = norm[var]["ecart_type"]
        valeur_std = (valeurs_brutes[var] - moyenne) / ecart_type
        z += coefs.get(var + "_std", 0.0) * valeur_std

    return sigmoid(z)


def calculer_proba_v18(valeurs_brutes, modele_v18):
    coefs = modele_v18["coefficients"]
    norm = modele_v18["standardisation"]

    requis = ["speed_figure_avant_course", "driver_forme", "biais_hippodrome",
              "nb_partants_course", "ecart_corde", "log_cote", "deferre_4_pieds"]
    for var in requis:
        if var not in valeurs_brutes or valeurs_brutes[var] is None:
            return None

    def std(var):
        m = norm[var]["moyenne"]
        s = norm[var]["ecart_type"]
        return (valeurs_brutes[var] - m) / s

    sf_std = std("speed_figure_avant_course")
    driver_std = std("driver_forme")
    hippo_std = std("biais_hippodrome")
    nb_partants_std = std("nb_partants_course")
    ecart_corde_std = std("ecart_corde")
    interaction = sf_std * driver_std

    z = coefs.get("const", 0.0)
    z += coefs.get("sf_std", 0.0) * sf_std
    z += coefs.get("log_cote", 0.0) * valeurs_brutes["log_cote"]
    z += coefs.get("driver_std", 0.0) * driver_std
    z += coefs.get("hippo_std", 0.0) * hippo_std
    z += coefs.get("interaction_sf_driver", 0.0) * interaction
    z += coefs.get("nb_partants_std", 0.0) * nb_partants_std
    z += coefs.get("ecart_corde_std", 0.0) * ecart_corde_std
    z += coefs.get("deferre_4_pieds", 0.0) * valeurs_brutes["deferre_4_pieds"]

    return sigmoid(z)


def calculer_proba_v110_ou_place(valeurs_brutes, modele):
    coefs = modele["coefficients"]
    norm = modele["moyennes_ecarts_types"]

    requis = ["speed_figure_avant_course", "driver_forme", "biais_hippodrome",
              "nb_partants_course", "ecart_corde", "age", "taux_victoire_carriere"]
    for var in requis:
        if var not in valeurs_brutes or valeurs_brutes[var] is None:
            return None

    def std(var):
        m = norm[var]["moyenne"]
        s = norm[var]["ecart_type"]
        return (valeurs_brutes[var] - m) / s

    sf_std = std("speed_figure_avant_course")
    driver_std = std("driver_forme")
    hippo_std = std("biais_hippodrome")
    nb_partants_std = std("nb_partants_course")
    ecart_corde_std = std("ecart_corde")
    age_std = std("age")
    taux_victoire_std = std("taux_victoire_carriere")
    interaction = sf_std * driver_std

    z = coefs.get("const", 0.0)
    z += coefs.get("sf_std", 0.0) * sf_std
    z += coefs.get("log_cote", 0.0) * valeurs_brutes["log_cote"]
    z += coefs.get("driver_std", 0.0) * driver_std
    z += coefs.get("hippo_std", 0.0) * hippo_std
    z += coefs.get("interaction_sf_driver", 0.0) * interaction
    z += coefs.get("nb_partants_std", 0.0) * nb_partants_std
    z += coefs.get("ecart_corde_std", 0.0) * ecart_corde_std
    z += coefs.get("deferre_4_pieds", 0.0) * valeurs_brutes["deferre_4_pieds"]
    z += coefs.get("age_std", 0.0) * age_std
    z += coefs.get("indicateur_femelle", 0.0) * valeurs_brutes["indicateur_femelle"]
    z += coefs.get("taux_victoire_std", 0.0) * taux_victoire_std

    return sigmoid(z)


def extraire_age(participant):
    return participant.get("age")


def extraire_indicateur_femelle(participant):
    return 1 if participant.get("sexe") == "FEMELLES" else 0


def extraire_taux_victoire_carriere(participant):
    nb_courses = participant.get("nombreCourses")
    nb_victoires = participant.get("nombreVictoires")
    if not nb_courses or nb_courses == 0:
        return None
    return nb_victoires / nb_courses


FENETRE_DRIVER = 100
FENETRE_HIPPODROME = 200
FENETRE_CHEVAL = 3
MIN_DRIVER = 20
MIN_HIPPODROME = 10


def get_driver_forme(etat_drivers, driver):
    historique = etat_drivers.get(driver, [])
    if len(historique) < MIN_DRIVER:
        return None
    return sum(historique) / len(historique)


def maj_driver(etat_drivers, driver, victoire):
    historique = etat_drivers.get(driver, [])
    historique.append(victoire)
    etat_drivers[driver] = historique[-FENETRE_DRIVER:]


def get_biais_hippodrome(etat_hippodromes, hippodrome):
    donnees = etat_hippodromes.get(hippodrome)
    if donnees is None:
        return None
    nb_total = sum(donnees["nb_partants"])
    if nb_total < MIN_HIPPODROME:
        return None
    return sum(donnees["sommes_ecart"]) / nb_total


def maj_hippodrome(etat_hippodromes, hippodrome, somme_ecart_course, nb_partants_course):
    donnees = etat_hippodromes.get(hippodrome, {"sommes_ecart": [], "nb_partants": []})
    donnees["sommes_ecart"].append(somme_ecart_course)
    donnees["nb_partants"].append(nb_partants_course)
    donnees["sommes_ecart"] = donnees["sommes_ecart"][-FENETRE_HIPPODROME:]
    donnees["nb_partants"] = donnees["nb_partants"][-FENETRE_HIPPODROME:]
    etat_hippodromes[hippodrome] = donnees


def get_speed_figure_avant_course(etat_chevaux, cheval):
    historique = etat_chevaux.get(cheval, [])
    if len(historique) == 0:
        return None
    return sum(historique) / len(historique)


def maj_cheval(etat_chevaux, cheval, speed_figure_brut):
    historique = etat_chevaux.get(cheval, [])
    historique.append(speed_figure_brut)
    etat_chevaux[cheval] = historique[-FENETRE_CHEVAL:]


FENETRE_CORDE = 5
MIN_CORDE = 2


def get_ecart_corde(etat_chevaux_corde, etat_chevaux, cheval, corde_du_jour):
    donnees = etat_chevaux_corde.get(cheval, {})
    historique_corde = donnees.get(corde_du_jour, [])
    if len(historique_corde) < MIN_CORDE:
        return 0.0
    sf_corde_specifique = sum(historique_corde) / len(historique_corde)
    sf_general = get_speed_figure_avant_course(etat_chevaux, cheval)
    if sf_general is None:
        return 0.0
    return sf_corde_specifique - sf_general


def maj_cheval_corde(etat_chevaux_corde, cheval, corde, speed_figure_brut):
    if corde not in ("CORDE_GAUCHE", "CORDE_DROITE"):
        return
    donnees = etat_chevaux_corde.get(cheval, {"CORDE_GAUCHE": [], "CORDE_DROITE": []})
    historique = donnees.get(corde, [])
    historique.append(speed_figure_brut)
    donnees[corde] = historique[-FENETRE_CORDE:]
    etat_chevaux_corde[cheval] = donnees


def get_dernier_rang(etat_dernier_rang, cheval):
    return etat_dernier_rang.get(cheval)


def maj_dernier_rang(etat_dernier_rang, cheval, rang_arrivee):
    etat_dernier_rang[cheval] = rang_arrivee


FENETRE_SIRE = 100
MIN_SIRE = 15


def get_sire_forme(etat_sire_forme, pere):
    if not pere:
        return None
    historique = etat_sire_forme.get(pere, [])
    if len(historique) < MIN_SIRE:
        return None
    return sum(historique) / len(historique)


def maj_sire_forme(etat_sire_forme, pere, victoire):
    if not pere:
        return
    historique = etat_sire_forme.get(pere, [])
    historique.append(victoire)
    etat_sire_forme[pere] = historique[-FENETRE_SIRE:]


def charger_table_pedigree(chemin_csv):
    import csv as csv_module
    table = {}
    try:
        with open(chemin_csv, "r", encoding="utf-8") as f:
            reader = csv_module.DictReader(f)
            for ligne in reader:
                if ligne.get("pere"):
                    table[ligne["nom_pmu"]] = ligne["pere"]
    except FileNotFoundError:
        pass
    return table


FENETRE_DEFERRE = 2


def get_deferre_precedent(etat_deferrage, cheval):
    return etat_deferrage.get(cheval, [])


def maj_deferrage(etat_deferrage, cheval, deferre_4_pieds_actuel):
    historique = etat_deferrage.get(cheval, [])
    historique.append(deferre_4_pieds_actuel)
    etat_deferrage[cheval] = historique[-FENETRE_DEFERRE:]


def detecter_changement_vers_d4(historique_deferrage, deferre_4_pieds_actuel):
    return (
        deferre_4_pieds_actuel == 1
        and len(historique_deferrage) == 2
        and historique_deferrage[0] == 0
        and historique_deferrage[1] == 0
    )


def extraire_cote_directe(participant):
    rapport = participant.get("dernierRapportDirect")
    if rapport and rapport.get("typePari") == "SIMPLE_GAGNANT":
        return rapport.get("rapport")
    return None


def extraire_deferre_4_pieds(participant):
    return 1 if participant.get("deferre") == "DEFERRE_ANTERIEURS_POSTERIEURS" else 0


def charger_table_calibration(racine):
    chemin = f"{racine}/table_calibration_isotonique.json"
    if not os.path.exists(chemin):
        return {}
    try:
        with open(chemin, "r") as f:
            return json.load(f)
    except Exception:
        return {}


SEUIL_ERREUR_CALIBRATION = 0.04


def appliquer_calibration(table_calibration, cle_modele, proba_brute):
    table = table_calibration.get(cle_modele)
    if not table or not table.get("x") or not table.get("y"):
        return proba_brute
    if table.get("erreur_calibration", 0) <= SEUIL_ERREUR_CALIBRATION:
        return proba_brute
    xs, ys = table["x"], table["y"]
    if proba_brute <= xs[0]:
        return ys[0]
    if proba_brute >= xs[-1]:
        return ys[-1]
    for i in range(len(xs) - 1):
        if xs[i] <= proba_brute <= xs[i + 1]:
            if xs[i + 1] == xs[i]:
                return ys[i]
            fraction = (proba_brute - xs[i]) / (xs[i + 1] - xs[i])
            return ys[i] + fraction * (ys[i + 1] - ys[i])
    return proba_brute


FRACTION_KELLY = 0.10
FRACTION_KELLY_D4 = 0.05
MISE_MINIMUM = 1.0
BANKROLL_DEPART = 1236
MISE_FIXE_PLACE = 10

PALIERS_PLAFOND = [
    (5000, 20),
    (20000, 50),
    (50000, 100),
    (150000, 250),
    (float("inf"), 500),
]

PLAFOND_GAIN_PMU = 100000


def arrondir_mise_euro(mise):
    mise_arrondie = round(mise)
    if mise_arrondie < MISE_MINIMUM:
        return 0.0
    return float(mise_arrondie)


def obtenir_plafond_dynamique(bankroll):
    for seuil, plafond in PALIERS_PLAFOND:
        if bankroll < seuil:
            return plafond
    return PALIERS_PLAFOND[-1][1]


def get_bankroll(racine, nom_modele):
    chemin = f"{racine}/bankroll_{nom_modele}.json"
    data = charger_json(chemin, {"bankroll": BANKROLL_DEPART})
    return data["bankroll"], chemin


def calculer_mise(proba, cote, bankroll):
    b = cote - 1
    if b <= 0:
        return 0.0
    kelly_full = max(0.0, (proba * b - (1 - proba)) / b)
    kelly_fraction = kelly_full * FRACTION_KELLY
    plafond_palier = obtenir_plafond_dynamique(bankroll)
    plafond_gain = PLAFOND_GAIN_PMU / cote
    mise = min(kelly_fraction * bankroll, plafond_palier, plafond_gain)
    return arrondir_mise_euro(mise)


def calculer_mise_v18(proba, cote, bankroll, est_deferre_4_pieds):
    b = cote - 1
    if b <= 0:
        return 0.0
    kelly_full = max(0.0, (proba * b - (1 - proba)) / b)
    fraction = FRACTION_KELLY_D4 if est_deferre_4_pieds else FRACTION_KELLY
    kelly_fraction = kelly_full * fraction
    plafond_palier = obtenir_plafond_dynamique(bankroll)
    plafond_gain = PLAFOND_GAIN_PMU / cote
    mise = min(kelly_fraction * bankroll, plafond_palier, plafond_gain)
    return arrondir_mise_euro(mise)


def calculer_mise_v110(proba, cote, bankroll, est_deferre_4_pieds):
    b = cote - 1
    if b <= 0:
        return 0.0
    kelly_full = max(0.0, (proba * b - (1 - proba)) / b)
    fraction = FRACTION_KELLY_D4 if est_deferre_4_pieds else FRACTION_KELLY
    kelly_fraction = kelly_full * fraction
    plafond_palier = obtenir_plafond_dynamique(bankroll)
    plafond_gain = PLAFOND_GAIN_PMU / cote
    mise = min(kelly_fraction * bankroll, plafond_palier, plafond_gain)
    return arrondir_mise_euro(mise)


def calculer_mise_place(bankroll):
    if bankroll < MISE_FIXE_PLACE:
        return 0.0
    return float(MISE_FIXE_PLACE)


def calculer_mise_2favori(proba, cote, bankroll):
    b = cote - 1
    if b <= 0:
        return 0.0
    kelly_full = max(0.0, (proba * b - (1 - proba)) / b)
    kelly_fraction = kelly_full * FRACTION_KELLY
    plafond_palier = obtenir_plafond_dynamique(bankroll)
    plafond_gain = PLAFOND_GAIN_PMU / cote
    mise = min(kelly_fraction * bankroll, plafond_palier, plafond_gain)
    return arrondir_mise_euro(mise)


def calculer_mise_v14sire(proba, cote, bankroll):
    b = cote - 1
    if b <= 0:
        return 0.0
    kelly_full = max(0.0, (proba * b - (1 - proba)) / b)
    kelly_fraction = kelly_full * FRACTION_KELLY
    plafond_palier = obtenir_plafond_dynamique(bankroll)
    plafond_gain = PLAFOND_GAIN_PMU / cote
    mise = min(kelly_fraction * bankroll, plafond_palier, plafond_gain)
    return arrondir_mise_euro(mise)


def mettre_a_jour_bankroll(chemin, nouvelle_bankroll):
    sauvegarder_json(chemin, {"bankroll": round(max(nouvelle_bankroll, 0), 2)})


def calculer_proba_avec_contributions(valeurs_brutes, modele):
    coefs = modele["coefficients"]
    norm = modele["normalisation"]
    contributions = {}
    z = coefs.get("const", 0.0)

    for var in modele["variables_brutes"]:
        if var not in valeurs_brutes or valeurs_brutes[var] is None:
            return None, {}
        moyenne = norm[var]["moyenne"]
        ecart_type = norm[var]["ecart_type"]
        valeur_std = (valeurs_brutes[var] - moyenne) / ecart_type
        contribution = coefs.get(var + "_std", 0.0) * valeur_std
        contributions[var] = contribution
        z += contribution

    return sigmoid(z), contributions


def calculer_proba_v18_avec_contributions(valeurs_brutes, modele_v18):
    coefs = modele_v18["coefficients"]
    norm = modele_v18["standardisation"]

    requis = ["speed_figure_avant_course", "driver_forme", "biais_hippodrome",
              "nb_partants_course", "ecart_corde", "log_cote", "deferre_4_pieds"]
    for var in requis:
        if var not in valeurs_brutes or valeurs_brutes[var] is None:
            return None, {}

    def std(var):
        m = norm[var]["moyenne"]
        s = norm[var]["ecart_type"]
        return (valeurs_brutes[var] - m) / s

    sf_std = std("speed_figure_avant_course")
    driver_std = std("driver_forme")
    hippo_std = std("biais_hippodrome")
    nb_partants_std = std("nb_partants_course")
    ecart_corde_std = std("ecart_corde")
    interaction = sf_std * driver_std

    contributions = {
        "vitesse_recente": coefs.get("sf_std", 0.0) * sf_std,
        "cote_marche": coefs.get("log_cote", 0.0) * valeurs_brutes["log_cote"],
        "driver": coefs.get("driver_std", 0.0) * driver_std,
        "hippodrome": coefs.get("hippo_std", 0.0) * hippo_std,
        "interaction_cheval_driver": coefs.get("interaction_sf_driver", 0.0) * interaction,
        "nb_partants": coefs.get("nb_partants_std", 0.0) * nb_partants_std,
        "corde": coefs.get("ecart_corde_std", 0.0) * ecart_corde_std,
        "deferrage": coefs.get("deferre_4_pieds", 0.0) * valeurs_brutes["deferre_4_pieds"],
    }
    z = coefs.get("const", 0.0) + sum(contributions.values())
    return sigmoid(z), contributions


def calculer_proba_v110_ou_place_avec_contributions(valeurs_brutes, modele):
    """RESERVEE au modele PLACE (11 variables standard, sans entropie).
    Le modele v1.10 SIGMOID (reference, 12 var, avec entropie) utilise
    calculer_proba_v110_avec_entropie_et_contributions. Le VRAI v1.10
    (mise reelle) utilise desormais calculer_probas_conditionnel_course."""
    coefs = modele["coefficients"]
    norm = modele["moyennes_ecarts_types"]

    requis = ["speed_figure_avant_course", "driver_forme", "biais_hippodrome",
              "nb_partants_course", "ecart_corde", "age", "taux_victoire_carriere"]
    for var in requis:
        if var not in valeurs_brutes or valeurs_brutes[var] is None:
            return None, {}

    def std(var):
        m = norm[var]["moyenne"]
        s = norm[var]["ecart_type"]
        return (valeurs_brutes[var] - m) / s

    sf_std = std("speed_figure_avant_course")
    driver_std = std("driver_forme")
    hippo_std = std("biais_hippodrome")
    nb_partants_std = std("nb_partants_course")
    ecart_corde_std = std("ecart_corde")
    age_std = std("age")
    taux_victoire_std = std("taux_victoire_carriere")
    interaction = sf_std * driver_std

    contributions = {
        "vitesse_recente": coefs.get("sf_std", 0.0) * sf_std,
        "cote_marche": coefs.get("log_cote", 0.0) * valeurs_brutes["log_cote"],
        "driver": coefs.get("driver_std", 0.0) * driver_std,
        "hippodrome": coefs.get("hippo_std", 0.0) * hippo_std,
        "interaction_cheval_driver": coefs.get("interaction_sf_driver", 0.0) * interaction,
        "nb_partants": coefs.get("nb_partants_std", 0.0) * nb_partants_std,
        "corde": coefs.get("ecart_corde_std", 0.0) * ecart_corde_std,
        "deferrage": coefs.get("deferre_4_pieds", 0.0) * valeurs_brutes["deferre_4_pieds"],
        "age": coefs.get("age_std", 0.0) * age_std,
        "sexe_femelle": coefs.get("indicateur_femelle", 0.0) * valeurs_brutes["indicateur_femelle"],
        "taux_victoire_carriere": coefs.get("taux_victoire_std", 0.0) * taux_victoire_std,
    }
    z = coefs.get("const", 0.0) + sum(contributions.values())
    return sigmoid(z), contributions


def calculer_proba_v110_A(valeurs_brutes, modele_A):
    """Version simple (sans contributions) du modele v1.10 SIGMOID
    standard a 11 variables - sert a generer les probas preliminaires
    necessaires au calcul de l'entropie (chemin SIGMOID, reference
    uniquement)."""
    coefs = modele_A["coefficients"]
    norm = modele_A["normalisation"]

    requis = ["speed_figure_avant_course", "driver_forme", "biais_hippodrome",
              "nb_partants_course", "ecart_corde", "age", "taux_victoire_carriere"]
    for var in requis:
        if var not in valeurs_brutes or valeurs_brutes[var] is None:
            return None

    def std(var):
        m = norm[var]["moyenne"]
        s = norm[var]["ecart_type"]
        return (valeurs_brutes[var] - m) / s

    sf_std = std("speed_figure_avant_course")
    driver_std = std("driver_forme")
    hippo_std = std("biais_hippodrome")
    nb_partants_std = std("nb_partants_course")
    ecart_corde_std = std("ecart_corde")
    age_std = std("age")
    taux_victoire_std = std("taux_victoire_carriere")
    interaction = sf_std * driver_std

    z = coefs.get("const", 0.0)
    z += coefs.get("sf_std", 0.0) * sf_std
    z += coefs.get("log_cote", 0.0) * valeurs_brutes["log_cote"]
    z += coefs.get("driver_std", 0.0) * driver_std
    z += coefs.get("hippo_std", 0.0) * hippo_std
    z += coefs.get("interaction_sf_driver", 0.0) * interaction
    z += coefs.get("nb_partants_std", 0.0) * nb_partants_std
    z += coefs.get("ecart_corde_std", 0.0) * ecart_corde_std
    z += coefs.get("deferre_4_pieds", 0.0) * valeurs_brutes["deferre_4_pieds"]
    z += coefs.get("age_std", 0.0) * age_std
    z += coefs.get("indicateur_femelle", 0.0) * valeurs_brutes["indicateur_femelle"]
    z += coefs.get("taux_victoire_std", 0.0) * taux_victoire_std

    return sigmoid(z)


def calculer_entropie_course(probas):
    """Entropie de Shannon de la distribution des probas (chemin
    SIGMOID, reference uniquement). Retourne None si vide/somme nulle."""
    if not probas:
        return None
    somme = sum(probas)
    if somme <= 0:
        return None
    probas_norm = [p / somme for p in probas]
    return -sum(p * math.log(p) for p in probas_norm if p > 0)


def calculer_proba_v110_avec_entropie_et_contributions(valeurs_brutes, modele):
    """Modele v1.10 SIGMOID (REFERENCE UNIQUEMENT depuis le 19 sept,
    ne mise plus reellement) - 12 variables (11 standard + entropie).
    Le VRAI v1.10 (mise reelle) utilise desormais
    calculer_probas_conditionnel_course, un logit conditionnel
    (softmax intra-course) valide comme superieur sur toutes les
    dimensions (Brier/log-loss, ROI, croissance, drawdown)."""
    coefs = modele["coefficients"]
    norm = modele["normalisation"]

    requis = ["speed_figure_avant_course", "driver_forme", "biais_hippodrome",
              "nb_partants_course", "ecart_corde", "age", "taux_victoire_carriere", "entropie"]
    for var in requis:
        if var not in valeurs_brutes or valeurs_brutes[var] is None:
            return None, {}

    def std(var):
        m = norm[var]["moyenne"]
        s = norm[var]["ecart_type"]
        return (valeurs_brutes[var] - m) / s

    sf_std = std("speed_figure_avant_course")
    driver_std = std("driver_forme")
    hippo_std = std("biais_hippodrome")
    nb_partants_std = std("nb_partants_course")
    ecart_corde_std = std("ecart_corde")
    age_std = std("age")
    taux_victoire_std = std("taux_victoire_carriere")
    entropie_std = std("entropie")
    interaction = sf_std * driver_std

    contributions = {
        "vitesse_recente": coefs.get("sf_std", 0.0) * sf_std,
        "cote_marche": coefs.get("log_cote", 0.0) * valeurs_brutes["log_cote"],
        "driver": coefs.get("driver_std", 0.0) * driver_std,
        "hippodrome": coefs.get("hippo_std", 0.0) * hippo_std,
        "interaction_cheval_driver": coefs.get("interaction_sf_driver", 0.0) * interaction,
        "nb_partants": coefs.get("nb_partants_std", 0.0) * nb_partants_std,
        "corde": coefs.get("ecart_corde_std", 0.0) * ecart_corde_std,
        "deferrage": coefs.get("deferre_4_pieds", 0.0) * valeurs_brutes["deferre_4_pieds"],
        "age": coefs.get("age_std", 0.0) * age_std,
        "sexe_femelle": coefs.get("indicateur_femelle", 0.0) * valeurs_brutes["indicateur_femelle"],
        "taux_victoire_carriere": coefs.get("taux_victoire_std", 0.0) * taux_victoire_std,
        "entropie_course": coefs.get("entropie_std", 0.0) * entropie_std,
    }
    z = coefs.get("const", 0.0) + sum(contributions.values())
    return sigmoid(z), contributions


def calculer_probas_conditionnel_course(liste_valeurs_par_cheval, modele):
    """NOUVEAU (19 sept) : LOGIT CONDITIONNEL (softmax intra-course,
    McFadden/Benter) - LE VRAI v1.10 DE PRODUCTION (mise reelle)
    depuis le 19 sept. Prend TOUS les chevaux d'une course EN UNE
    FOIS (pas un a la fois comme les autres fonctions de ce fichier),
    calcule un score lineaire par cheval, puis normalise par softmax
    SUR L'ENSEMBLE DES CHEVAUX DE LA COURSE - garantit que les
    probabilites somment exactement a 1 dans la course, contrairement
    a un sigmoid independant.

    liste_valeurs_par_cheval : liste de tuples (identifiant,
    valeurs_brutes_dict) - un par cheval de la course. identifiant
    peut etre n'importe quoi (nom du cheval, numero PMU...), reutilise
    tel quel dans le resultat.

    modele : dict avec "coefficients" (9 variables, AUCUNE constante -
    s'annule sous softmax par construction) et "normalisation".

    Retourne une liste de tuples (identifiant, proba), softmax-
    normalisee sur les chevaux VALIDES de la course (ceux avec
    toutes les variables requises disponibles - les chevaux
    incomplets sont simplement exclus du calcul, pas d'erreur).
    Retourne une liste vide si aucun cheval n'est valide."""
    coefs = modele["coefficients"]
    norm = modele["normalisation"]
    requis = ["speed_figure_avant_course", "driver_forme", "ecart_corde", "age", "taux_victoire_carriere"]

    def std(var, valeurs_brutes):
        m = norm[var]["moyenne"]
        s = norm[var]["ecart_type"]
        return (valeurs_brutes[var] - m) / s

    scores = []
    identifiants_valides = []
    for identifiant, valeurs_brutes in liste_valeurs_par_cheval:
        if any(valeurs_brutes.get(var) is None for var in requis) or valeurs_brutes.get("log_cote") is None:
            continue

        sf_std = std("speed_figure_avant_course", valeurs_brutes)
        driver_std = std("driver_forme", valeurs_brutes)
        ecart_corde_std = std("ecart_corde", valeurs_brutes)
        age_std = std("age", valeurs_brutes)
        taux_victoire_std = std("taux_victoire_carriere", valeurs_brutes)
        interaction = sf_std * driver_std

        z = coefs.get("sf_std", 0.0) * sf_std
        z += coefs.get("log_cote", 0.0) * valeurs_brutes["log_cote"]
        z += coefs.get("driver_std", 0.0) * driver_std
        z += coefs.get("interaction_sf_driver", 0.0) * interaction
        z += coefs.get("ecart_corde_std", 0.0) * ecart_corde_std
        z += coefs.get("deferre_4_pieds", 0.0) * valeurs_brutes["deferre_4_pieds"]
        z += coefs.get("age_std", 0.0) * age_std
        z += coefs.get("indicateur_femelle", 0.0) * valeurs_brutes["indicateur_femelle"]
        z += coefs.get("taux_victoire_std", 0.0) * taux_victoire_std

        scores.append(z)
        identifiants_valides.append(identifiant)

    if not scores:
        return []

    scores_array = np.array(scores)
    scores_stables = scores_array - scores_array.max()
    exp_scores = np.exp(scores_stables)
    probas = exp_scores / exp_scores.sum()

    return list(zip(identifiants_valides, probas.tolist()))


def formater_contributions(contributions, top_n=3):
    if not contributions:
        return ""
    tries = sorted(contributions.items(), key=lambda x: abs(x[1]), reverse=True)[:top_n]
    parties = [f"{nom}:{val:+.2f}" for nom, val in tries]
    return " (" + ", ".join(parties) + ")"
