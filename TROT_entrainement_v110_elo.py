"""
=============================================================================
ENTRAINEMENT DE PRODUCTION - LOGIT CONDITIONNEL AVEC ELO DRIVER
(remplace driver_forme [fenetre glissante 100 courses] par un rating Elo
[trot uniquement, multi-joueurs par course] comme source de driver_std)

Walk-forward valide le 19 sept : 14/16 Brier (88%), 15/16 log-loss (94%),
13/16 ROI (81%). Remplacement direct decide par l'utilisateur (pas de
double logging - changement de source d'UNE variable, pas d'architecture).

CE SCRIPT PRODUIT 2 FICHIERS A COPIER VERS LA RACINE DU DEPOT GITHUB :
  1. modele_v110_conditionnel_production.json - coefficients reentraines
     sur tout l'historique. MEMES CLES DE COEFFICIENTS que le modele
     actuel (driver_std, interaction_sf_driver) pour ne rien casser cote
     schema - seule la SOURCE de driver_std change (Elo au lieu de
     driver_forme). Cle de normalisation "elo_driver_avant" (nouvelle,
     remplace "driver_forme").
  2. etat_elo_drivers.json - ETAT INITIAL (bootstrap) des ratings Elo de
     TOUS les drivers actifs, calcule sur tout l'historique disponible.
     A partir du prochain deploiement, ce fichier doit etre MIS A JOUR
     par verifier_resultats.py apres chaque course (voir note dans le
     fichier), puis LU par verifier_a_venir.py pour la prediction - ces
     2 scripts restent a adapter separement (fichiers de production non
     disponibles dans cette session pour patch direct).
=============================================================================
"""

from google.colab import drive
try:
    drive.mount('/content/drive')
except Exception:
    pass

import pandas as pd
import numpy as np
from scipy.optimize import minimize
import json

DOSSIER = "/content/drive/MyDrive/pmu_data"

# ============================================================================
# 1. RECONSTRUCTION DU DATASET
# ============================================================================

sf = pd.read_csv(f"{DOSSIER}/speed_figures_trot.csv", dtype={"date": str, "num_reunion": str, "num_course": str, "num_pmu": "Int64"})
entries_v2 = pd.read_csv(f"{DOSSIER}/entries_v2.csv", dtype={"date": str, "num_reunion": str, "num_course": str, "num_pmu": "Int64"})
races_v2 = pd.read_csv(f"{DOSSIER}/races_v2.csv", dtype={"date": str, "num_reunion": str, "num_course": str})

entries_v2["deferre_4_pieds"] = (entries_v2["deferre"] == "DEFERRE_ANTERIEURS_POSTERIEURS").astype(int)
entries_v2["indicateur_femelle"] = (entries_v2["sexe"] == "FEMELLES").astype(int)
entries_v2["taux_victoire_carriere"] = np.where(entries_v2["nombre_courses_carriere"] > 0, entries_v2["nombre_victoires_carriere"] / entries_v2["nombre_courses_carriere"], np.nan)

sf = sf.drop(columns=["corde", "distance"], errors="ignore")
sf = sf.merge(races_v2[["date", "num_reunion", "num_course", "corde", "distance"]], on=["date", "num_reunion", "num_course"], how="left")

sf = sf.merge(
    entries_v2[["date", "num_reunion", "num_course", "num_pmu", "rang_arrivee", "cote_directe",
                "deferre_4_pieds", "age", "indicateur_femelle", "taux_victoire_carriere"]],
    on=["date", "num_reunion", "num_course", "num_pmu"], how="left")
sf["date_dt"] = pd.to_datetime(sf["date"], format="%d%m%Y", errors="coerce")
sf = sf.sort_values(["nom_cheval", "date_dt"]).reset_index(drop=True)
sf["speed_figure_avant_course"] = sf.groupby("nom_cheval")["speed_figure_brut"].transform(lambda s: s.shift(1).rolling(3, min_periods=1).mean())

