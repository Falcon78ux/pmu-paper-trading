# -----------------------------------------------------------------------
# ETAT "DERNIER RANG D'ARRIVEE" (nouveau, pour la strategie 2e favori)
# -----------------------------------------------------------------------

def get_dernier_rang(etat_dernier_rang, cheval):
    return etat_dernier_rang.get(cheval)


def maj_dernier_rang(etat_dernier_rang, cheval, rang_arrivee):
    etat_dernier_rang[cheval] = rang_arrivee


# -----------------------------------------------------------------------
# MISE KELLY STRATEGIE 2E FAVORI (Kelly standard, meme structure que v14/v15)
# -----------------------------------------------------------------------

def calculer_mise_2favori(proba, cote, bankroll):
    b = cote - 1
    if b <= 0:
        return 0.0
    kelly_full = max(0.0, (proba * b - (1 - proba)) / b)
    kelly_fraction = kelly_full * FRACTION_KELLY
    mise = min(kelly_fraction * bankroll, PLAFOND_MISE)
    if mise < MISE_MINIMUM:
        return 0.0
    return round(mise, 2)
