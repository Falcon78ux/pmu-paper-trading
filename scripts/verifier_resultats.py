"""
=============================================================================
VERIFIER_RESULTATS.PY - Compare aux resultats reels, met a jour l'historique
=============================================================================
v1.10-FAVORI ajoute : resolution standard marche gagnant, identique a
v1.10 (meme mecanique gagnant/perdant), bankroll separee.
=============================================================================
"""

import sys
import os
import csv
from datetime import datetime, timezone

import requests

sys.path.insert(0, os.path.dirname(__file__))
from commun import (
    charger_json, sauvegarder_json, envoyer_telegram,
    maj_driver, maj_hippodrome, maj_cheval, maj_cheval_corde,
    maj_dernier_rang, maj_sire_forme, charger_table_pedigree,
    get_bankroll, mettre_a_jour_bankroll,
)

RACINE = os.path.join(os.path.dirname(__file__), "..")

INCIDENTS_A_EXCLURE = {
    "DISQUALIFIE_POUR_ALLURE_IRREGULIERE", "NON_PARTANT", "DISTANCE",
    "ARRETE", "DISQUALIFIE_POTEAU_GALOP", "TOMBE", "RESTE_AU_POTEAU",
}

CHAMPS_AUDIT = [
    "race_id", "modele", "chevaux_paries", "rangs_arrivee_chevaux_paries",
    "top4_reel", "combinaison_rapport_brute", "cote_utilisee", "gain_calcule",
    "resultat", "coherence_verifiee", "detail_incoherence", "date_verif",
]


def recuperer_participants(date_str, num_reunion, num_course):
    url = (
        f"https://online.turfinfo.api.pmu.fr/rest/client/61/programme/"
        f"{date_str}/R{num_reunion}/C{num_course}/participants"
    )
    r = requests.get(url, timeout=20)
    r.raise_for_status()
    return r.json().get("participants", [])


def recuperer_rapports_definitifs(date_str, num_reunion, num_course):
    url = f"https://online.turfinfo.api.pmu.fr/rest/client/1/programme/{date_str}/R{num_reunion}/C{num_course}/rapports-definitifs"
    try:
        r = requests.get(url, timeout=20)
        if r.status_code != 200:
            return None
        return r.json()
    except Exception:
        return None


def extraire_rapports_place(data):
    resultat = {}
    for pari in data:
        if pari.get("typePari") != "SIMPLE_PLACE":
            continue
        mise_base = pari.get("miseBase", 200)
        for rapport in pari.get("rapports", []):
            try:
                num_pmu = int(rapport.get("combinaison"))
            except (TypeError, ValueError):
                continue
            dividende = rapport.get("dividendePourUneMiseDeBase")
            if dividende is not None:
                resultat[num_pmu] = dividende / mise_base
    return resultat


def extraire_cote_deux_sur_quatre(data):
    for pari in data:
        if pari.get("typePari") != "DEUX_SUR_QUATRE":
            continue
        mise_base = pari.get("miseBase", 100)
        rapports = pari.get("rapports", [])
        if rapports:
            dividende = rapports[0].get("dividendePourUneMiseDeBase")
            if dividende is not None:
                return dividende / mise_base
    return None


def extraire_trio(data):
    for pari in data:
        if pari.get("typePari") != "TRIO":
            continue
        mise_base = pari.get("miseBase", 200)
        rapports = pari.get("rapports", [])
        if not rapports:
            continue
        combinaison = rapports[0].get("combinaison", "")
        if "NP" in str(combinaison):
            return None, None, combinaison
        try:
            ensemble = frozenset(int(x) for x in str(combinaison).split("-"))
        except Exception:
            return None, None, combinaison
        dividende = rapports[0].get("dividendePourUneMiseDeBase")
        if dividende is not None:
            return ensemble, dividende / mise_base, combinaison
    return None, None, None


