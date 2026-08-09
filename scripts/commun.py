"""
=============================================================================
FONCTIONS COMMUNES - utilisees par verifier_a_venir.py et verifier_resultats.py
=============================================================================
"""

import json
import math
import os
import requests


# -----------------------------------------------------------------------
# CHARGEMENT / SAUVEGARDE JSON
# -----------------------------------------------------------------------

def charger_json(chemin, defaut=None):
    if os.path.exists(chemin):
        with open(chemin, "r", encoding="utf-8") as f:
            return json.load(f)
    return defaut if defaut is not None else {}


def sauvegarder_json(chemin, data):
    with open(chemin, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)


# -----------------------------------------------------------------------
# TELEGRAM
# -----------------------------------------------------------------------

def envoyer_telegram(message):
    """Envoie un message via le bot Telegram. Token/chat_id lus depuis les
    variables d'environnement (injectees par GitHub Actions depuis les Secrets)."""
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


# -----------------------------------------------------------------------
# SCORING DU MODELE v1.4/v1.5 (regression logistique - INCHANGE)
# -----------------------------------------------------------------------
# Reutilisee telle quelle pour la strategie "2e favori" (memes 4
# variables, meme structure de standardisation systematique).

def sigmoid(x):
    try:
        return 1 / (1 + math.exp(-x))
    except OverflowError:
        return 0.0 if x < 0 else 1.0


def calculer_proba(valeurs_brutes, modele):
    """
    valeurs_brutes : dict {nom_variable: valeur}, ex {"speed_figure_avant_course": 120, "log_cote": 1.5, ...}
    modele : dict charge depuis modele_v14.json, modele_v15.json, ou
             modele_deuxieme_favori_v2_production.json
    Retourne la probabilite de victoire estimee, ou None si une variable manque.
    """
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


# -----------------------------------------------------------------------
# SCORING DU MODELE v1.8 (avec interaction, nb_partants, corde, deferrage)
# -----------------------------------------------------------------------

def calculer_proba_v18(valeurs_brutes, modele_v18):
    """
    valeurs_brutes attendu : {
        "speed_figure_avant_course": ..., "log_cote": ..., "driver_forme": ...,
        "biais_hippodrome": ..., "nb_partants_course": ..., "ecart_corde": ...,
        "deferre_4_pieds": 0 ou 1,
    }
    modele_v18 : dict charge depuis modele_v18_production.json
    """
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


# -----------------------------------------------------------------------
# SCORING COMMUN v1.10 ET PLACE (memes variables, coefficients differents)
# -----------------------------------------------------------------------

def calculer_proba_v110_ou_place(valeurs_brutes, modele):
    """
    Fonction partagee par v1.10 (cible : gagnant) et le modele place
    (cible : place) - memes variables exactement, seuls les coefficients
    charges depuis le JSON different.

    valeurs_brutes attendu : {
        "speed_figure_avant_course", "log_cote", "driver_forme",
        "biais_hippodrome", "nb_partants_course", "ecart_corde",
        "deferre_4_pieds", "age", "indicateur_femelle",
        "taux_victoire_carriere",
    }
    modele : dict charge depuis modele_v110_production.json ou
             modele_place_v1_production.json
    """
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


# -----------------------------------------------------------------------
# EXTRACTION DIRECTE DEPUIS LE PARTICIPANT (pas d'etat glissant necessaire)
# -----------------------------------------------------------------------

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


# -----------------------------------------------------------------------
# GESTION DE L'ETAT GLISSANT (driver / hippodrome / cheval) - INCHANGE
# -----------------------------------------------------------------------

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
    """victoire : 1 si gagnant, 0 sinon."""
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


# -----------------------------------------------------------------------
# ETAT GLISSANT SPECIFIQUE A L'APTITUDE CORDE (v1.8)
# -----------------------------------------------------------------------

FENETRE_CORDE = 5
MIN_CORDE = 2