sf = sf.sort_values(["nom_cheval", "corde", "date_dt"]).reset_index(drop=True)
sf["sf_corde_specifique"] = sf.groupby(["nom_cheval", "corde"])["speed_figure_brut"].transform(lambda s: s.shift(1).rolling(5, min_periods=2).mean())
sf["ecart_corde"] = (sf["sf_corde_specifique"] - sf["speed_figure_avant_course"]).fillna(0)

sf = sf.sort_values("date_dt").reset_index(drop=True)
sf["race_id"] = sf["date"] + "_" + sf["num_reunion"] + "_" + sf["num_course"]
sf["nb_partants_course"] = sf.groupby("race_id")["num_pmu"].transform("count").astype("int64")

# ============================================================================
# 2. ELO DRIVER (trot uniquement, multi-joueurs par course, causal)
#    -> produit aussi l'etat final (bootstrap) pour la production
# ============================================================================

print(f"\n{'='*78}\nCALCUL ELO DRIVER (TROT UNIQUEMENT)\n{'='*78}")
races_trot = races_v2[races_v2["discipline"].isin(["ATTELE", "MONTE"])][["date", "num_reunion", "num_course"]].drop_duplicates()
elo_source = entries_v2.merge(races_trot, on=["date", "num_reunion", "num_course"], how="inner")
elo_source = elo_source.dropna(subset=["driver_jockey", "rang_arrivee"])
elo_source = elo_source[elo_source["rang_arrivee"] > 0]
elo_source["date_dt"] = pd.to_datetime(elo_source["date"], format="%d%m%Y", errors="coerce")
elo_source = elo_source.dropna(subset=["date_dt"])
elo_source["race_id"] = elo_source["date"] + "_" + elo_source["num_reunion"] + "_" + elo_source["num_course"]
elo_source = elo_source.sort_values(["date_dt", "num_reunion", "num_course"]).reset_index(drop=True)
print(f"n entrees trot (elo) : {len(elo_source)} sur {elo_source['race_id'].nunique()} courses")
derniere_date_elo = elo_source["date_dt"].max()

ratings = {}
games_played = {}
elo_avant = np.full(len(elo_source), 1500.0)

for race_id, groupe in elo_source.groupby("race_id", sort=False):
    idx = groupe.index.values
    drivers_course = groupe["driver_jockey"].values
    rangs = groupe["rang_arrivee"].values.astype(float)
    n = len(drivers_course)
    R = np.array([ratings.get(d, 1500.0) for d in drivers_course])
    elo_avant[idx] = R
    if n < 2:
        continue
    K = np.array([32.0 if games_played.get(d, 0) < 30 else 16.0 for d in drivers_course])

    Ri = R[:, None]
    Rj = R[None, :]
    expected = 1.0 / (1.0 + 10 ** ((Rj - Ri) / 400.0))
    actual = (rangs[:, None] < rangs[None, :]).astype(float) + 0.5 * (rangs[:, None] == rangs[None, :]).astype(float)
    np.fill_diagonal(expected, 0.0)
    np.fill_diagonal(actual, 0.0)
    delta = K * (actual.sum(axis=1) - expected.sum(axis=1)) / (n - 1)

    for i, d in enumerate(drivers_course):
        ratings[d] = R[i] + delta[i]
        games_played[d] = games_played.get(d, 0) + 1

elo_source["elo_driver_avant"] = elo_avant
print(f"Ratings Elo finaux calcules pour {len(ratings)} drivers (au {derniere_date_elo.date()}).")

