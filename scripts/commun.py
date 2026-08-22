"""
=============================================================================
FONCTIONS COMMUNES - utilisees par verifier_a_venir.py et verifier_resultats.py
=============================================================================
CORRECTION MAJEURE (21 aout, soir) : les mises doivent etre des
MONTANTS ENTIERS EN EUROS (pas de centimes), minimum 1EUR - confirme
par l'utilisateur en testant directement sur l'application PMU reelle
(un Simple Gagnant a 4.82EUR ou 9.57EUR n'est pas plaçable). Toutes
les fonctions de calcul de mise Kelly arrondissent desormais a l'euro
le plus proche et rejettent le pari si le resultat est inferieur a
1EUR. AVERTISSEMENT : tous les backtests anterieurs a cette correction
supposaient des mises decimales continues - a re-verifier.
=============================================================================
"""

import json
import math
import os
import requests


def charger_json(chemin, defaut=None):
    if os.path.exists(chemin):
        with open(chemin, "r", encoding="utf-8") as f:
            return json.load(f)
    return defaut if defaut is not None else {}


def sauvegarder_json(chemin, data):
    with open(chemin, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)


def envoyer_telegram(message):
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        print("ATTENTION : TELEGRAM_BOT_TOKEN ou TELEGRAM_CHAT_ID manquant, message non envoye.")
        print(message)
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        r = requests.post(url, data={"chat_id": chat_id, "text": message, "parse_mode": "HTML"}, timeout=15)
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


def extraire_cote_directe(participant):
    rapport = participant.get("dernierRapportDirect")
    if rapport and rapport.get("typePari") == "SIMPLE_GAGNANT":
        return rapport.get("rapport")
    return None


def extraire_deferre_4_pieds(participant):
    return 1 if participant.get("deferre") == "DEFERRE_ANTERIEURS_POSTERIEURS" else 0


FRACTION_KELLY = 0.10
FRACTION_KELLY_D4 = 0.05
MISE_MINIMUM = 1.0  # CORRIGE (21 aout) : 1EUR confirme sur l'app reelle, pas 1.50EUR
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
    """CORRECTION (21 aout) : le PMU n'accepte que des montants entiers
    en euros, minimum 1EUR - confirme par test direct sur l'application
    reelle. Arrondit a l'euro le plus proche, rejette (retourne 0.0) si
    le resultat est inferieur a 1EUR."""
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


def calculer_mise_place():
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
    sauvegarder_json(chemin, {"bankroll": round(nouvelle_bankroll, 2)})


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


def formater_contributions(contributions, top_n=3):
    if not contributions:
        return ""
    tries = sorted(contributions.items(), key=lambda x: abs(x[1]), reverse=True)[:top_n]
    parties = [f"{nom}:{val:+.2f}" for nom, val in tries]
    return " (" + ", ".join(parties) + ")"