def get_ecart_corde(etat_chevaux_corde, etat_chevaux, cheval, corde_du_jour):
    """Renvoie l'ecart entre la performance du cheval specifiquement dans
    CE sens de rotation et sa moyenne generale. 0 si pas assez d'historique
    specifique a ce sens (valeur neutre, comme a l'entrainement)."""
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
        return  # ignore LIGNE_DROITE / valeurs manquantes - pas de sens de rotation
    donnees = etat_chevaux_corde.get(cheval, {"CORDE_GAUCHE": [], "CORDE_DROITE": []})
    historique = donnees.get(corde, [])
    historique.append(speed_figure_brut)
    donnees[corde] = historique[-FENETRE_CORDE:]
    etat_chevaux_corde[cheval] = donnees


# -----------------------------------------------------------------------
# ETAT "DERNIER RANG D'ARRIVEE" (nouveau, pour la strategie 2e favori)
# -----------------------------------------------------------------------
# Suit le dernier rang d'arrivee connu de CHAQUE cheval, tous modeles
# confondus - necessaire pour detecter "le 1er favori du jour etait
# NON PLACE (rang > 3) a sa derniere sortie".

def get_dernier_rang(etat_dernier_rang, cheval):
    return etat_dernier_rang.get(cheval)


def maj_dernier_rang(etat_dernier_rang, cheval, rang_arrivee):
    etat_dernier_rang[cheval] = rang_arrivee


# -----------------------------------------------------------------------
# EXTRACTION DE LA COTE DIRECTE DEPUIS UN PARTICIPANT (API PMU)
# -----------------------------------------------------------------------

def extraire_cote_directe(participant):
    rapport = participant.get("dernierRapportDirect")
    if rapport and rapport.get("typePari") == "SIMPLE_GAGNANT":
        return rapport.get("rapport")
    return None


def extraire_deferre_4_pieds(participant):
    return 1 if participant.get("deferre") == "DEFERRE_ANTERIEURS_POSTERIEURS" else 0


# -----------------------------------------------------------------------
# BANKROLL VIRTUELLE ET MISE KELLY (une bankroll independante par modele)
# -----------------------------------------------------------------------

FRACTION_KELLY = 0.10       # v1.4/v1.5/v1.8/v1.10/2favori (regime normal)
FRACTION_KELLY_D4 = 0.05    # v1.8/v1.10 uniquement, sur les paris D4
PLAFOND_MISE = 20           # en euros
MISE_MINIMUM = 1.50         # mise minimum reelle au PMU
BANKROLL_DEPART = 1236      # en euros
MISE_FIXE_PLACE = 10        # EUR, pour place/2sur4/trio/multi (pas de cote en direct)


def get_bankroll(racine, nom_modele):
    """Cree le fichier au depart si absent."""
    chemin = f"{racine}/bankroll_{nom_modele}.json"
    data = charger_json(chemin, {"bankroll": BANKROLL_DEPART})
    return data["bankroll"], chemin


def calculer_mise(proba, cote, bankroll):
    """Formule de Kelly fractionne (1/10), plafonnee a 20EUR. v1.4/v1.5."""
    b = cote - 1
    if b <= 0:
        return 0.0
    kelly_full = max(0.0, (proba * b - (1 - proba)) / b)
    kelly_fraction = kelly_full * FRACTION_KELLY
    mise = min(kelly_fraction * bankroll, PLAFOND_MISE)
    if mise < MISE_MINIMUM:
        return 0.0
    return round(mise, 2)


def calculer_mise_v18(proba, cote, bankroll, est_deferre_4_pieds):
    """Kelly a deux regimes pour v1.8 : fraction reduite (1/20) sur les
    paris D4, fraction normale (1/10) sinon."""
    b = cote - 1
    if b <= 0:
        return 0.0
    kelly_full = max(0.0, (proba * b - (1 - proba)) / b)
    fraction = FRACTION_KELLY_D4 if est_deferre_4_pieds else FRACTION_KELLY
    kelly_fraction = kelly_full * fraction
    mise = min(kelly_fraction * bankroll, PLAFOND_MISE)
    if mise < MISE_MINIMUM:
        return 0.0
    return round(mise, 2)