# --- Bootstrap production : uniquement les drivers actifs recemment (365j) ---
date_limite_actif = derniere_date_elo - pd.Timedelta(days=365)
derniere_course_par_driver = elo_source.groupby("driver_jockey")["date_dt"].max()
drivers_actifs = set(derniere_course_par_driver[derniere_course_par_driver >= date_limite_actif].index)
etat_elo_bootstrap = {
    "ratings": {d: round(float(r), 2) for d, r in ratings.items() if d in drivers_actifs},
    "games_played": {d: int(games_played[d]) for d in drivers_actifs},
    "date_bootstrap": derniere_date_elo.strftime("%Y-%m-%d"),
    "default_rating": 1500.0,
    "note": ("Etat initial des ratings Elo (trot uniquement), calcule sur tout "
             "l'historique disponible. Limite aux drivers ayant couru dans les "
             "365 jours precedant le bootstrap (evite de trainer des milliers "
             "de drivers retraites/inactifs). Un driver absent de ce fichier "
             "demarre a default_rating (1500). A METTRE A JOUR par "
             "verifier_resultats.py apres chaque course resolue (meme formule "
             "que ce script : Elo multi-joueurs, K=32 si <30 courses sinon "
             "K=16), puis a LIRE par verifier_a_venir.py pour la prediction."),
}
with open(f"{DOSSIER}/etat_elo_drivers.json", "w") as f:
    json.dump(etat_elo_bootstrap, f, indent=2)
print(f"Bootstrap exporte : etat_elo_drivers.json ({len(etat_elo_bootstrap['ratings'])} drivers actifs sur {len(ratings)} au total)")

sf = sf.merge(elo_source[["date", "num_reunion", "num_course", "num_pmu", "elo_driver_avant"]],
              on=["date", "num_reunion", "num_course", "num_pmu"], how="left")

# ============================================================================
# 3. FINALISATION DU DATASET + STANDARDISATION
# ============================================================================

df = sf.dropna(subset=["speed_figure_avant_course", "cote_directe", "rang_arrivee",
                        "elo_driver_avant", "ecart_corde", "deferre_4_pieds", "age", "indicateur_femelle",
                        "taux_victoire_carriere", "date_dt"]).copy()
df = df[df["cote_directe"] > 1]
df["gagnant"] = (df["rang_arrivee"] == 1).astype(int)
df["log_cote"] = np.log(df["cote_directe"])
df = df.sort_values("date_dt").reset_index(drop=True)

nb_gagnants_par_course = df.groupby("race_id")["gagnant"].transform("sum")
df = df[nb_gagnants_par_course == 1].copy()
print(f"\nn disponible (courses avec exactement 1 gagnant) : {len(df)}")

VARIABLES = ["sf_std", "log_cote", "driver_std", "interaction_sf_driver", "ecart_corde_std",
             "deferre_4_pieds", "age_std", "indicateur_femelle", "taux_victoire_std"]
COLS_STD = [("speed_figure_avant_course", "sf_std"), ("elo_driver_avant", "driver_std"),
            ("ecart_corde", "ecart_corde_std"), ("age", "age_std"),
            ("taux_victoire_carriere", "taux_victoire_std")]

normalisation = {}
for col, std_col in COLS_STD:
    m, s = df[col].mean(), df[col].std()
    normalisation[col] = (m, s)
    df[std_col] = (df[col] - m) / s
df["interaction_sf_driver"] = df["sf_std"] * df["driver_std"]

# ============================================================================
# 4. ESTIMATION MLE CONDITIONNELLE SUR TOUT L'HISTORIQUE
# ============================================================================

def neg_log_vraisemblance_et_gradient(beta, X, gagnant, groupe_idx, n_groupes):
    z = X @ beta
    z_max_par_groupe = np.full(n_groupes, -np.inf)
    np.maximum.at(z_max_par_groupe, groupe_idx, z)
    z_centre = z - z_max_par_groupe[groupe_idx]
    exp_z = np.exp(z_centre)
    somme_exp_par_groupe = np.zeros(n_groupes)
    np.add.at(somme_exp_par_groupe, groupe_idx, exp_z)
    logsumexp_par_groupe = z_max_par_groupe + np.log(somme_exp_par_groupe)
    ll = np.sum(z[gagnant == 1]) - np.sum(logsumexp_par_groupe)
    softmax_i = exp_z / somme_exp_par_groupe[groupe_idx]
    gradient = X[gagnant == 1].sum(axis=0) - (X * softmax_i[:, None]).sum(axis=0)
    return -ll, -gradient