def extraire_cote_multi(data, type_pari):
    meilleure_cote = None
    for pari in data:
        if pari.get("typePari") != type_pari:
            continue
        mise_base = pari.get("miseBase", 300)
        for rapport in pari.get("rapports", []):
            dividende = rapport.get("dividendePourUneMiseDeBase")
            if dividende is not None:
                cote = dividende / mise_base
                if meilleure_cote is None or cote > meilleure_cote:
                    meilleure_cote = cote
    return meilleure_cote


def resultat_disponible(participants):
    return any(p.get("ordreArrivee") is not None for p in participants)


def ecrire_audit(ligne_audit):
    chemin_audit = f"{RACINE}/journal_audit.csv"
    existe = os.path.exists(chemin_audit)
    with open(chemin_audit, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CHAMPS_AUDIT)
        if not existe:
            writer.writeheader()
        writer.writerow(ligne_audit)


def main():
    courses_notifiees = charger_json(f"{RACINE}/courses_notifiees.json", {})
    etat_drivers = charger_json(f"{RACINE}/etat_drivers.json", {})
    etat_hippodromes = charger_json(f"{RACINE}/etat_hippodromes.json", {})
    etat_chevaux = charger_json(f"{RACINE}/etat_chevaux.json", {})
    etat_chevaux_corde = charger_json(f"{RACINE}/etat_chevaux_corde.json", {})
    etat_dernier_rang = charger_json(f"{RACINE}/etat_dernier_rang.json", {})
    etat_sire_forme = charger_json(f"{RACINE}/etat_sire_forme.json", {})
    etat_pause = charger_json(f"{RACINE}/etat_pause.json", {})
    table_pedigree = charger_table_pedigree(f"{RACINE}/pedigree_aplati.csv")
    constantes = charger_json(f"{RACINE}/constantes.json", {})
    offset_discipline = constantes.get("offset_discipline_monte_moins_attele_ms_km", -400)
    min_partants_sf = constantes.get("min_partants_valides_speed_figure", 6)
    plafond_ecart = constantes.get("plafond_ecart_speed_figure_ms", 8000)

    bankroll_v14, chemin_bankroll_v14 = get_bankroll(RACINE, "v14")
    bankroll_v14sire, chemin_bankroll_v14sire = get_bankroll(RACINE, "v14sire")
    bankroll_v15, chemin_bankroll_v15 = get_bankroll(RACINE, "v15")
    bankroll_v18, chemin_bankroll_v18 = get_bankroll(RACINE, "v18")
    bankroll_v110, chemin_bankroll_v110 = get_bankroll(RACINE, "v110")
    bankroll_v110favori, chemin_bankroll_v110favori = get_bankroll(RACINE, "v110favori")
    bankroll_place, chemin_bankroll_place = get_bankroll(RACINE, "place")
    bankroll_2sur4, chemin_bankroll_2sur4 = get_bankroll(RACINE, "2sur4")
    bankroll_trio, chemin_bankroll_trio = get_bankroll(RACINE, "trio")
    bankroll_multi, chemin_bankroll_multi = get_bankroll(RACINE, "multi")
    bankroll_2favori, chemin_bankroll_2favori = get_bankroll(RACINE, "2favori")

    chemin_log = f"{RACINE}/paris_virtuels.csv"
    if not os.path.exists(chemin_log):
        print("Aucun paris_virtuels.csv - rien a traiter.")
        return

    with open(chemin_log, "r", encoding="utf-8") as f:
        lignes = list(csv.DictReader(f))

    races_en_attente = sorted(set(
        l["race_id"] for l in lignes if l.get("resultat", "") == ""
    ))

    courses_traitees_ce_run = []

    for race_id in races_en_attente:
        parts = race_id.split("_")
        date_str, num_reunion, num_course = parts[0], parts[1], parts[2]

        try:
            participants = recuperer_participants(date_str, num_reunion, num_course)
        except Exception as e:
            print(f"Erreur recuperation resultat {race_id} : {e}")
            continue

        if not resultat_disponible(participants):
            continue

        infos_course = courses_notifiees.get(race_id, {})
        hippodrome_nom = infos_course.get("hippodrome") if isinstance(infos_course, dict) else None
        corde_course = infos_course.get("corde", "") if isinstance(infos_course, dict) else ""

        rang_par_nom = {
            p.get("nom"): p.get("ordreArrivee")
            for p in participants if p.get("ordreArrivee") is not None
        }
        top4_reel_noms = sorted(
            (p.get("nom") for p in participants if p.get("ordreArrivee") in (1, 2, 3, 4)),
            key=lambda nom: rang_par_nom.get(nom, 99),
        )
        top4_reel_str = "|".join(top4_reel_noms)

        valides = []
        for p in participants:
            incident = p.get("incident", "") or ""
            rk = p.get("reductionKilometrique")
            if incident in INCIDENTS_A_EXCLURE or rk is None:
                continue
            valides.append(p)

        if len(valides) >= min_partants_sf:
            rks_ajustees = []
            for p in valides:
                rk = p["reductionKilometrique"]
                if p.get("allure") == "MONTE" or (p.get("discipline") == "MONTE"):
                    rk = rk - offset_discipline
                rks_ajustees.append(rk)
            track_variant = sorted(rks_ajustees)[len(rks_ajustees) // 2]

            for p, rk_adj in zip(valides, rks_ajustees):
                sf_brut = track_variant - rk_adj
                sf_brut = max(-plafond_ecart, min(plafond_ecart, sf_brut))
                maj_cheval(etat_chevaux, p.get("nom"), sf_brut)
                if corde_course:
                    maj_cheval_corde(etat_chevaux_corde, p.get("nom"), corde_course, sf_brut)

        somme_ecart_course = 0.0
        nb_partants_course = 0
        for p in participants:
            rang = p.get("ordreArrivee")
            if rang is None:
                continue
            gagnant = 1 if rang == 1 else 0
            driver = p.get("driver") or p.get("entraineur")
            if driver:
                maj_driver(etat_drivers, driver, gagnant)

            maj_dernier_rang(etat_dernier_rang, p.get("nom"), rang)

            pere = table_pedigree.get(p.get("nom"))
            if pere:
                maj_sire_forme(etat_sire_forme, pere, float(gagnant))

            cote = None
            rapport = p.get("dernierRapportDirect")
            if rapport and rapport.get("typePari") == "SIMPLE_GAGNANT":
                cote = rapport.get("rapport")
            if cote and cote > 1:
                ecart = gagnant - (1 / cote)
                somme_ecart_course += ecart
                nb_partants_course += 1

        if nb_partants_course > 0 and hippodrome_nom:
            maj_hippodrome(etat_hippodromes, hippodrome_nom, somme_ecart_course, nb_partants_course)

        paris_combines_en_attente = {"place", "2sur4", "trio", "multi"}
        a_un_pari_combine_en_attente = any(
            l["race_id"] == race_id and l["modele"] in paris_combines_en_attente and l.get("resultat", "") == ""
            for l in lignes
        )
        rapports_data = None
        course_combine_incomplete = False
        if a_un_pari_combine_en_attente:
            rapports_data = recuperer_rapports_definitifs(date_str, num_reunion, num_course)
            if rapports_data is None:
                course_combine_incomplete = True

        rapports_place, cote_2sur4 = None, None
        trio_reel_ensemble, trio_reel_cote, trio_combinaison_brute = None, None, None
        if rapports_data is not None:
            rapports_place = extraire_rapports_place(rapports_data)
            cote_2sur4 = extraire_cote_deux_sur_quatre(rapports_data)
            trio_reel_ensemble, trio_reel_cote, trio_combinaison_brute = extraire_trio(rapports_data)

        top4_reel = frozenset(
            p.get("numPmu") for p in participants
            if p.get("ordreArrivee") is not None and p.get("ordreArrivee") <= 4
        )

        lignes_message = []
        for l in lignes:
            if l["race_id"] != race_id or l.get("resultat", "") != "":
                continue

            if l["modele"] == "2sur4":
                if course_combine_incomplete:
                    continue
                chevaux_paries = l["cheval"].split("|")
                rangs = [rang_par_nom.get(c) for c in chevaux_paries]
                a_gagne = all(r is not None and r <= 4 for r in rangs)
                mise = float(l.get("mise", 0) or 0)
                gain_euros = mise * (cote_2sur4 - 1) if a_gagne and cote_2sur4 else -mise
                l["resultat"] = "GAGNANT" if a_gagne else "PERDANT"
                l["gain_euros"] = f"{gain_euros:.2f}"
                l["cote"] = f"{cote_2sur4:.2f}" if a_gagne and cote_2sur4 else ""
                bankroll_2sur4 += gain_euros

                coherence, detail = "OK", ""
                if a_gagne and (cote_2sur4 is None or gain_euros <= 0):
                    coherence, detail = "INCOHERENT", "Gagnant mais gain <= 0 ou cote manquante"
                if not a_gagne and gain_euros != -mise:
                    coherence, detail = "INCOHERENT", "Perdant mais gain != -mise"

                ecrire_audit({
                    "race_id": race_id, "modele": "2sur4",
                    "chevaux_paries": "|".join(chevaux_paries),
                    "rangs_arrivee_chevaux_paries": "|".join(str(r) for r in rangs),
                    "top4_reel": top4_reel_str, "combinaison_rapport_brute": "",
                    "cote_utilisee": cote_2sur4 or "", "gain_calcule": f"{gain_euros:.2f}",
                    "resultat": l["resultat"], "coherence_verifiee": coherence,
                    "detail_incoherence": detail, "date_verif": datetime.now(timezone.utc).isoformat(),
                })

                if not etat_pause.get("2sur4", False):
                    emoji = "✅" if a_gagne else "❌"
                    lignes_message.append(f"{emoji} [2sur4] {' + '.join(chevaux_paries)} (mise {mise:.2f}€) — gain {gain_euros:+.2f}€ | bankroll 2sur4 : {bankroll_2sur4:.2f}€")
                continue

            if l["modele"] == "trio":
                if course_combine_incomplete:
                    continue
                chevaux_paries = l["cheval"].split("|")
                rangs = [rang_par_nom.get(c) for c in chevaux_paries]
                a_gagne_independant = set(rangs) == {1, 2, 3}

                if trio_reel_ensemble is None:
                    ecrire_audit({
                        "race_id": race_id, "modele": "trio",
                        "chevaux_paries": "|".join(chevaux_paries),
                        "rangs_arrivee_chevaux_paries": "|".join(str(r) for r in rangs),
                        "top4_reel": top4_reel_str, "combinaison_rapport_brute": trio_combinaison_brute or "NP_ou_indisponible",
                        "cote_utilisee": "", "gain_calcule": "", "resultat": "NON_RESOLU_NP",
                        "coherence_verifiee": "IGNORE", "detail_incoherence": "Rapport TRIO avec NP ou indisponible",
                        "date_verif": datetime.now(timezone.utc).isoformat(),
                    })
                    continue

                try:
                    ensemble_parie = frozenset(int(next(p.get("numPmu") for p in participants if p.get("nom") == c)) for c in chevaux_paries)
                except Exception:
                    continue
                a_gagne = ensemble_parie == trio_reel_ensemble
                mise = float(l.get("mise", 0) or 0)
                gain_euros = mise * (trio_reel_cote - 1) if a_gagne else -mise
                l["resultat"] = "GAGNANT" if a_gagne else "PERDANT"
                l["gain_euros"] = f"{gain_euros:.2f}"
                l["cote"] = f"{trio_reel_cote:.2f}" if a_gagne else ""
                bankroll_trio += gain_euros

                coherence, detail = "OK", ""
                if a_gagne != a_gagne_independant:
                    coherence = "INCOHERENT"
                    detail = f"Rapport dit {a_gagne}, verite terrain (rangs) dit {a_gagne_independant}"

                ecrire_audit({
                    "race_id": race_id, "modele": "trio",
                    "chevaux_paries": "|".join(chevaux_paries),
                    "rangs_arrivee_chevaux_paries": "|".join(str(r) for r in rangs),
                    "top4_reel": top4_reel_str, "combinaison_rapport_brute": trio_combinaison_brute or "",
                    "cote_utilisee": trio_reel_cote or "", "gain_calcule": f"{gain_euros:.2f}",
                    "resultat": l["resultat"], "coherence_verifiee": coherence,
                    "detail_incoherence": detail, "date_verif": datetime.now(timezone.utc).isoformat(),
                })

                if not etat_pause.get("trio", False):
                    emoji = "✅" if a_gagne else "❌"
                    lignes_message.append(f"{emoji} [trio] {' + '.join(chevaux_paries)} (mise {mise:.2f}€) — gain {gain_euros:+.2f}€ | bankroll trio : {bankroll_trio:.2f}€")
                continue

            if l["modele"] == "multi":
                if course_combine_incomplete:
                    continue
                type_pari_multi = l.get("cote", "MULTI")
                chevaux_paries = l["cheval"].split("|")
                rangs = [rang_par_nom.get(c) for c in chevaux_paries]
                a_gagne_independant = set(rangs) == {1, 2, 3, 4}

                try:
                    ensemble_parie = frozenset(int(next(p.get("numPmu") for p in participants if p.get("nom") == c)) for c in chevaux_paries)
                except Exception:
                    continue
                a_gagne = ensemble_parie == top4_reel
                cote_multi = extraire_cote_multi(rapports_data, type_pari_multi) if rapports_data else None
                mise = float(l.get("mise", 0) or 0)
                gain_euros = mise * (cote_multi - 1) if a_gagne and cote_multi else -mise
                l["resultat"] = "GAGNANT" if a_gagne else "PERDANT"
                l["gain_euros"] = f"{gain_euros:.2f}"
                l["cote"] = f"{cote_multi:.2f}" if a_gagne and cote_multi else ""
                bankroll_multi += gain_euros

                coherence, detail = "OK", ""
                if a_gagne != a_gagne_independant:
                    coherence = "INCOHERENT"
                    detail = f"Calcul principal dit {a_gagne}, verite terrain (rangs) dit {a_gagne_independant}"

                ecrire_audit({
                    "race_id": race_id, "modele": type_pari_multi.lower(),
                    "chevaux_paries": "|".join(chevaux_paries),
                    "rangs_arrivee_chevaux_paries": "|".join(str(r) for r in rangs),
                    "top4_reel": top4_reel_str, "combinaison_rapport_brute": "",
                    "cote_utilisee": cote_multi or "", "gain_calcule": f"{gain_euros:.2f}",
                    "resultat": l["resultat"], "coherence_verifiee": coherence,
                    "detail_incoherence": detail, "date_verif": datetime.now(timezone.utc).isoformat(),
                })

                if not etat_pause.get("multi", False):
                    emoji = "✅" if a_gagne else "❌"
                    lignes_message.append(f"{emoji} [{type_pari_multi.lower()}] {' + '.join(chevaux_paries)} (mise {mise:.2f}€) — gain {gain_euros:+.2f}€ | bankroll multi : {bankroll_multi:.2f}€")
                continue

            cheval_parie = l["cheval"]
            participant_correspondant = next((p for p in participants if p.get("nom") == cheval_parie), None)
            if participant_correspondant is None:
                continue
            rang_reel = rang_par_nom.get(cheval_parie)

            if l["modele"] == "place":
                if course_combine_incomplete:
                    continue
                num_pmu = participant_correspondant.get("numPmu")
                mise = float(l.get("mise", 0) or 0)
                cote_place_reelle = rapports_place.get(num_pmu) if rapports_place else None
                a_place = cote_place_reelle is not None
                gain_euros = mise * (cote_place_reelle - 1) if a_place else -mise
                l["resultat"] = "PLACE" if a_place else "NON_PLACE"
                l["gain_euros"] = f"{gain_euros:.2f}"
                l["cote"] = f"{cote_place_reelle:.2f}" if a_place else ""
                bankroll_place += gain_euros

                coherence, detail = "OK", ""
                if a_place and rang_reel is not None and rang_reel > 4:
                    coherence, detail = "INCOHERENT", f"Marque PLACE mais rang reel = {rang_reel} (>4, impossible)"
                elif not a_place and rang_reel is not None and rang_reel <= 2:
                    coherence, detail = "INCOHERENT", f"Marque NON_PLACE mais rang reel = {rang_reel} (<=2, toujours placable)"

                ecrire_audit({
                    "race_id": race_id, "modele": "place",
                    "chevaux_paries": cheval_parie,
                    "rangs_arrivee_chevaux_paries": str(rang_reel),
                    "top4_reel": top4_reel_str, "combinaison_rapport_brute": "",
                    "cote_utilisee": cote_place_reelle or "", "gain_calcule": f"{gain_euros:.2f}",
                    "resultat": l["resultat"], "coherence_verifiee": coherence,
                    "detail_incoherence": detail, "date_verif": datetime.now(timezone.utc).isoformat(),
                })

                if not etat_pause.get("place", False):
                    emoji = "✅" if a_place else "❌"
                    lignes_message.append(f"{emoji} [place] {cheval_parie} (mise {mise:.2f}€) — gain {gain_euros:+.2f}€ | bankroll place : {bankroll_place:.2f}€")
                continue

            gagnant = rang_reel == 1
            cote = float(l["cote"])
            mise = float(l.get("mise", 0) or 0)

            gain_euros = mise * (cote - 1) if gagnant else -mise
            l["resultat"] = "GAGNANT" if gagnant else "PERDANT"
            l["gain_euros"] = f"{gain_euros:.2f}"

            if l["modele"] == "v1.4":
                bankroll_v14 += gain_euros
                bankroll_apres = bankroll_v14
                cle_pause = "v14"
            elif l["modele"] == "v14sire":
                bankroll_v14sire += gain_euros
                bankroll_apres = bankroll_v14sire
                cle_pause = "v14sire"
            elif l["modele"] == "v1.5":
                bankroll_v15 += gain_euros
                bankroll_apres = bankroll_v15
                cle_pause = "v15"
            elif l["modele"] == "v1.8":
                bankroll_v18 += gain_euros
                bankroll_apres = bankroll_v18
                cle_pause = "v18"
            elif l["modele"] == "v1.10":
                bankroll_v110 += gain_euros
                bankroll_apres = bankroll_v110
                cle_pause = "v110"
            elif l["modele"] == "v110favori":
                bankroll_v110favori += gain_euros
                bankroll_apres = bankroll_v110favori
                cle_pause = "v110favori"
            elif l["modele"] == "2favori":
                bankroll_2favori += gain_euros
                bankroll_apres = bankroll_2favori
                cle_pause = "2favori"
            else:
                bankroll_apres = None
                cle_pause = None

            coherence, detail = "OK", ""
            if gagnant and rang_reel != 1:
                coherence, detail = "INCOHERENT", "Marque gagnant mais rang reel != 1"
            if not gagnant and rang_reel == 1:
                coherence, detail = "INCOHERENT", "Marque perdant mais rang reel == 1"

            ecrire_audit({
                "race_id": race_id, "modele": l["modele"],
                "chevaux_paries": cheval_parie,
                "rangs_arrivee_chevaux_paries": str(rang_reel),
                "top4_reel": top4_reel_str, "combinaison_rapport_brute": "",
                "cote_utilisee": cote, "gain_calcule": f"{gain_euros:.2f}",
                "resultat": l["resultat"], "coherence_verifiee": coherence,
                "detail_incoherence": detail, "date_verif": datetime.now(timezone.utc).isoformat(),
            })

            if not (cle_pause and etat_pause.get(cle_pause, False)):
                emoji = "✅" if gagnant else "❌"
                lignes_message.append(
                    f"{emoji} [{l['modele']}] {cheval_parie} (cote {cote:.1f}, mise {mise:.2f}€) "
                    f"— gain {gain_euros:+.2f}€ | bankroll {l['modele']} : {bankroll_apres:.2f}€"
                )

        if lignes_message:
            msg = f"🏁 <b>Resultat course {race_id}</b>\n\n" + "\n".join(lignes_message)
            envoyer_telegram(msg)

        if not course_combine_incomplete:
            courses_traitees_ce_run.append(race_id)

    with open(chemin_log, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "race_id", "modele", "cheval", "cote", "ev", "mise",
            "date_detection", "resultat", "gain_euros",
        ])
        writer.writeheader()
        for l in lignes:
            writer.writerow(l)

    mettre_a_jour_bankroll(chemin_bankroll_v14, bankroll_v14)
    mettre_a_jour_bankroll(chemin_bankroll_v14sire, bankroll_v14sire)
    mettre_a_jour_bankroll(chemin_bankroll_v15, bankroll_v15)
    mettre_a_jour_bankroll(chemin_bankroll_v18, bankroll_v18)
    mettre_a_jour_bankroll(chemin_bankroll_v110, bankroll_v110)
    mettre_a_jour_bankroll(chemin_bankroll_v110favori, bankroll_v110favori)
    mettre_a_jour_bankroll(chemin_bankroll_place, bankroll_place)
    mettre_a_jour_bankroll(chemin_bankroll_2sur4, bankroll_2sur4)
    mettre_a_jour_bankroll(chemin_bankroll_trio, bankroll_trio)
    mettre_a_jour_bankroll(chemin_bankroll_multi, bankroll_multi)
    mettre_a_jour_bankroll(chemin_bankroll_2favori, bankroll_2favori)

    sauvegarder_json(f"{RACINE}/etat_drivers.json", etat_drivers)
    sauvegarder_json(f"{RACINE}/etat_hippodromes.json", etat_hippodromes)
    sauvegarder_json(f"{RACINE}/etat_chevaux.json", etat_chevaux)
    sauvegarder_json(f"{RACINE}/etat_chevaux_corde.json", etat_chevaux_corde)
    sauvegarder_json(f"{RACINE}/etat_dernier_rang.json", etat_dernier_rang)
    sauvegarder_json(f"{RACINE}/etat_sire_forme.json", etat_sire_forme)

    print(f"{len(courses_traitees_ce_run)} courses traitees dans ce run.")
    print(f"v1.4:{bankroll_v14:.2f} v14sire:{bankroll_v14sire:.2f} v1.5:{bankroll_v15:.2f} v1.8:{bankroll_v18:.2f} v1.10:{bankroll_v110:.2f} v110favori:{bankroll_v110favori:.2f} place:{bankroll_place:.2f} 2sur4:{bankroll_2sur4:.2f} trio:{bankroll_trio:.2f} multi:{bankroll_multi:.2f} 2favori:{bankroll_2favori:.2f}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        detail = traceback.format_exc()[-500:]
        detail_echappe = detail.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        envoyer_telegram(f"🔴 <b>Erreur dans verifier_resultats.py</b>\n\n{e}\n\n<code>{detail_echappe}</code>")
        raise
