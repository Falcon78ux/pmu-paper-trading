"""
=============================================================================
COLLECTE MICRO-CAPS - ETAPE 1/3 : IDENTIFICATION DE L'UNIVERS
=============================================================================
Utilise le Screener EODHD pour identifier les actions US (NYSE/NASDAQ)
sous un seuil de capitalisation boursiere donne (micro-cap standard :
<300M$). Sauvegarde la liste des tickers pour l'etape 2 (telechargement
de l'historique de prix).

A LANCER APRES avoir obtenu une cle API EODHD sur https://eodhd.com,
plan "EOD+Intraday - All World Extended" (29.99EUR/mois) - PAS le plan
"All World" seul (19.99EUR/mois), qui n'inclut pas l'API Screener
utilisee ici (verifie sur la doc officielle EODHD).

IMPORTANT (verifie sur la doc officielle) : le Screener plafonne a 1000
resultats par requete (offset max 999). La recherche est donc decoupee
en sous-bandes de capitalisation (BANDES_MARKET_CAP) plutot qu'un seul
appel 10M$-300M$ par exchange, pour eviter une troncature silencieuse
de l'univers. Un avertissement s'affiche si une bande approche quand
meme la limite (a redecouper plus finement dans ce cas).
=============================================================================
"""

from google.colab import drive
try:
    drive.mount('/content/drive')
except Exception:
    pass

import requests
import pandas as pd
import time

# ============================================================
# A REMPLIR : colle ta cle API EODHD ici avant de lancer
# ============================================================
API_TOKEN = "COLLE_TA_CLE_ICI"
# ============================================================

DOSSIER = "/content/drive/MyDrive/microcap_data"  # dossier dedie, separe du projet PMU trot
SEUIL_MARKET_CAP_MIN = 10_000_000    # 10M$ = exclut les nano-caps trop illiquides/risquees
SEUIL_MARKET_CAP_MAX = 300_000_000   # 300M$ = seuil standard micro-cap
BANDES_MARKET_CAP = [10_000_000, 40_000_000, 80_000_000, 130_000_000, 190_000_000, 300_000_000]

if API_TOKEN == "COLLE_TA_CLE_ICI":
    print("ERREUR : remplace API_TOKEN par ta vraie cle EODHD avant de lancer.")
else:
    tous_les_tickers = []
    bandes_a_surveiller = []

    for exchange in ["NYSE", "NASDAQ"]:
        for i in range(len(BANDES_MARKET_CAP) - 1):
            cap_min, cap_max = BANDES_MARKET_CAP[i], BANDES_MARKET_CAP[i + 1]
            print(f"\nRecherche {exchange}, capitalisation {cap_min:,}$-{cap_max:,}$...")
            offset = 0
            limite_par_appel = 100
            while True:
                url = (
                    f"https://eodhd.com/api/screener"
                    f"?api_token={API_TOKEN}"
                    f"&sort=market_capitalization.asc"
                    f"&filters=[[\"exchange\",\"=\",\"{exchange}\"],"
                    f"[\"market_capitalization\",\">\",{cap_min}],"
                    f"[\"market_capitalization\",\"<\",{cap_max}]]"
                    f"&limit={limite_par_appel}&offset={offset}"
                )
                r = requests.get(url, timeout=30)
                if r.status_code != 200:
                    print(f"  Erreur {r.status_code} : {r.text[:200]}")
                    break
                data = r.json()
                resultats = data.get("data", [])
                if not resultats:
                    break
                for item in resultats:
                    tous_les_tickers.append({
                        "code": item.get("code"),
                        "exchange": exchange,
                        "nom": item.get("name"),
                        "market_cap": item.get("market_capitalization"),
                        "secteur": item.get("sector"),
                        "industrie": item.get("industry"),
                    })
                print(f"  {offset + len(resultats)} tickers recuperes dans cette bande...")
                if offset >= 900 and len(resultats) == limite_par_appel:
                    bandes_a_surveiller.append((exchange, cap_min, cap_max))
                    print(f"  ATTENTION : cette bande approche la limite de pagination (offset={offset}) "
                          f"- troncature possible, affiner la bande si besoin.")
                offset += limite_par_appel
                time.sleep(0.3)
                if len(resultats) < limite_par_appel:
                    break

    df_univers = pd.DataFrame(tous_les_tickers).drop_duplicates(subset="code").reset_index(drop=True)
    print(f"\n{'=' * 60}")
    print(f"UNIVERS MICRO-CAP IDENTIFIE : {len(df_univers)} tickers")
    print(f"(entre {SEUIL_MARKET_CAP_MIN:,}$ et {SEUIL_MARKET_CAP_MAX:,}$ de capitalisation)")
    print(f"{'=' * 60}")
    print(df_univers.head(20))

    if bandes_a_surveiller:
        print(f"\n{len(bandes_a_surveiller)} bande(s) potentiellement tronquee(s) : {bandes_a_surveiller}")
        print("Si important, redecouper BANDES_MARKET_CAP plus finement sur ces zones et relancer.")

    chemin_sortie = f"{DOSSIER}/microcap_univers.csv"
    df_univers.to_csv(chemin_sortie, index=False)
    print(f"\nUnivers sauvegarde dans : {chemin_sortie}")
    print("Pret pour l'etape 2 (telechargement de l'historique de prix).")