print(f"\n{'='*78}\nENTRAINEMENT LOGIT CONDITIONNEL (9 variables, driver_std = Elo)\n{'='*78}")
codes, uniques = pd.factorize(df["race_id"])
n_groupes = len(uniques)
X = df[VARIABLES].values.astype(float)
gagnant = df["gagnant"].values
beta_init = np.zeros(X.shape[1])

resultat = minimize(
    neg_log_vraisemblance_et_gradient, beta_init,
    args=(X, gagnant, codes, n_groupes),
    jac=True, method="L-BFGS-B",
    options={"maxiter": 500},
)
print(f"Convergence : {resultat.success}, message : {resultat.message}")
beta_final = resultat.x

for var, coef in zip(VARIABLES, beta_final):
    print(f"  {var:<25} : {coef:+.5f}")

# ============================================================================
# 5. EXPORT DU MODELE (memes cles que la version actuelle - seule la source
#    de driver_std change, rien a modifier cote schema)
# ============================================================================

export = {
    "coefficients": {var: float(coef) for var, coef in zip(VARIABLES, beta_final)},
    "normalisation": {
        "speed_figure_avant_course": {"moyenne": float(normalisation["speed_figure_avant_course"][0]), "ecart_type": float(normalisation["speed_figure_avant_course"][1])},
        "elo_driver_avant": {"moyenne": float(normalisation["elo_driver_avant"][0]), "ecart_type": float(normalisation["elo_driver_avant"][1])},
        "ecart_corde": {"moyenne": float(normalisation["ecart_corde"][0]), "ecart_type": float(normalisation["ecart_corde"][1])},
        "age": {"moyenne": float(normalisation["age"][0]), "ecart_type": float(normalisation["age"][1])},
        "taux_victoire_carriere": {"moyenne": float(normalisation["taux_victoire_carriere"][0]), "ecart_type": float(normalisation["taux_victoire_carriere"][1])},
    },
    "nb_lignes_entrainement": len(df),
    "nb_courses_entrainement": n_groupes,
    "date_export": pd.Timestamp.now().strftime("%Y-%m-%d"),
    "type_modele": "logit_conditionnel_softmax_intracourse",
    "note": ("AUCUNE constante (s'annule sous softmax par construction). "
             "driver_std est desormais standardise a partir du rating ELO du "
             "driver (trot uniquement, multi-joueurs, cf etat_elo_drivers.json) "
             "et NON PLUS de driver_forme (fenetre glissante 100 courses). Cle "
             "de normalisation renommee speed_figure_avant_course/elo_driver_avant "
             "en consequence - le reste du schema (coefficients, "
             "interaction_sf_driver) est inchange. Walk-forward de validation "
             "(19 sept) : Elo bat driver_forme sur 14/16 Brier, 15/16 log-loss, "
             "13/16 ROI."),
}
with open(f"{DOSSIER}/modele_v110_conditionnel_production.json", "w") as f:
    json.dump(export, f, indent=2)
print(f"\nExporte : modele_v110_conditionnel_production.json")
print(f"Exporte : etat_elo_drivers.json")
print("Les 2 fichiers sont a copier vers la racine du depot GitHub.")
print("\nRESTE A FAIRE (fichiers de production non disponibles dans cette")
print("session) : adapter commun.py (lecture etat_elo_drivers.json + fonction")
print("de mise a jour Elo), verifier_a_venir.py (utiliser l'Elo pour la")
print("prediction au lieu de driver_forme), verifier_resultats.py (mettre a")
print("jour et sauvegarder les ratings Elo apres chaque course resolue).")