def calculer_mise_v110(proba, cote, bankroll, est_deferre_4_pieds):
    """Identique structure v1.8 - deux regimes."""
    b = cote - 1
    if b <= 0:
        return 0.0
    kelly_full = max(0.0, (proba * b - (1 - proba)) / b)
    fraction = FRACTION_KELLY_D4 if est_deferre_4_pieds else FRACTION_KELLY
    kelly_fraction = kelly_full * fraction
    mise = min(kelly_fraction * bankroll, PLAFOND_MISE)
    if mise < MISE_MINIMUM:
        return 0.0
    return round(mise, 2)


def calculer_mise_place():
    """Mise fixe - pas de cote place connue avant la course."""
    return MISE_FIXE_PLACE


def calculer_mise_2favori(proba, cote, bankroll):
    """Kelly standard (1/10) - meme structure que calculer_mise(), mais
    fonction dediee pour ne prendre aucun risque sur v1.4/v1.5 qui
    partagent calculer_proba() mais pas calculer_mise()."""
    b = cote - 1
    if b <= 0:
        return 0.0
    kelly_full = max(0.0, (proba * b - (1 - proba)) / b)
    kelly_fraction = kelly_full * FRACTION_KELLY
    mise = min(kelly_fraction * bankroll, PLAFOND_MISE)
    if mise < MISE_MINIMUM:
        return 0.0
    return round(mise, 2)


def mettre_a_jour_bankroll(chemin, nouvelle_bankroll):
    sauvegarder_json(chemin, {"bankroll": round(nouvelle_bankroll, 2)})
    # -----------------------------------------------------------------------
# VERSIONS "AVEC CONTRIBUTIONS" - meme calcul, renvoie en plus le detail
# de ce que chaque variable apporte au score (pour affichage Telegram).
# N'affecte AUCUN calcul de proba/EV/mise existant - fonctions separees.
# -----------------------------------------------------------------------

def calculer_proba_avec_contributions(valeurs_brutes, modele):
    """Version de calculer_proba() qui renvoie aussi le detail des
    contributions. Renvoie (proba, contributions) ou (None, {})."""
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
    """Version de calculer_proba_v18() avec detail des contributions."""
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
    """Version de calculer_proba_v110_ou_place() avec detail des
    contributions - partagee par v1.10 et le modele place."""
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
    """Formate les N contributions les plus fortes (en valeur absolue)
    pour affichage compact dans Telegram."""
    if not contributions:
        return ""
    tries = sorted(contributions.items(), key=lambda x: abs(x[1]), reverse=True)[:top_n]
    parties = [f"{nom}:{val:+.2f}" for nom, val in tries]
    return " (" + ", ".join(parties) + ")"
# -----------------------------------------------------------------------
# ETAT "FORME DES ETALONS" (nouveau, pour v1.4 + sire_forme genealogie)
# -----------------------------------------------------------------------
# Meme structure que get_driver_forme/maj_driver, mais avec un seuil
# minimum different (15 au lieu de 20) pour matcher exactement le
# backtest valide (MIN_FORME_GENITEUR=15 dans les scripts de test).

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
    """Charge pedigree_aplati.csv en dictionnaire {nom_cheval: pere}.
    Table STATIQUE (scrapee le 9 aout) - les chevaux debutant apres
    cette date n'auront pas de pere connu tant qu'une nouvelle collecte
    n'est pas relancee."""
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


def calculer_mise_v14sire(proba, cote, bankroll):
    """Kelly standard (1/10) - dediee, meme principe que calculer_mise_2favori."""
    b = cote - 1
    if b <= 0:
        return 0.0
    kelly_full = max(0.0, (proba * b - (1 - proba)) / b)
    kelly_fraction = kelly_full * FRACTION_KELLY
    mise = min(kelly_fraction * bankroll, PLAFOND_MISE)
    if mise < MISE_MINIMUM:
        return 0.0
    return round(mise, 2)

